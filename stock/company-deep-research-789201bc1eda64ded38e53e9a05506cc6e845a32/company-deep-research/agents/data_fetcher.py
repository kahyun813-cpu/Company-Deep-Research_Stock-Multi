from __future__ import annotations
import json
from typing import Any
from tools.financials import get_financials, get_earnings_history, get_earnings_calendar, get_insider_trades
from tools.news import search_news, parse_earnings_from_news
from tools.sec import get_sec_filing_text
from tools.valuation import get_valuation, get_analyst_targets, get_competitor_comparison

def _cache_key(tool_name: str, params: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(params, sort_keys=True, default=str)}"

def cached_tool_call(cache: dict[str, Any], tool_name: str, params: dict[str, Any], fn):
    key = _cache_key(tool_name, params)
    if key in cache:
        return cache[key]
    result = fn(**params)
    cache[key] = result
    return result

def apply_news_corrected_fields(financials: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_earnings_from_news(news.get("articles", []))
    income_rows = financials.get("statements", {}).get("income", [])
    if not income_rows or not any(parsed.values()):
        return financials
    row = income_rows[0]
    if parsed.get("diluted_eps") is not None:
        row["diluted_eps_corrected"] = parsed["diluted_eps"]
    if parsed.get("eps_consensus") is not None:
        row["eps_consensus_corrected"] = parsed["eps_consensus"]
    if parsed.get("eps_surprise_pct") is not None:
        row["eps_surprise_pct_corrected"] = parsed["eps_surprise_pct"]
    if parsed.get("revenue_yoy_pct") is not None:
        row["revenue_yoy_pct_corrected"] = parsed["revenue_yoy_pct"]
    row["_note"] = "IMPORTANT: USE _corrected fields for EPS and revenue_yoy."
    return financials

def fetch_collected_payload(*, ticker: str, company_name: str, report_date: str, competitors: list[str] | None, categories: list[str], cache: dict[str, Any]) -> dict[str, Any]:
    competitors = competitors or []
    collected: dict[str, Any] = {}

    # Standard tool calls
    if "financials" in categories:
        collected["financials"] = cached_tool_call(cache, "get_financials", {"ticker": ticker}, lambda **p: get_financials(**p))
    if "earnings_hist" in categories:
        collected["earnings_hist"] = cached_tool_call(cache, "get_earnings_history", {"ticker": ticker}, lambda **p: get_earnings_history(**p))
    if "valuation" in categories:
        collected["valuation"] = cached_tool_call(cache, "get_valuation", {"ticker": ticker}, lambda **p: get_valuation(**p))
    if "analyst" in categories:
        collected["analyst"] = cached_tool_call(cache, "get_analyst_targets", {"ticker": ticker}, lambda **p: get_analyst_targets(**p))
    
    if "earnings_calendar" in categories:
        collected["earnings_calendar"] = cached_tool_call(cache, "get_earnings_calendar", {"ticker": ticker}, lambda **p: get_earnings_calendar(**p))

    if "insider" in categories:
        collected["insider"] = cached_tool_call(cache, "get_insider_trades", {"ticker": ticker}, lambda **p: get_insider_trades(**p))

    # IMPROVED NEWS QUERY
    if "news" in categories:
        collected["news"] = cached_tool_call(cache, "search_news", {"query": f"{ticker} {company_name} news"}, lambda **p: search_news(**p))

    if "sec_10k" in categories:
        collected["sec_10k"] = cached_tool_call(cache, "get_sec_filing_text", {"ticker": ticker, "form_type": "10-K"}, lambda **p: get_sec_filing_text(p["ticker"], p["form_type"]))

    if "competitor" in categories and competitors:
        collected["competitor"] = cached_tool_call(cache, "get_competitor_comparison", {"tickers": competitors}, lambda **p: get_competitor_comparison(p["tickers"]))
    
    if collected.get("financials") and collected.get("news"):
        collected["financials"] = apply_news_corrected_fields(collected["financials"], collected["news"])

    return collected