"""Automatic Sharia screen for stocks (US and Borsa Istanbul), modelled on AAOIFI Standard No. 21.

Two layers:
  1. Business activity: the core business must be permissible.
  2. Financial ratios (AAOIFI uses market capitalisation as the denominator):
       interest-bearing debt         / market cap  < 30%
       cash + interest-bearing items / market cap  < 30%
       impermissible (interest) income / revenue   <  5%   -> purify this share of dividends/gains

Data comes from Yahoo Finance and is cached for a week. Results are a screen, not a fatwa: for
Turkish stocks the agent should also cross-check Borsa Istanbul's Katilim (participation) indices.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from .config import ROOT

CACHE_FILE = ROOT / "data" / "stock_screen_cache.json"
CACHE_TTL = 7 * 24 * 3600

DEBT_LIMIT = 0.30
CASH_LIMIT = 0.30
IMPURE_INCOME_LIMIT = 0.05

# Yahoo Finance industry names. Substring match, case-insensitive.
PROHIBITED_INDUSTRIES = (
    "banks", "insurance", "credit services", "mortgage finance", "financial conglomerates",
    "wineries", "distilleries", "brewers", "tobacco", "gambling", "casinos",
)
REVIEW_INDUSTRIES = (  # mixed activities: needs a human look even if ratios pass
    "capital markets", "asset management", "financial data", "shell companies",
    "aerospace & defense", "entertainment", "lodging", "broadcasting",
)


def _fetch_fundamentals(symbol: str) -> dict:
    import yfinance as yf

    from .market_data import fx_to_usd

    t = yf.Ticker(symbol)
    info = t.info or {}

    def row(df, *names):
        if df is None or df.empty:
            return None
        for n in names:
            if n in df.index:
                v = df.loc[n].dropna()
                if not v.empty:
                    return float(v.iloc[0])  # most recent fiscal year
        return None

    bs, inc = t.balance_sheet, t.income_stmt
    debt = row(bs, "Total Debt") or info.get("totalDebt")
    cash = row(bs, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents") \
        or info.get("totalCash")
    interest_income = row(inc, "Interest Income", "Interest Income Non Operating")
    revenue = row(inc, "Total Revenue", "Operating Revenue") or info.get("totalRevenue")

    # Statements can be reported in a different currency than the share price (market cap).
    price_ccy, fin_ccy = info.get("currency"), info.get("financialCurrency")
    if price_ccy and fin_ccy and price_ccy != fin_ccy:
        k = fx_to_usd(fin_ccy) / fx_to_usd(price_ccy)
        debt, cash = (debt * k if debt else debt), (cash * k if cash else cash)

    return {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "market_cap": info.get("marketCap"), "currency": price_ccy,
        "total_debt": debt, "cash_and_investments": cash,
        "interest_income": interest_income, "revenue": revenue,
    }


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def evaluate(symbol: str, f: dict) -> dict:
    """Pure screening logic on a fundamentals dict (testable without network)."""
    industry = (f.get("industry") or "").lower()
    reasons, warnings = [], []
    ratios = {}

    mcap = f.get("market_cap")
    if mcap:
        if f.get("total_debt") is not None:
            ratios["debt_to_market_cap"] = round(f["total_debt"] / mcap, 4)
        if f.get("cash_and_investments") is not None:
            ratios["cash_to_market_cap"] = round(f["cash_and_investments"] / mcap, 4)
    if f.get("interest_income") is not None and f.get("revenue"):
        ratios["impure_income_ratio"] = round(abs(f["interest_income"]) / f["revenue"], 4)

    status = "compliant"
    if any(k in industry for k in PROHIBITED_INDUSTRIES):
        status = "not_compliant"
        reasons.append(f"Prohibited core business: {f.get('industry')}")
    if ratios.get("debt_to_market_cap", 0) >= DEBT_LIMIT:
        status = "not_compliant"
        reasons.append(f"Interest-bearing debt {ratios['debt_to_market_cap']:.0%} of market cap (limit 30%)")
    if ratios.get("cash_to_market_cap", 0) >= CASH_LIMIT:
        status = "not_compliant"
        reasons.append(f"Cash/interest-bearing assets {ratios['cash_to_market_cap']:.0%} of market cap (limit 30%)")
    if ratios.get("impure_income_ratio", 0) >= IMPURE_INCOME_LIMIT:
        status = "not_compliant"
        reasons.append(f"Interest income {ratios['impure_income_ratio']:.1%} of revenue (limit 5%)")

    if status == "compliant":
        if not industry:
            status = "review_required"
            reasons.append("Business activity unknown - verify manually")
        elif "debt_to_market_cap" not in ratios or "cash_to_market_cap" not in ratios:
            status = "review_required"
            reasons.append("Missing balance-sheet data for the financial ratio screen")
        elif any(k in industry for k in REVIEW_INDUSTRIES):
            status = "review_required"
            reasons.append(f"Mixed-activity industry ({f.get('industry')}): ratios pass, business needs review")
        else:
            reasons.append("Passes business-activity and AAOIFI financial-ratio screens")
        if "impure_income_ratio" not in ratios:
            warnings.append("Interest income not reported - purification share could not be computed")

    return {
        "symbol": symbol.upper(), "status": status, "tradable": status == "compliant",
        "name": f.get("name"), "sector": f.get("sector"), "industry": f.get("industry"),
        "ratios": ratios, "reasons": reasons, "warnings": warnings,
        "purification_pct": round(ratios.get("impure_income_ratio", 0) * 100, 2),
        "method": "AAOIFI No. 21 (auto screen, Yahoo Finance data)",
    }


def screen(symbol: str, fetch: Callable[[str], dict] = _fetch_fundamentals,
           use_cache: bool = True) -> dict:
    key = symbol.upper()
    cache = _load_cache() if use_cache else {}
    hit = cache.get(key)
    if hit and time.time() - hit["ts"] < CACHE_TTL:
        return hit["result"]
    try:
        result = evaluate(key, fetch(key))
    except Exception as e:
        return {"symbol": key, "status": "review_required", "tradable": False,
                "reasons": [f"Could not fetch fundamentals: {e}"], "warnings": [],
                "ratios": {}, "purification_pct": None}
    if use_cache:
        cache[key] = {"ts": time.time(), "result": result}
        _save_cache(cache)
    return result
