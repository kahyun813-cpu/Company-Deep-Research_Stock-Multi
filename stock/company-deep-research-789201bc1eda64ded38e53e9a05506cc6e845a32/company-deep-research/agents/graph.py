import json
import math

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
# shared.config를 먼저 임포트해야 load_dotenv()가 tools/의 API 키를 로드한 뒤
# data_fetcher → tools/*.py 모듈 레벨 변수들이 올바른 값을 가져간다.
from shared.config import CHECKLIST_MODEL, RESEARCHER_MODEL, FAST_MODEL, MAX_RESEARCH_LOOPS
from shared.llm_invoke import invoke_with_backoff
from shared.normalization import extract_json
from shared.yaml_loader import get_system_prompt, get_user_prompt

from agents.data_fetcher import fetch_collected_payload
from agents.needs_resolver import resolve_needed_categories
from agents.report_sections import build_final_report
from agents.state import ResearchState


MINIMAL_CATEGORIES: list[str] = [
    # Core data fetched upfront.
    # earnings_calendar is cheap (one yfinance call) and provides pe_forward + next_earnings_date.
    # news, sec_10k fetched on-demand via needs_resolver → lazy_refetch.
    "financials",
    "valuation",
    "competitor",
    "earnings_calendar",
]


def _as_valid_number(value: object) -> float | None:
    """
    Coerce a numeric value; return None for null / non-numeric strings.
    """
    if value is None:
        return None
    # bool is a subclass of int; exclude it explicitly.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _rating_from_composite_score(composite_score: float) -> str:
    if composite_score >= 7.5:
        return "STRONG BUY"
    if composite_score >= 6.0:
        return "MODERATE BUY"
    if composite_score >= 4.5:
        return "HOLD"
    if composite_score >= 3.0:
        return "MODERATE SELL"
    return "SELL"


_VERDICT_SCORE: dict[str, float] = {
    "positive": 8.5,
    "caution": 5.0,
    "negative": 2.0,
    # "insufficient_data" is excluded from the average
}

_CATEGORY_KEY_TO_SCORE_FIELD: dict[str, str] = {
    "A_financial_health": "A_score",
    "B_valuation":        "B_score",
    "C_catalysts":        "C_score",
    "D_industry":         "D_score",
    "E_sentiment":        "E_score",
}


def _apply_category_score_fallback(checklist_output: dict) -> None:
    """
    Deterministically recompute each A-E category score from item verdicts.
    - positive → 8.5, caution → 5.0, negative → 2.0
    - insufficient_data items are excluded from the average
    - Also recomputes composite_score from valid category scores and updates investment_rating.
    """
    payload = (checklist_output or {}).get("payload") if isinstance(checklist_output, dict) else None
    if not isinstance(payload, dict):
        return

    checklist = payload.get("checklist")
    summary   = payload.get("summary")
    if not isinstance(checklist, dict) or not isinstance(summary, dict):
        return

    # Recompute each category score from item verdicts.
    recomputed: dict[str, float | None] = {}
    for cat_key, score_field in _CATEGORY_KEY_TO_SCORE_FIELD.items():
        cat = checklist.get(cat_key, {})
        items = cat.get("items") or []
        scores_for_cat: list[float] = []
        for item in items:
            verdict = (item.get("verdict") or "").lower().strip()
            if verdict in _VERDICT_SCORE:
                scores_for_cat.append(_VERDICT_SCORE[verdict])
        cat_score = (sum(scores_for_cat) / len(scores_for_cat)) if scores_for_cat else None
        recomputed[score_field] = cat_score
        # Always overwrite: LLM often miscalculates (e.g. including insufficient_data items).
        if cat_score is not None:
            cat["score"] = round(cat_score, 2)
            summary[score_field] = round(cat_score, 2)
        else:
            summary[score_field] = None

    # Recompute composite_score using the weighted formula from checklist_judge.yaml:
    # composite = A*0.30 + B*0.25 + C*0.20 + D*0.15 + E*0.10
    # If a category is null (all insufficient_data), redistribute its weight proportionally.
    _WEIGHTS = {"A_score": 0.30, "B_score": 0.25, "C_score": 0.20, "D_score": 0.15, "E_score": 0.10}
    total_weight = sum(w for field, w in _WEIGHTS.items() if recomputed.get(field) is not None)
    if total_weight > 0:
        weighted_sum = sum(
            recomputed[field] * w
            for field, w in _WEIGHTS.items()
            if recomputed.get(field) is not None
        )
        composite = weighted_sum / total_weight
        summary["composite_score"] = round(composite, 3)
        summary["investment_rating"] = _rating_from_composite_score(composite)
    else:
        summary["composite_score"] = None


