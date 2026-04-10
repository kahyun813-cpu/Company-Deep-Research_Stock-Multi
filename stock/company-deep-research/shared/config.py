import os
from pathlib import Path
from dotenv import load_dotenv

# .env가 tools/ 아래에 있으므로 명시적으로 경로 지정
load_dotenv(Path(__file__).parent.parent / "tools" / ".env")

# LLM 모델 설정
RESEARCHER_MODEL = os.getenv("RESEARCHER_MODEL", "gpt-4o-mini")
CHECKLIST_MODEL  = os.getenv("CHECKLIST_MODEL",  "gpt-4o")
WRITER_MODEL     = os.getenv("WRITER_MODEL",     "gpt-4o")
FAST_MODEL       = os.getenv("FAST_MODEL",       "gpt-4o-mini")

# Reflect 루프 설정: insufficient_data 항목이 남아있을 때 최대 반복 횟수
MAX_RESEARCH_LOOPS = int(os.getenv("MAX_RESEARCH_LOOPS", "2"))

# API 키
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY")
FMP_API_KEY      = os.getenv("FMP_API_KEY")

# LangSmith
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "true")
LANGCHAIN_PROJECT    = os.getenv("LANGCHAIN_PROJECT", "company-deep-research")