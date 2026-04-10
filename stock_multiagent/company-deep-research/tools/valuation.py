import requests
import yfinance as yf
import os

FMP_BASE   = "https://financialmodelingprep.com/api/v3"
FMP_API_KEY = os.getenv("FMP_API_KEY", "")


def get_valuation(ticker: str) -> dict:
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        # PEG: yfinance info에서 직접
        peg = info.get("pegRatio") or info.get("trailingPegRatio")

        # price_to_fcf: FMP에서 보완
        price_to_fcf = None
        if FMP_API_KEY:
            try:
                url = (f"{FMP_BASE}/key-metrics/{ticker}"
                       f"?period=quarterly&limit=1&apikey={FMP_API_KEY}")
                r = requests.get(url, timeout=10)
                if r.status_code == 200 and r.json():
                    m = r.json()[0]
                    price_to_fcf = m.get("pfcfRatio")
                    peg = peg or m.get("pegRatio")
            except Exception:
                pass

        return {
            "ticker":           ticker,
            "pe":               info.get("trailingPE"),
            "pb":               info.get("priceToBook"),
            "ps":               info.get("priceToSalesTrailing12Months"),
            "ev_ebitda":        info.get("enterpriseToEbitda"),
            "peg":              peg,
            "market_cap":       info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "price_to_fcf":     price_to_fcf,
            "current_price":    info.get("currentPrice") or info.get("regularMarketPrice"),
            "sources": [
                {"type": "api", "tool": "yfinance",
                 "title": f"{ticker} Valuation Multiples"}
            ]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_analyst_targets(ticker: str) -> dict:
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        # 최근 30일 등급 변경
        upgrades_downgrades = []
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                ud = ud.reset_index()
                # 최근 30일
                from datetime import date, timedelta
                cutoff = date.today() - timedelta(days=30)
                for _, row in ud.iterrows():
                    row_date = None
                    try:
                        row_date = row["GradeDate"].date()
                    except Exception:
                        pass
                    if row_date and row_date >= cutoff:
                        upgrades_downgrades.append({
                            "date":        str(row_date),
                            "firm":        row.get("Firm", ""),
                            "from_grade":  row.get("FromGrade", ""),
                            "to_grade":    row.get("ToGrade", ""),
                            "action":      row.get("Action", ""),
                        })
        except Exception:
            pass

        upgrades   = sum(1 for x in upgrades_downgrades if x["action"] == "up")
        downgrades = sum(1 for x in upgrades_downgrades if x["action"] == "down")

        return {
            "ticker":             ticker,
            "target_low":         info.get("targetLowPrice"),
            "target_mean":        info.get("targetMeanPrice"),
            "target_high":        info.get("targetHighPrice"),
            "buy_count":          info.get("numberOfAnalystOpinions"),
            "recommendation":     info.get("recommendationKey"),
            "upgrades_30d":       upgrades,
            "downgrades_30d":     downgrades,
            "net_rating_change":  upgrades - downgrades,
            "rating_changes":     upgrades_downgrades[:10],
            "sources": [
                {"type": "api", "tool": "yfinance",
                 "title": f"{ticker} Analyst Targets & Rating Changes"}
            ]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_competitor_comparison(tickers: list, metrics: list = None) -> dict:
    peers = []
    for t_str in tickers:
        try:
            t    = yf.Ticker(t_str)
            info = t.info
            peers.append({
                "ticker":           t_str,
                "company_name":     info.get("shortName"),
                "pe":               info.get("trailingPE"),
                "pb":               info.get("priceToBook"),
                "operating_margin": info.get("operatingMargins"),
                "revenue_growth":   info.get("revenueGrowth"),
                "ev_ebitda":        info.get("enterpriseToEbitda"),
            })
        except Exception:
            peers.append({"ticker": t_str, "error": "fetch failed"})

    pe_values      = [p["pe"] for p in peers if p.get("pe")]
    sector_avg_pe  = round(sum(pe_values) / len(pe_values), 2) if pe_values else None

    return {
        "competitors":    peers,
        "sector_avg_pe":  sector_avg_pe,
        "sources": [{"type": "api", "tool": "yfinance",
                     "title": "Competitor Comparison"}]
    }