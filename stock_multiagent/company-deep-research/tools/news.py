from tavily import TavilyClient
import os
import re

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 소스당 최대 컨텍스트 비용을 결정론적으로 제어합니다.
# 4 chars ≈ 1 token 기준: 400 chars ≈ 100 tokens/article
MAX_CHARS_PER_ARTICLE = 400

POSITIVE_KEYWORDS = [
    "beat", "record", "growth", "surge", "strong", "exceed", "upgrade",
    "bullish", "profit", "gain", "rally", "outperform", "raised", "positive",
    "revenue growth", "earnings beat", "new high", "partnership", "wins"
]
NEGATIVE_KEYWORDS = [
    "miss", "decline", "fall", "drop", "risk", "concern", "downgrade",
    "bearish", "loss", "sell", "weak", "cut", "lower", "negative",
    "lawsuit", "investigation", "ban", "restrict", "warning", "disappointing"
]


def tag_sentiment(text: str) -> str:
    text_lower = text.lower()
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def search_news(query: str, days_back: int = 7, max_results: int = 10) -> dict:
    try:
        client   = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            days=days_back,
        )

        articles = []
        for r in response.get("results", []):
            snippet   = r.get("content", "")[:MAX_CHARS_PER_ARTICLE]
            title     = r.get("title", "")
            sentiment = tag_sentiment(title + " " + snippet)
            articles.append({
                "title":          title,
                "url":            r.get("url"),
                "published_date": r.get("published_date"),
                "source_name":    r.get("source"),
                "snippet":        snippet,
                "sentiment":      sentiment,
            })

        total = len(articles)
        pos   = sum(1 for a in articles if a["sentiment"] == "positive")
        neg   = sum(1 for a in articles if a["sentiment"] == "negative")
        neu   = total - pos - neg

        return {
            "query":             query,
            "articles_analyzed": total,
            "positive_pct":      round(pos / total * 100, 1) if total else None,
            "negative_pct":      round(neg / total * 100, 1) if total else None,
            "neutral_pct":       round(neu / total * 100, 1) if total else None,
            "articles":          articles,
            "sources": [
                {"type": "news", "tool": "tavily",
                 "title": a["title"], "id": a["url"]}
                for a in articles
            ]
        }
    except Exception as e:
        return {
            "query": query, "error": str(e), "articles": [],
            "articles_analyzed": 0,
            "positive_pct": None, "negative_pct": None, "neutral_pct": None
        }


def parse_earnings_from_news(articles: list) -> dict:
    result = {
        "diluted_eps":      None,
        "eps_consensus":    None,
        "eps_surprise_pct": None,
        "revenue_yoy_pct":  None,
    }

    for article in articles:
        text = (article.get("snippet", "") + " " + article.get("title", "")).lower()

        # "EPS of $1.62 beat estimates by 6.58%"
        m = re.search(
            r"eps\s+of\s+\$?([\d.]+)\s+beat\s+estimates?\s+by\s+([\d.]+)%", text)
        if m:
            result["diluted_eps"]      = float(m.group(1))
            result["eps_surprise_pct"] = float(m.group(2))

        # "1.62 USD per share, beating the 1.54 USD estimate"
        m = re.search(
            r"([\d.]+)\s+usd per share,?\s+beating\s+the\s+([\d.]+)\s+usd estimate", text)
        if m:
            result["diluted_eps"]   = float(m.group(1))
            result["eps_consensus"] = float(m.group(2))

        # "beat the $X estimate by Y%"
        m = re.search(r"beat.*?\$([\d.]+).*?estimate.*?by\s+([\d.]+)%", text)
        if m and result["eps_consensus"] is None:
            result["eps_consensus"]    = float(m.group(1))
            result["eps_surprise_pct"] = float(m.group(2))

        # "up 73% from a year ago"
        m = re.search(r"up\s+([\d.]+)%\s+from\s+a\s+year", text)
        if m:
            result["revenue_yoy_pct"] = float(m.group(1))

        # "73% year-over-year"
        m = re.search(r"([\d.]+)%\s+year.over.year", text)
        if m and result["revenue_yoy_pct"] is None:
            result["revenue_yoy_pct"] = float(m.group(1))

        # "65.47% growth"
        m = re.search(r"([\d.]+)%\s+growth", text)
        if m and result["revenue_yoy_pct"] is None:
            result["revenue_yoy_pct"] = float(m.group(1))

    return result