import requests
import re

HEADERS  = {"User-Agent": "company-deep-research research@example.com"}
BASE_URL = "https://www.sec.gov"

# 소스당 최대 컨텍스트 비용을 결정론적으로 제어합니다.
SEC_FULL_TEXT_FETCH_CHARS  = 500_000  # 전체 10-K 원문 fetch 한도
MAX_CHARS_PER_SEC_SECTION  = 30_000   # Item 1A (Risk Factors) 슬라이스 한도


# Heuristics used to avoid extracting "corporate boilerplate" text from
# Item 1A Risk Factors (e.g., insider trading policies, codes of conduct,
# generic legal disclaimers).
_RISK_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"forward[-\s]?looking statements?", re.IGNORECASE),
    re.compile(r"insider trading", re.IGNORECASE),
    re.compile(r"\bmnpi\b", re.IGNORECASE),
    re.compile(r"material nonpublic information", re.IGNORECASE),
    re.compile(r"code of (conduct|ethics)", re.IGNORECASE),
    re.compile(r"conflict of interest", re.IGNORECASE),
    re.compile(r"employee (code|conduct|ethics)", re.IGNORECASE),
    re.compile(r"not intended to be (exhaustive|complete)", re.IGNORECASE),
    re.compile(r"generic legal disclaimer|disclaimer", re.IGNORECASE),
    re.compile(r"rule\s*10b-?5-?1", re.IGNORECASE),
    re.compile(r"section\s*16", re.IGNORECASE),
    # Policy / HR language that leaks from insider-trading policy sections
    re.compile(r"unless\s+you\s+are\s+expressly\s+authorized", re.IGNORECASE),
    re.compile(r"you\s+are\s+not\s+(permitted|authorized|allowed)\s+to", re.IGNORECASE),
    re.compile(r"performing\s+your\s+job\s+duties", re.IGNORECASE),
    # "Examples of material information could include: ..."
    re.compile(r"examples\s+of\s+material\s+(non[-\s]?public\s+)?information", re.IGNORECASE),
    # Analyst/broker-dealer disclosure boilerplate
    re.compile(r"broker[-\s]?dealers?\s+or\s+investment\s+research", re.IGNORECASE),
    re.compile(r"financial\s+analysts?,\s+broker", re.IGNORECASE),
    # Generic "you may not provide information to the press" policy sentences
    re.compile(r"you\s+may\s+not\s+provide\s+information\s+to\s+the\s+press", re.IGNORECASE),
]

_SYSTEMIC_RISK_KEYWORDS: list[re.Pattern[str]] = [
    # Competitive / market structure
    re.compile(r"competition|competitive|competitor|market share|pricing", re.IGNORECASE),
    # Demand / customers
    re.compile(r"demand|customer|customers|market", re.IGNORECASE),
    # Macro / economic / rates / inflation
    re.compile(r"inflation|interest rate|interest rates|recession|economic downturn|macroeconomic", re.IGNORECASE),
    # Geopolitical / trade / sanctions / foreign exposure
    re.compile(r"sanction|tariff|geopolitical|trade restrictions|foreign|currency|exchange rate", re.IGNORECASE),
    # Operations / supply chain / execution
    re.compile(r"supply|manufactur|third[-\s]?party|operations|execution|production", re.IGNORECASE),
    # Technology / product / obsolescence
    re.compile(r"technology|product|platform|innovation|obsolescence", re.IGNORECASE),
    # Cyber / data security
    re.compile(r"cyber|data breach|security", re.IGNORECASE),
    # Regulatory (often still "business risk", but not generic boilerplate)
    re.compile(r"regulation|regulatory|lawsuit|litigation|compliance", re.IGNORECASE),
    # Financial condition / liquidity
    re.compile(r"\bliquidity\b|cash flow|debt|leverage|financing|refinanc", re.IGNORECASE),
]


def _looks_like_boilerplate(sentence: str) -> bool:
    s = sentence or ""
    return any(p.search(s) for p in _RISK_BOILERPLATE_PATTERNS)


def _systemic_keyword_score(sentence: str) -> int:
    s = sentence or ""
    score = 0
    for kw in _SYSTEMIC_RISK_KEYWORDS:
        if kw.search(s):
            score += 1
    return score


def get_ticker_cik(ticker: str) -> str | None:
    try:
        url  = f"{BASE_URL}/files/company_tickers.json"
        r    = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        for item in data.values():
            if item["ticker"].upper() == ticker.upper():
                return str(item["cik_str"]).zfill(10)
        return None
    except Exception:
        return None


