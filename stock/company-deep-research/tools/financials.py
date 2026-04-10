import yfinance as yf


def get_financials(ticker: str, period: str = "quarterly", limit: int = 4) -> dict:
    try:
        t = yf.Ticker(ticker)

        if period == "quarterly":
            income   = t.quarterly_income_stmt
            balance  = t.quarterly_balance_sheet
            cashflow = t.quarterly_cashflow
        else:
            income   = t.income_stmt
            balance  = t.balance_sheet
            cashflow = t.cashflow

        def df_to_list(df, lim):
            if df is None or df.empty:
                return []
            df = df.iloc[:, :lim]
            result = []
            for col in df.columns:
                row = {"date": str(col.date())}
                for idx in df.index:
                    val = df.loc[idx, col]
                    row[str(idx)] = None if str(val) == "nan" else float(val)
                result.append(row)
            return result

        return {
            "ticker": ticker,
            "period": period,
            "statements": {
                "income":   df_to_list(income, limit),
                "balance":  df_to_list(balance, limit),
                "cashflow": df_to_list(cashflow, limit),
            },
            "sources": [{"type": "api", "tool": "yfinance",
                         "title": f"{ticker} Financial Statements"}]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "statements": {}}


def get_earnings_history(ticker: str) -> dict:
    """최근 EPS 서프라이즈 이력 — yfinance earnings_dates 사용"""
    try:
        t = yf.Ticker(ticker)

        # yfinance의 earnings_dates가 가장 안정적
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return {"ticker": ticker, "history": [], "consecutive_beats": 0}

        history = []
        for idx, row in ed.iterrows():
            reported = row.get("Reported EPS")
            estimated = row.get("EPS Estimate")
            surprise = row.get("Surprise(%)")

            # nan 처리
            import math
            def safe(v):
                try:
                    return None if (v is None or math.isnan(float(v))) else float(v)
                except Exception:
                    return None

            history.append({
                "fiscal_date":   str(idx.date()),
                "reported_eps":  safe(reported),
                "estimated_eps": safe(estimated),
                "surprise_pct":  safe(surprise),
            })

        # 연속 beat 계산 (최신부터)
        beats = 0
        for h in history:
            sp = h.get("surprise_pct")
            if sp is not None and sp > 0:
                beats += 1
            else:
                break

        valid = [h for h in history if h.get("surprise_pct") is not None]
        avg_surprise = (
            round(sum(h["surprise_pct"] for h in valid) / len(valid), 2)
            if valid else None
        )

        return {
            "ticker": ticker,
            "history": history[:8],
            "consecutive_beats": beats,
            "avg_surprise_pct":  avg_surprise,
            "sources": [{"type": "api", "tool": "yfinance",
                         "title": f"{ticker} Earnings History"}]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "history": [],
                "consecutive_beats": 0}


def get_earnings_calendar(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        info = t.info

        # info에서 직접 가져오는 게 더 안정적
        next_date = None
        cal = t.calendar
        if cal is not None and "Earnings Date" in cal:
            dates = cal["Earnings Date"]
            if hasattr(dates, "__iter__"):
                next_date = str(list(dates)[0])
            else:
                next_date = str(dates)

        return {
            "ticker":             ticker,
            "next_earnings_date": next_date,
            "forward_eps":        info.get("forwardEps"),
            "trailing_eps":       info.get("trailingEps"),
            "sources": [{"type": "api", "tool": "yfinance",
                         "title": f"{ticker} Earnings Calendar"}]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "next_earnings_date": None}


def get_insider_trades(ticker: str, limit: int = 10) -> dict:
    try:
        t = yf.Ticker(ticker)
        insider = t.insider_transactions

        if insider is None or insider.empty:
            return {"ticker": ticker, "transactions": []}

        transactions = []
        for _, row in insider.head(limit).iterrows():
            transactions.append({
                "date":             str(row.get("Start Date", "")),
                "name":             str(row.get("Filer Name", "")),
                "title":            str(row.get("Filer Relation", "")),
                "transaction_type": str(row.get("Transaction", "")),
                "shares":           float(row["Shares"]) if row.get("Shares") else None,
                "value_usd":        float(row["Value"]) if row.get("Value") else None,
            })

        return {
            "ticker": ticker,
            "transactions": transactions,
            "sources": [{"type": "sec", "tool": "yfinance/edgar",
                         "title": f"{ticker} Insider Transactions (Form 4)"}]
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "transactions": []}