def _apply_composite_score_fallback(checklist_output: dict) -> None:
    """
    Wrapper that runs both category-level and composite-level deterministic fallbacks.
    Call this after the checklist LLM response.
    """
    _apply_category_score_fallback(checklist_output)


def _patch_current_price(checklist_output: dict, real_price: float | None) -> None:
    """
    LLM이 hallucinate한 current_price 필드를 state의 실제 값으로 덮어씁니다.
    top-level과 payload.summary 두 곳 모두 적용.
    """
    if not isinstance(checklist_output, dict) or real_price is None:
        return
    if "current_price" in checklist_output:
        checklist_output["current_price"] = real_price
    payload = checklist_output.get("payload")
    if isinstance(payload, dict):
        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary["current_price"] = real_price
            # upside_pct 재계산
            target = _as_valid_number(summary.get("target_price"))
            if target is not None and real_price and real_price > 0:
                summary["upside_pct"] = round((target / real_price - 1) * 100, 1)


_TOOL_TO_SOURCE_TYPE: dict[str, str] = {
    "yfinance": "api",
    "fmp": "api",
    "financial modeling prep": "api",
    "alpha_vantage": "api",
    "alphavantage": "api",
    "tavily": "news",
    "edgar": "sec",
    "sec": "sec",
    "sec edgar": "sec",
}


def _normalize_checklist_sources(checklist_output: dict) -> None:
    """
    체크리스트 LLM이 sources를 string 배열 ["yfinance"] 형태로 반환할 때
    {"type": ..., "tool": ..., "title": ...} 객체 배열로 정규화합니다.
    """
    payload = (checklist_output or {}).get("payload", {})
    checklist = (payload or {}).get("checklist", {})
    for cat_val in (checklist or {}).values():
        for item in (cat_val or {}).get("items") or []:
            raw_sources = item.get("sources")
            if not isinstance(raw_sources, list):
                continue
            normalized = []
            for src in raw_sources:
                if isinstance(src, str):
                    key = src.lower().strip()
                    src_type = _TOOL_TO_SOURCE_TYPE.get(key, "api")
                    normalized.append({"type": src_type, "tool": src, "title": src})
                elif isinstance(src, dict):
                    normalized.append(src)
            item["sources"] = normalized


def _compress_news_articles(news: dict, llm: ChatOpenAI) -> dict:
    """
    뉴스 기사 스니펫들을 하나의 코히런트한 내러티브로 압축합니다.
    (open_deep_research의 compress_research 패턴: 원본 데이터 정보 손실 없이 중복 제거)
    - 원본 articles/sources/sentiment 통계는 보존됩니다.
    - narrative_summary 필드를 추가해 researcher LLM이 구조화된 서술을 바로 활용할 수 있습니다.
    """
    articles = news.get("articles") or []
    if not articles:
        return news

    articles_text = "\n\n".join(
        f"[{i + 1}] {a.get('title', '')} ({a.get('published_date', '')})\n{a.get('snippet', '')}"
        for i, a in enumerate(articles)
    )

    try:
        response = invoke_with_backoff(
            llm,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a financial news analyst. "
                        "Compress the following news snippets into a single cohesive narrative. "
                        "Preserve ALL specific facts, figures, dates, and key events verbatim. "
                        "Eliminate only obvious duplication. "
                        "Output plain prose, no bullet points."
                    ),
                },
                {"role": "user", "content": f"News articles to compress:\n\n{articles_text}"},
            ],
        )
        return {**news, "narrative_summary": response.content}
    except Exception:
        # 압축 실패 시 원본 그대로 반환 (안전 우선)
        return news