def get_recent_accession(cik: str, form_type: str) -> dict | None:
    try:
        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r    = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()

        filings      = data.get("filings", {}).get("recent", {})
        forms        = filings.get("form", [])
        dates        = filings.get("filingDate", [])
        accnums      = filings.get("accessionNumber", [])
        primary_docs = filings.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form == form_type:
                return {
                    "form":             form,
                    "filed_date":       dates[i],
                    "accession_number": accnums[i].replace("-", ""),
                    "primary_doc":      primary_docs[i] if i < len(primary_docs) else None,
                }
        return None
    except Exception:
        return None


def _select_main_doc_url(index_html: str) -> str | None:
    """
    EDGAR filing index HTML에서 메인 10-K 문서 URL을 선택합니다.
    Type 열이 "10-K"인 행을 우선, 없으면 "10k"가 파일명에 포함된 것,
    그것도 없으면 가장 큰 htm 파일(exhibit 제외)을 반환합니다.
    """
    # EDGAR index table: <td><a href="...">filename</a></td><td>desc</td><td>type</td>
    rows = re.findall(
        r'href="(/Archives/edgar/data/[^"]+\.htm)"[^>]*>[^<]*</a>'
        r'(?:[^<]*<[^>]+>)*?[^<]*<td[^>]*>([^<]*)</td>',
        index_html,
        re.IGNORECASE,
    )

    # Type 열이 정확히 "10-K"인 행 우선
    for url, doc_type in rows:
        if doc_type.strip().upper() in ("10-K", "10-KT"):
            return url

    # 파일명에 "10k" 포함, exhibit/exhibit_* 제외
    all_htm = re.findall(
        r'href="(/Archives/edgar/data/[^"]+\.htm)"', index_html, re.IGNORECASE
    )
    non_index = [u for u in all_htm if "index" not in u.lower()]
    for u in non_index:
        fname = u.split("/")[-1].lower()
        if re.search(r"10k|10-k", fname) and "ex" not in fname:
            return u

    # fallback: 인덱스/exhibit 제외한 첫 번째 htm
    for u in non_index:
        fname = u.split("/")[-1].lower()
        if not re.search(r"^ex|exhibit", fname):
            return u

    return non_index[0] if non_index else None


