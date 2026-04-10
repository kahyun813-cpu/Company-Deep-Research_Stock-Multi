# Company Deep Research

LangGraph 기반 멀티 에이전트 주식 심층 분석 도구입니다.  
티커(ticker)를 입력하면 재무 데이터, 뉴스, SEC 공시를 자동 수집하고, AI가 투자 체크리스트 평가와 상세 리포트를 생성합니다.

---

## 주요 기능

- **자동 데이터 수집**: yfinance, Financial Modeling Prep, SEC EDGAR, Tavily 뉴스 통합
- **멀티 에이전트 파이프라인**: LangGraph 기반 워크플로우 (데이터 수집 → 체크리스트 → 리플렉션 루프 → 리포트)
- **투자 체크리스트**: A~E 5개 카테고리 가중 평가 → 종합 점수 및 투자 등급 산출
- **Reflect & Loop**: 데이터 부족 항목 감지 시 자동 추가 수집 후 재평가
- **구조화된 JSON 출력**: `researcher_output`, `checklist_output`, `report_output` 3종 파일 저장

---

## 투자 등급 기준

| 등급 | 종합 점수 |
|------|-----------|
| STRONG BUY | 7.5 이상 |
| MODERATE BUY | 6.0 ~ 7.5 |
| HOLD | 4.5 ~ 6.0 |
| MODERATE SELL | 3.0 ~ 4.5 |
| SELL | 3.0 미만 |

### 카테고리별 가중치

| 카테고리 | 내용 | 가중치 |
|----------|------|--------|
| A | 재무 건전성 (Financial Health) | 30% |
| B | 밸류에이션 (Valuation) | 25% |
| C | 촉매 요인 (Catalysts) | 20% |
| D | 산업 환경 (Industry) | 15% |
| E | 시장 심리 (Sentiment) | 10% |

---

## 파이프라인 구조

```
[초기 데이터 수집]
       ↓
[Pre-Checklist (Fast Model)]
       ↓
[Needs Resolver: 부족 데이터 파악]
       ↓
  ┌────┴────┐
  │         │
[Lazy Refetch]  [Upgrade Researcher]
  │         │
  └────┬────┘
       ↓
[Final Checklist (Quality Model)]
       ↓
[Reflect: 루프 여부 결정]
       ↓
  부족 항목 있음 → Needs Resolver (최대 2회 반복)
  완료          → [Report Sections]
       ↓
      END
```

---

## 설치 및 실행

### 사전 요구 사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/) 패키지 매니저

### 설치

```bash
git clone <repository-url>
cd company-deep-research

uv sync
```

### 환경 변수 설정

`tools/.env` 파일을 생성하고 API 키를 입력합니다.

```env
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
FMP_API_KEY=...

# 모델 설정 (선택, 기본값 아래와 같음)
RESEARCHER_MODEL=gpt-4o-mini
CHECKLIST_MODEL=gpt-4o
WRITER_MODEL=gpt-4o
FAST_MODEL=gpt-4o-mini

# Reflect 루프 최대 반복 횟수 (선택, 기본값 2)
MAX_RESEARCH_LOOPS=2

# LangSmith 트레이싱 (선택)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=company-deep-research
LANGCHAIN_API_KEY=...
```

### API 키 발급

| 서비스 | 용도 | 발급 링크 |
|--------|------|-----------|
| OpenAI | LLM 분석 | https://platform.openai.com |
| Tavily | 뉴스 검색 | https://tavily.com |
| Financial Modeling Prep | 재무 데이터 | https://financialmodelingprep.com |

### 실행

```bash
uv run python main.py
```

`main.py` 하단의 `run()` 호출 부분을 수정하여 원하는 종목을 분석합니다.

```python
if __name__ == "__main__":
    run(
        ticker="NVDA",
        company_name="NVIDIA Corporation",
        competitors=["AMD", "INTC", "QCOM"],
    )
```

---

## 출력 파일

실행 결과는 `outputs/<run_id>/` 디렉터리에 저장됩니다.

```
outputs/
└── 20260329-4692a89d/
    ├── researcher_output.json   # 정제된 재무·산업 데이터
    ├── checklist_output.json    # 투자 체크리스트 및 등급
    └── report_output.json       # 최종 분석 리포트
```

---

## 프로젝트 구조

```
company-deep-research/
├── main.py                  # 진입점
├── pyproject.toml
├── agents/
│   ├── graph.py             # LangGraph 워크플로우 정의
│   ├── data_fetcher.py      # 데이터 수집 오케스트레이터
│   ├── needs_resolver.py    # 부족 데이터 카테고리 판별
│   ├── report_sections.py   # 섹션별 리포트 생성
│   └── state.py             # ResearchState 타입 정의
├── tools/
│   ├── financials.py        # yfinance 재무제표
│   ├── valuation.py         # 밸류에이션 · 경쟁사 비교
│   ├── news.py              # Tavily 뉴스 검색
│   ├── sec.py               # SEC EDGAR 10-K 공시
│   └── .env                 # API 키 (gitignore)
├── prompts/
│   ├── researcher_main.yaml
│   ├── checklist_judge.yaml
│   ├── report_writer.yaml
│   └── section_writer_*.yaml
└── shared/
    ├── config.py            # 환경 변수 로드
    ├── llm_invoke.py        # 백오프 재시도 LLM 호출
    ├── normalization.py     # JSON 정규화
    └── yaml_loader.py       # 프롬프트 로더
```

---

## 의존성

주요 라이브러리:

- `langgraph` — 멀티 에이전트 그래프
- `langchain-openai` — OpenAI LLM 연동
- `yfinance` — 주가·재무 데이터
- `tavily-python` — 뉴스 검색
- `python-dotenv` — 환경 변수 관리