def _run_researcher_llm(
    *, state: ResearchState, collected: dict, llm: ChatOpenAI,
    prior_output: dict | None = None,
) -> tuple[dict, float | None]:
    system = get_system_prompt("researcher_main.yaml")
    user = get_user_prompt(
        "researcher_main.yaml",
        ticker=state["ticker"],
        company_name=state["company_name"],
        report_date=state["report_date"],
        run_id=state["run_id"],
        competitor_tickers_json=json.dumps(state.get("competitor_tickers", [])),
    )
    # Running Summary 패턴: 이전 분석 결과가 있으면 증분 업데이트를 요청합니다.
    # 새로운 데이터(raw_collected_data)만 전달하고 LLM이 기존 결론을 보존하도록 유도합니다.
    if prior_output:
        user += (
            f"\n\n<prior_researcher_output>\n"
            f"{json.dumps(prior_output, ensure_ascii=False, default=str)}\n"
            f"</prior_researcher_output>\n\n"
            "The above is your previous analysis. "
            "Update it incrementally using the new data in <raw_collected_data> below. "
            "Preserve all prior conclusions unless the new data explicitly contradicts them."
        )
    user += (
        f"\n\n<raw_collected_data>\n"
        f"{json.dumps(collected, ensure_ascii=False, default=str)}\n"
        f"</raw_collected_data>\n\n"
        "CRITICAL: In the income statement rows, use fields ending in _corrected "
        "when available. They are more accurate than raw fields."
    )
    response = invoke_with_backoff(
        llm,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    result = extract_json(response.content)
    valuation = collected.get("valuation") or {}
    current_price = valuation.get("current_price")
    return result, current_price


def initial_data_collector_node(state: ResearchState) -> dict:
    ticker = state["ticker"]
    company_name = state["company_name"]
    report_date = state["report_date"]
    competitors = state.get("competitor_tickers", [])

    cache = state.get("data_cache") or {}
    # Store back to state (ensures subsequent nodes reuse it).
    print(f"[1] Minimal data fetch... ({ticker})")

    categories = list(MINIMAL_CATEGORIES)
    if not competitors:
        categories = [c for c in categories if c != "competitor"]

    collected = fetch_collected_payload(
        ticker=ticker,
        company_name=company_name,
        report_date=report_date,
        competitors=competitors,
        categories=categories,
        cache=cache,
    )

    # 1차 호출: 저비용 모델 사용
    cheap_llm = ChatOpenAI(model=FAST_MODEL, temperature=0)
    researcher_output, current_price = _run_researcher_llm(state=state, collected=collected, llm=cheap_llm)

    # Extract sector_avg_pe from collected data so checklist uses the real value,
    # not a hardcoded default. Key is "competitor" (see data_fetcher.py).
    competitors_data = collected.get("competitor") or {}
    sector_avg_pe = _as_valid_number(competitors_data.get("sector_avg_pe"))

    return {
        "data_cache": cache,
        "raw_collected_data": collected,
        "researcher_output": researcher_output,
        "current_price": current_price,
        **({"sector_avg_pe": sector_avg_pe} if sector_avg_pe is not None else {}),
    }


def _run_checklist_llm(*, state: ResearchState, llm: ChatOpenAI) -> dict:
    system = get_system_prompt("checklist_judge.yaml")
    raw_data = state.get("raw_collected_data") or {}
    has_news = "news" in raw_data
    has_sec = "sec_10k" in raw_data
    user = get_user_prompt(
        "checklist_judge.yaml",
        ticker=state["ticker"],
        company_name=state["company_name"],
        report_date=state["report_date"],
        run_id=state["run_id"],
        current_price=state.get("current_price") or "Data unavailable for current_price",
        sector=state.get("sector", "Technology"),
        sector_avg_pe=state.get("sector_avg_pe", 25),
        researcher_output_json=json.dumps(
            state["researcher_output"], ensure_ascii=False, default=str
        ),
    )
    dynamic_instructions = "\n\nCRITICAL RULES FOR EVALUATION:\n"
    if not has_news:
        dynamic_instructions += "- You DO NOT have 'news' data yet. You MUST evaluate any News, Sentiment, or recent events items (like C1, E1) exactly as 'insufficient_data'.\n"
    if not has_sec:
        dynamic_instructions += "- You DO NOT have 'sec_10k' (Risk) data yet. You MUST evaluate any official Risk Factors or complex Catalysts items (like C3, D1, D2) exactly as 'insufficient_data'.\n"
    dynamic_instructions += "- If data for an item is not explicitly in the provided JSON, your verdict MUST be 'insufficient_data'. DO NOT guess or hallucinate."
    user += dynamic_instructions
    response = invoke_with_backoff(
        llm,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    result = extract_json(response.content)
    _apply_composite_score_fallback(result)
    _patch_current_price(result, state.get("current_price"))
    _normalize_checklist_sources(result)
    return result


def pre_checklist_node(state: ResearchState) -> dict:
    print("[2] Pre-checklist evaluation (fast model)...")
    # 1차 체크리스트: 저비용 모델 사용
    cheap_llm = ChatOpenAI(model=FAST_MODEL, temperature=0)
    result = _run_checklist_llm(state=state, llm=cheap_llm)
    print("  ✅ Pre-checklist done")
    return {"checklist_output": result}


def checklist_node(state: ResearchState) -> dict:
    print("[5] Final checklist evaluation (quality model)...")
    # 최종 체크리스트: 고품질 모델 사용
    quality_llm = ChatOpenAI(model=CHECKLIST_MODEL, temperature=0)
    result = _run_checklist_llm(state=state, llm=quality_llm)
    print("  ✅ Checklist done")
    return {"checklist_output": result}


def upgrade_researcher_node(state: ResearchState) -> dict:
    """lazy_refetch를 건너뛴 경우, researcher_output을 고품질 모델로 업그레이드합니다."""
    print("[4a] Upgrading researcher output to quality model...")
    quality_llm = ChatOpenAI(model=RESEARCHER_MODEL, temperature=0)
    researcher_output, current_price = _run_researcher_llm(
        state=state,
        collected=state["raw_collected_data"],
        llm=quality_llm,
        prior_output=state.get("researcher_output"),
    )
    # current_price는 researcher_llm에서 다시 추출될 수 있으므로 업데이트합니다.
    return {"researcher_output": researcher_output, "current_price": current_price}


def needs_resolver_node(state: ResearchState) -> dict:
    print("[3] Resolving missing data needs...")
    insufficient_item_ids, categories = resolve_needed_categories(state["checklist_output"])
    # Remove categories that were already fetched in initial stage.
    already = set((state.get("raw_collected_data") or {}).keys())
    missing = [c for c in categories if c not in already]
    return {
        "insufficient_item_ids": insufficient_item_ids,
        "data_categories_requested": missing,
    }


def lazy_refetch_node(state: ResearchState) -> dict:
    ticker = state["ticker"]
    company_name = state["company_name"]
    report_date = state["report_date"]
    competitors = state.get("competitor_tickers", [])

    cache = state.get("data_cache") or {}
    existing = state.get("raw_collected_data") or {}

    requested = state.get("data_categories_requested") or []
    if not requested:
        print("[4] Lazy refetch skipped (no missing categories).")
        # 데이터 포맷터와 체크리스트를 다시 실행할 필요가 없으므로 상태를 그대로 반환
        return {
            "current_price": state.get("current_price"),
            "researcher_output": state["researcher_output"],
        }

    print(f"[4] Lazy refetch categories: {requested}")

    newly_collected = fetch_collected_payload(
        ticker=ticker,
        company_name=company_name,
        report_date=report_date,
        competitors=competitors,
        categories=requested,
        cache=cache,
    )

    merged = {**existing, **newly_collected}

    # 뉴스 데이터가 추가되었을 경우, 재무 데이터에 수정된 필드를 적용
    if merged.get("financials") and merged.get("news"):
        from agents.data_fetcher import apply_news_corrected_fields
        merged["financials"] = apply_news_corrected_fields(merged["financials"], merged["news"])

    # 뉴스 압축 (compress_research 패턴): 기사 스니펫 → 내러티브 요약
    # researcher LLM이 10개 raw 스니펫 대신 단일 코히런트 서술을 소비해 토큰 절약 + 품질 향상
    if merged.get("news"):
        fast_llm = ChatOpenAI(model=FAST_MODEL, temperature=0)
        merged["news"] = _compress_news_articles(merged["news"], fast_llm)
        print("  ✅ News compressed (narrative_summary added)")

    # 2차 호출: 고품질 모델 사용 + Running Summary 패턴으로 이전 분석 결과를 베이스로 증분 업데이트
    quality_llm = ChatOpenAI(model=RESEARCHER_MODEL, temperature=0)
    researcher_output, current_price = _run_researcher_llm(
        state=state,
        collected=merged,
        llm=quality_llm,
        prior_output=state.get("researcher_output"),
    )

    competitors_data = merged.get("competitor") or {}
    sector_avg_pe = _as_valid_number(competitors_data.get("sector_avg_pe"))

    return {
        "data_cache": cache,
        "raw_collected_data": merged,
        "researcher_output": researcher_output,
        "current_price": current_price,
        **({"sector_avg_pe": sector_avg_pe} if sector_avg_pe is not None else {}),
    }


def should_refetch(state: ResearchState) -> str:
    """
    needs_resolver 이후 분기:
    - 추가 데이터가 있으면 → lazy_refetch
    - 첫 번째 통과이고 추가 데이터 없으면 → upgrade_researcher (checklist 키로 매핑)
    - 루프 중이고 추가 데이터 없으면 → 더 이상 가져올 데이터가 없으므로 report_sections로 종료
    """
    if state.get("data_categories_requested"):
        return "lazy_refetch"
    if state.get("research_loop_count", 0) > 0:
        return "report_sections"
    return "checklist"


def reflect_node(state: ResearchState) -> dict:
    """
    Reflect & Loop 패턴: 최종 체크리스트 이후 knowledge gap을 재점검합니다.
    - research_loop_count를 증가시켜 루프 횟수를 추적합니다.
    - 최신 checklist_output에서 insufficient_item_ids를 재계산해 should_continue_loop에 전달합니다.
    """
    loop_count = state.get("research_loop_count", 0) + 1
    # 최신 체크리스트 기준으로 insufficient 항목을 다시 계산합니다.
    insufficient_item_ids, _ = resolve_needed_categories(state["checklist_output"])
    print(f"[Reflect] loop={loop_count}, remaining insufficient items: {insufficient_item_ids}")
    return {
        "research_loop_count": loop_count,
        "insufficient_item_ids": insufficient_item_ids,
    }


def should_continue_loop(state: ResearchState) -> str:
    """
    reflect_node 이후 분기:
    - 루프 횟수 < MAX_RESEARCH_LOOPS AND insufficient 항목이 남아있으면 → continue (needs_resolver 재실행)
    - 아니면 → done (report_sections)
    """
    if state.get("research_loop_count", 0) >= MAX_RESEARCH_LOOPS:
        return "done"
    if not state.get("insufficient_item_ids"):
        return "done"
    return "continue"


def report_sections_node(state: ResearchState) -> dict:
    print("[6] Sectional drafting + merge + validation...")
    report_output = build_final_report(state)
    # Clear cache (ephemeral optimization).
    return {
        "report_output": report_output,
        "data_cache": {},
    }


def build_graph():
    g = StateGraph(ResearchState)

    g.add_node("initial_data_collector", initial_data_collector_node)
    g.add_node("pre_checklist", pre_checklist_node)
    g.add_node("needs_resolver", needs_resolver_node)
    g.add_node("upgrade_researcher", upgrade_researcher_node)
    g.add_node("lazy_refetch", lazy_refetch_node)
    g.add_node("checklist", checklist_node)
    g.add_node("reflect", reflect_node)
    g.add_node("report_sections", report_sections_node)

    g.add_edge(START, "initial_data_collector")
    g.add_edge("initial_data_collector", "pre_checklist")
    g.add_edge("pre_checklist", "needs_resolver")

    # needs_resolver 이후 분기:
    # - 추가 데이터 있음 → lazy_refetch
    # - 첫 통과 & 추가 데이터 없음 → upgrade_researcher ("checklist" 키로 매핑)
    # - 루프 중 & 추가 데이터 없음 → report_sections (더 이상 fetch할 것이 없으므로 종료)
    g.add_conditional_edges("needs_resolver", should_refetch, {
        "lazy_refetch": "lazy_refetch",
        "checklist": "upgrade_researcher",
        "report_sections": "report_sections",
    })
    g.add_edge("upgrade_researcher", "checklist")
    g.add_edge("lazy_refetch", "checklist")

    # checklist 이후 → reflect (Reflect & Loop 패턴)
    g.add_edge("checklist", "reflect")
    g.add_conditional_edges("reflect", should_continue_loop, {
        "continue": "needs_resolver",   # insufficient 항목 남아있으면 루프 재진입
        "done": "report_sections",      # 루프 한도 초과 or 항목 없으면 리포트 작성
    })

    g.add_edge("report_sections", END)

    return g.compile()