def fetch_filing_text(cik: str, accession: str, max_chars: int = SEC_FULL_TEXT_FETCH_CHARS,
                      primary_doc: str | None = None) -> str:
    """
    공시 원문 전체를 가져와 HTML을 제거한 뒤 반환합니다.
    primary_doc이 주어지면 인덱스 파싱을 생략하고 직접 문서 URL을 구성합니다.
    """
    try:
        cik_int = int(cik)
        acc_fmt = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"

        if primary_doc:
            # Use the known primary document filename directly — most reliable path.
            doc_url = (f"{BASE_URL}/Archives/edgar/data/"
                       f"{cik_int}/{accession}/{primary_doc}")
        else:
            # Fall back to parsing the index HTML.
            idx_url  = (f"{BASE_URL}/Archives/edgar/data/"
                        f"{cik_int}/{accession}/{acc_fmt}-index.htm")
            r        = requests.get(idx_url, headers=HEADERS, timeout=15)
            idx_html = r.text
            doc_url_path = _select_main_doc_url(idx_html)
            if not doc_url_path:
                clean = re.sub(r"<[^>]+>", " ", idx_html)
                return re.sub(r"\s+", " ", clean).strip()[:max_chars]
            doc_url = BASE_URL + doc_url_path

        r2  = requests.get(doc_url, headers=HEADERS, timeout=30)
        raw = r2.text

        # HTML 태그 / 엔티티 제거 후 공백 정리
        clean = re.sub(r"<[^>]+>", " ", raw)
        clean = re.sub(r"&[a-zA-Z0-9#]+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:max_chars]

    except Exception as e:
        return f"[fetch error: {e}]"


def _slice_item1a(text: str, max_chars: int = MAX_CHARS_PER_SEC_SECTION) -> str:
    """
    전체 10-K 텍스트에서 'Item 1A. Risk Factors' 섹션만 잘라냅니다.
    Item 1B 또는 Item 2 시작 부분까지를 경계로 합니다.
    섹션을 찾지 못하면 빈 문자열을 반환합니다.
    """
    upper = text.upper()

    start_idx = -1
    for marker in [
        "ITEM 1A. RISK FACTORS",
        "ITEM 1A RISK FACTORS",
        "ITEM\u00a01A",          # non-breaking space 변형
        "ITEM 1A:",
    ]:
        idx = upper.find(marker)
        if idx != -1:
            start_idx = idx
            break

    if start_idx == -1:
        return ""

    # 경계: Item 1B 또는 Item 2 (최소 500자 이후에서 검색)
    end_idx = len(text)
    for end_marker in ["ITEM 1B", "ITEM\u00a01B", "ITEM 2.", "ITEM\u00a02."]:
        idx = upper.find(end_marker, start_idx + 500)
        if idx != -1 and idx < end_idx:
            end_idx = idx

    return text[start_idx:end_idx][:max_chars]


def extract_risk_factors(text: str) -> list[str]:
    """텍스트에서 Risk Factors 섹션을 추출한다."""
    # Extract ONLY "Item 1A. Risk Factors" content, bounded by the next
    # "Item 1B" section.
    #
    # Note: `fetch_filing_text()` removes HTML and collapses whitespace,
    # so we rely on headings being present in the flattened text.
    section_match = re.search(
        r"ITEM\s+1A(?:\s*[\.\-:]*\s*)RISK\s+FACTORS(.*?)(?:ITEM\s+1B(?:\s*[\.\-:]*\s*))",
        text or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    section = section_match.group(1).strip() if section_match else ""

    # If we can't isolate Item 1A, fall back to sentence-level extraction,
    # but still avoid boilerplate via heuristics.
    candidate_source = section if section else (text or "")

    sentences = re.split(r"(?<=[.!?])\s+", candidate_source)

    scored: list[tuple[int, str]] = []
    for s in sentences:
        s = (s or "").strip()
        if not s:
            continue
        if s.startswith("•"):
            continue

        # Length filter: too short tends to be fragments; too long tends to
        # include adjacent boilerplate.
        if len(s) < 80 or len(s) > 500:
            continue

        # Hard exclude obvious boilerplate/disclaimer text.
        if _looks_like_boilerplate(s):
            continue

        # Require at least one systemic risk keyword to reduce boilerplate.
        kw_score = _systemic_keyword_score(s)
        if kw_score <= 0:
            continue

        scored.append((kw_score, s))

    if not scored:
        # Last resort: keep "risk" keyword sentences, still excluding
        # boilerplate.
        risk_sents: list[str] = []
        for s in re.split(r"(?<=[.!?])\s+", text or ""):
            s = (s or "").strip()
            if len(s) < 80 or len(s) > 300:
                continue
            if _looks_like_boilerplate(s):
                continue
            if "risk" in s.lower():
                risk_sents.append(s)
        return risk_sents[:3]

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for _, s in scored[:3]]
    return top


def get_sec_filing_text(ticker: str, form_type: str = "10-K") -> dict:
    try:
        cik = get_ticker_cik(ticker)
        if not cik:
            return {"ticker": ticker, "form_type": form_type,
                    "error": "CIK not found", "sections": {}}

        filing = get_recent_accession(cik, form_type)
        if not filing:
            return {"ticker": ticker, "form_type": form_type,
                    "error": f"No {form_type} found", "sections": {}}

        accnum      = filing["accession_number"]
        date        = filing["filed_date"]
        primary_doc = filing.get("primary_doc")

        # 넉넉하게 SEC_FULL_TEXT_FETCH_CHARS 가져와서 Item 1A 위치까지 도달
        # primary_doc이 있으면 인덱스 파싱 없이 직접 문서 URL 사용 (더 신뢰할 수 있음)
        full_text = fetch_filing_text(cik, accnum, max_chars=SEC_FULL_TEXT_FETCH_CHARS, primary_doc=primary_doc)

        # Risk Factors: Item 1A 섹션만 슬라이스 후 추출
        item1a_text  = _slice_item1a(full_text)
        risk_factors = extract_risk_factors(item1a_text if item1a_text else full_text[:30_000])

        # Guidance: Item 1A 이후 텍스트에서 forward-looking 문장 검색
        guidance_source = full_text[full_text.upper().find("ITEM 1A"):] if "ITEM 1A" in full_text.upper() else full_text
        guidance_sentences = []
        for sent in re.split(r"(?<=[.!?])\s+", guidance_source):
            sent = sent.strip()
            if not sent or _looks_like_boilerplate(sent):
                continue
            if any(kw in sent.lower() for kw in
                   ["expect", "guidance", "outlook", "forecast", "anticipate"]):
                if 80 < len(sent) < 300:
                    guidance_sentences.append(sent)

        return {
            "ticker":           ticker,
            "form_type":        form_type,
            "filed_date":       date,
            "cik":              cik,
            "accession_number": accnum,
            "sections": {
                "top_risk_factors": risk_factors,
                "guidance_hints":   guidance_sentences[:3],
            },
            "sources": [{
                "type":  "sec",
                "tool":  "edgar",
                "id":    accnum,
                "title": f"{ticker} {form_type} filed {date} (SEC EDGAR)"
            }]
        }
    except Exception as e:
        return {"ticker": ticker, "form_type": form_type,
                "error": str(e), "sections": {}}