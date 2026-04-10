import json
import uuid
from datetime import date
from pathlib import Path
from agents.graph import build_graph
from shared.normalization import replace_none_with_unavailable_strings

def run(ticker: str, company_name: str, competitors: list = None):
    run_id      = date.today().strftime("%Y%m%d") + "-" + str(uuid.uuid4())[:8]
    report_date = date.today().strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"  Company Deep Research")
    print(f"  Ticker: {ticker}  |  Date: {report_date}")
    print(f"{'='*50}\n")

    graph = build_graph()

    result = graph.invoke({
        "run_id":             run_id,
        "ticker":             ticker,
        "company_name":       company_name,
        "report_date":        report_date,
        "sector":             "Technology",
        "sector_avg_pe":      28.0,
        "competitor_tickers": competitors or [],
    })

    out_dir = Path("outputs") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for key in ["researcher_output", "checklist_output", "report_output"]:
        if result.get(key):
            cleaned = replace_none_with_unavailable_strings(result[key], field_name=key)
            path = out_dir / f"{key}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            print(f"  저장됨: {path}")
            result[key] = cleaned

    print(f"\n완료! 결과: outputs/{run_id}/")
    return result


if __name__ == "__main__":
    run(
        ticker="NVDA",
        company_name="NVIDIA Corporation",
        competitors=["AMD", "INTC", "QCOM"],
    )