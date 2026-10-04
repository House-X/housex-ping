"""Opportunity scanners: Borsa Istanbul stocks and Binance spot crypto.

Long-only by design (no shorting): every setup is a buy setup or nothing. Scores are a ranking
aid for the agent, not signals to trade blindly - the agent still runs a full analysis.
"""
from __future__ import annotations

import json
from typing import Callable

import pandas as pd

from . import market_data
from .config import ROOT
from .indicators import adx, atr, ema, macd, rsi

STABLE_BASES = {"USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "EUR", "USDE", "PYUSD", "AEUR",
                "EURI", "XUSD", "USD1", "BFUSD", "TRY", "BRL"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def technical_score(df: pd.DataFrame, benchmark: pd.Series | None = None) -> dict:
    """Score a daily chart 0-100 for long setups and label the setup type."""
    c, h, v = df["close"], df["high"], df["volume"]
    price = float(c.iloc[-1])
    e20, e50 = ema(c, 20), ema(c, 50)
    e200 = ema(c, 200) if len(c) >= 200 else ema(c, len(c) // 2)
    r = float(rsi(c).iloc[-1])
    a = float(atr(df).iloc[-1])
    ad = float(adx(df).iloc[-1])
    _, _, hist = macd(c)
    hi55 = float(h.tail(55).max())
    vol_ratio = float(v.iloc[-1] / v.tail(20).mean()) if v.tail(20).mean() > 0 else 0.0

    def ret(n):
        return float(c.iloc[-1] / c.iloc[-n - 1] - 1) if len(c) > n else None

    uptrend = price > e50.iloc[-1] > e200.iloc[-1]
    score, notes = 0, []
    if price > e50.iloc[-1]:
        score += 15
    if e50.iloc[-1] > e200.iloc[-1]:
        score += 15
    if price > e200.iloc[-1]:
        score += 10
    if 50 <= r <= 70:
        score += 15
    elif 40 <= r < 50:
        score += 5
    elif r > 75:
        score -= 5
        notes.append("RSI overheated")
    if ad > 20:
        score += 5

    rs = None
    if benchmark is not None and len(c) > 63:
        bench = benchmark.reindex(c.index, method="ffill")
        if bench.notna().iloc[-64:].all():
            rs = ret(63) - float(bench.iloc[-1] / bench.iloc[-64] - 1)
            if rs > 0:
                score += 15

    setup = "none"
    if price >= 0.98 * hi55 and uptrend:
        setup = "breakout / new highs"
        score += 15 + (5 if vol_ratio > 1.5 else 0)
    elif uptrend and abs(float(df["low"].iloc[-1]) - e20.iloc[-1]) <= a and 40 <= r <= 58:
        setup = "pullback to EMA20 in uptrend"
        score += 15
    elif uptrend:
        setup = "trend continuation"
        score += 5
    elif price > e50.iloc[-1] and e50.iloc[-1] <= e200.iloc[-1] and hist.iloc[-1] > 0 > hist.iloc[-5]:
        setup = "early reversal (above EMA50, fresh MACD turn)"
        score += 10

    return {
        "score": int(max(0, min(100, score))), "setup": setup, "price": round(price, 6),
        "rsi14": round(r, 1), "adx": round(ad, 1), "atr_pct": round(a / price * 100, 2),
        "return_1m_pct": round(ret(21) * 100, 1) if ret(21) is not None else None,
        "return_3m_pct": round(ret(63) * 100, 1) if ret(63) is not None else None,
        "rel_strength_3m_pct": round(rs * 100, 1) if rs is not None else None,
        "from_55d_high_pct": round((price / hi55 - 1) * 100, 1),
        "volume_vs_20d": round(vol_ratio, 2), "notes": notes,
    }


# ── Borsa Istanbul ─────────────────────────────────────────────

def bist_universe() -> list[str]:
    data = json.loads((ROOT / "universes" / "bist.json").read_text())
    return [t if t.endswith(".IS") else f"{t}.IS" for t in data["tickers"]]


def scan_bist(top_n: int = 10, include_review: bool = False, min_score: int = 50,
              fetch_many: Callable = market_data.fetch_daily_many,
              screen: Callable | None = None) -> dict:
    from .sharia import check as sharia_check
    screen = screen or sharia_check

    tickers = bist_universe()
    data = fetch_many(tickers + ["XU100.IS", "USDTRY=X"])
    index = data.get("XU100.IS", pd.DataFrame()).get("close")
    usdtry = data.get("USDTRY=X", pd.DataFrame()).get("close")

    rows = []
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        traded_value = float((df["close"] * df["volume"]).tail(20).mean())
        if traded_value < 50e6:  # skip illiquid names (< 50M TRY/day)
            continue
        s = technical_score(df, index)
        if s["score"] < min_score:
            continue
        # TRY returns flatter in a high-inflation economy; judge real performance in USD too.
        if usdtry is not None and s["return_3m_pct"] is not None and len(usdtry) > 63:
            fx = usdtry.reindex(df.index, method="ffill")
            s["return_3m_usd_pct"] = round(((1 + s["return_3m_pct"] / 100)
                                            * fx.iloc[-64] / fx.iloc[-1] - 1) * 100, 1)
        s["avg_daily_value_try_m"] = round(traded_value / 1e6, 1)
        rows.append({"symbol": t, **s})

    rows.sort(key=lambda x: x["score"], reverse=True)
    picks, excluded = [], []
    for row in rows:
        if len(picks) >= top_n:
            break
        sh = screen(row["symbol"])
        row["sharia"] = {k: sh.get(k) for k in ("status", "reasons", "purification_pct")}
        if sh["status"] == "compliant" or (include_review and sh["status"] == "review_required"):
            picks.append(row)
        else:
            excluded.append({"symbol": row["symbol"], "status": sh["status"], "reasons": sh["reasons"]})

    return {"market": "Borsa Istanbul", "scanned": len(tickers), "passed_technical": len(rows),
            "opportunities": picks, "excluded_by_sharia_screen": excluded,
            "index_3m_pct": round(float(index.iloc[-1] / index.iloc[-64] - 1) * 100, 1)
                            if index is not None and len(index) > 63 else None}


# ── Binance spot crypto ────────────────────────────────────────

def _spot_usdt_markets(ex) -> list[str]:
    ex.load_markets()
    out = []
    for sym, m in ex.markets.items():
        base = m.get("base", "")
        if (m.get("spot") and m.get("active") and m.get("quote") == "USDT" and ":" not in sym
                and base not in STABLE_BASES and not base.endswith(LEVERAGED_SUFFIXES)):
            out.append(sym)
    return out


def scan_crypto(mode: str = "established", top_n: int = 10, candidates: int = 60,
                exchange=None, fetch_ohlcv: Callable | None = None) -> dict:
    """mode: 'established' = liquid coins ranked by setup quality
             'new_listings' = coins listed on the exchange in the last ~120 days
             'trending'     = CoinGecko trending coins that trade on the exchange"""
    from .sharia import check as sharia_check
    from .sharia import universe

    ex = exchange or market_data._crypto_exchange()
    fetch = fetch_ohlcv or (lambda s, n: ex.fetch_ohlcv(s, "1d", limit=n))
    blocked = set(universe().get("crypto_blocked_bases", []))
    markets = [m for m in _spot_usdt_markets(ex) if m.split("/")[0] not in blocked]

    tickers = ex.fetch_tickers(markets)
    by_volume = sorted(markets, key=lambda s: (tickers.get(s) or {}).get("quoteVolume") or 0, reverse=True)

    if mode == "trending":
        from .crypto_research import trending
        wanted = {f"{t['symbol']}/USDT" for t in trending()}
        pool = [m for m in by_volume if m in wanted]
    elif mode == "new_listings":
        pool = by_volume[:max(candidates * 3, 150)]
    else:
        pool = by_volume[:candidates]

    def to_df(rows):
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("ts")

    btc = to_df(fetch("BTC/USDT", 300))["close"]
    bars = 130 if mode == "new_listings" else 300  # new listings only need a short history
    rows = []
    for sym in pool:
        try:
            df = to_df(fetch(sym, bars))
        except Exception:
            continue
        age_days = len(df)  # fewer bars than requested means it listed within the window
        if mode == "new_listings" and age_days > 120:
            continue
        if len(df) < 30:
            continue
        s = technical_score(df, btc if sym != "BTC/USDT" else None)
        t = tickers.get(sym) or {}
        sh = sharia_check(sym)
        rows.append({
            "symbol": sym, **s,
            "listed_days_ago": age_days if age_days < bars else f"{bars}+",
            "volume_24h_usd_m": round((t.get("quoteVolume") or 0) / 1e6, 1),
            "change_24h_pct": t.get("percentage"),
            "sharia": {"status": sh["status"], "tradable": sh["tradable"]},
        })
        if mode == "new_listings" and len(rows) >= candidates:
            break

    rows.sort(key=lambda x: x["score"], reverse=True)
    return {"exchange": ex.id, "mode": mode, "scanned": len(pool), "opportunities": rows[:top_n],
            "note": "Only coins approved in sharia_universe.json are tradable; research the rest "
                    "with research_crypto before proposing them for approval."}
