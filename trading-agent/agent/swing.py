"""Swing / trend-riding strategies with a trailing stop, tested the same honest way as shortterm.py.

The short-term lab showed small fixed targets cut winners short and fees eat the rest. Trend
following does the opposite: few trades, no fixed target, and a stop that trails the price up so a
winner can run for weeks while a falling market is exited early. Long-only spot, the whole swing
budget in one trade at a time, no leverage.

  breakout  close above the highest high of the last N bars, above the 200-bar average
  ema_cross fast EMA crosses above slow EMA, above the 200-bar average

Exit: chandelier trailing stop = highest close since entry - k x ATR(14), raised at every close,
never lowered. Entry at the next bar's open; a gap through the stop exits at the open.

Judged on the last 30% of history (parameters chosen on the first 70%), and also shown over the
whole history so bear markets (2018, 2022) are included. The question is not "does it beat
buy-and-hold in a bull market" (it usually won't) but "does it keep most of the upside while
avoiding the big crashes".
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from . import market_data
from .backtest import _stats
from .indicators import atr, ema

STRATEGIES_AR = {
    "breakout": "اختراق القمة مع وقف متحرك",
    "ema_cross": "تقاطع المتوسطات مع وقف متحرك",
}
GRIDS = {
    "breakout": [{"lookback": n, "trail_atr": k} for n in (20, 55) for k in (2.5, 3.5, 4.5)],
    "ema_cross": [{"fast": f, "slow": s, "trail_atr": k} for f, s in ((10, 30), (20, 50)) for k in (2.5, 3.5, 4.5)],
}
FEE, SLIPPAGE = 0.001, 0.0005
WARMUP = 210
MIN_TRADES = 6


def signals(df: pd.DataFrame, strategy: str, p: dict) -> pd.Series:
    c = df["close"]
    above = c > ema(c, 200)
    if strategy == "breakout":
        trig = c > df["high"].shift(1).rolling(p["lookback"]).max()
    elif strategy == "ema_cross":
        fast, slow = ema(c, p["fast"]), ema(c, p["slow"])
        trig = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    else:
        raise ValueError(f"unknown strategy {strategy}")
    return (above & trig).fillna(False)


def simulate(df: pd.DataFrame, entries: pd.Series, trail_atr: float = 3.5, fee: float = FEE,
             slippage: float = SLIPPAGE, start_equity: float = 10_000, warmup: int = WARMUP) -> dict:
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(df, 14).to_numpy(float)
    sig = entries.to_numpy(bool)
    idx = df.index
    cash = start_equity
    pos, trades, curve, bars_in = None, [], [], 0

    def close(i, px, reason):
        nonlocal cash, pos
        proceeds = pos["units"] * px * (1 - slippage) * (1 - fee)
        cash += proceeds
        pnl = proceeds - pos["cost"]
        trades.append({"entry_date": pos["date"], "exit_date": idx[i], "entry": pos["entry"],
                       "exit": float(px), "reason": reason, "bars": i - pos["i"] + 1,
                       "pnl": float(pnl), "r": float(pnl / pos["risk"]) if pos["risk"] else 0.0,
                       "return_pct": float(pnl / pos["cost"] * 100)})
        pos = None

    for i in range(warmup, len(df)):
        if pos is None and i > warmup and sig[i - 1] and not np.isnan(a[i - 1]):
            entry = o[i] * (1 + slippage)
            stop = entry - trail_atr * a[i - 1]
            units = cash / (entry * (1 + fee))
            if stop > 0 and units > 0:
                pos = {"i": i, "date": idx[i], "entry": entry, "stop": stop, "units": units,
                       "cost": cash, "risk": units * (entry - stop), "peak": entry}
                cash = 0.0
        if pos is not None:
            bars_in += 1
            if pos["i"] < i and o[i] <= pos["stop"]:
                close(i, o[i], "trailing stop (gap)")
            elif lo[i] <= pos["stop"]:
                close(i, pos["stop"], "trailing stop")
            else:  # raise the stop at the close, never lower it
                pos["peak"] = max(pos["peak"], c[i])
                if not np.isnan(a[i]):
                    pos["stop"] = max(pos["stop"], pos["peak"] - trail_atr * a[i])
        curve.append(cash + (pos["units"] * c[i] if pos else 0.0))

    if pos is not None:
        close(len(df) - 1, c[-1], "open at end")
        curve[-1] = cash
    eq = pd.Series(curve, index=idx[warmup:warmup + len(curve)], dtype=float)
    stats = _stats(trades, eq, start_equity, c[warmup] if len(c) > warmup else np.nan, c[-1])
    hold = df["close"].iloc[warmup:]
    bh_dd = float(((hold / hold.cummax()) - 1).min() * 100) if len(hold) else 0.0
    stats.update({
        "time_in_market_pct": round(bars_in / max(len(eq), 1) * 100, 1),
        "buy_and_hold_max_drawdown_pct": round(bh_dd, 1),
        "avg_hold_bars": round(float(np.mean([t["bars"] for t in trades])), 1) if trades else None,
        "best_trade_pct": round(max(t["return_pct"] for t in trades), 1) if trades else None,
        "worst_trade_pct": round(min(t["return_pct"] for t in trades), 1) if trades else None,
    })
    return {"stats": stats, "trades": trades, "equity_curve": eq}


def _score(st: dict) -> float:
    """Training rank: return per unit of drawdown (keeps the upside AND avoids crashes)."""
    if st["trades"] < 4:
        return -1e9
    return st["total_return_pct"] / max(abs(st["max_drawdown_pct"]), 5.0)


def verdict(st: dict) -> tuple[str, str]:
    n, pf, ret = st["trades"], st["profit_factor"], st["total_return_pct"]
    dd, bh_dd = abs(st["max_drawdown_pct"]), abs(st["buy_and_hold_max_drawdown_pct"])
    if n < MIN_TRADES:
        return "insufficient", "صفقات قليلة في فترة الاختبار — النتيجة إرشادية فقط"
    if ret > 0 and (pf or 0) >= 1.5 and dd <= 0.7 * bh_dd:
        return "promising", "واعدة: ربحت على بيانات لم ترها وبهبوط أقل بكثير من الاحتفاظ"
    if ret > 0 and (pf or 0) >= 1.2:
        return "weak", "مقبولة: ربح على بيانات لم ترها لكن الحماية من الهبوط محدودة"
    return "fails", "لا تعمل: لم تربح على بيانات لم ترها"


def evaluate(df: pd.DataFrame, strategy: str, split: float = 0.7) -> dict:
    cut = int(len(df) * split)
    train_df = df.iloc[:cut]
    best, best_score = GRIDS[strategy][0], -2e9
    for p in GRIDS[strategy]:
        sc = _score(simulate(train_df, signals(train_df, strategy, p), p["trail_atr"])["stats"])
        if sc > best_score:
            best, best_score = p, sc
    entries = signals(df, strategy, best)
    test_df = df.iloc[cut - WARMUP:]
    test = simulate(test_df, entries.iloc[cut - WARMUP:], best["trail_atr"])
    whole = simulate(df, entries, best["trail_atr"])
    code, text = verdict(test["stats"])
    fmt = lambda ts: [{**t, "entry_date": str(t["entry_date"])[:10], "exit_date": str(t["exit_date"])[:10],  # noqa: E731
                       "return_pct": round(t["return_pct"], 1), "pnl": round(t["pnl"], 2)} for t in ts]
    return {"strategy": strategy, "strategy_ar": STRATEGIES_AR[strategy], "params": best,
            "test": test["stats"], "whole": whole["stats"], "verdict": code, "verdict_ar": text,
            "test_from": str(df.index[cut])[:10], "test_to": str(df.index[-1])[:10],
            "whole_from": str(df.index[WARMUP])[:10],
            "equity_whole": whole["equity_curve"], "last_trades": fmt(whole["trades"][-8:])}


def _fetch(symbol: str, timeframe: str, years: int) -> pd.DataFrame:
    if timeframe == "1d":
        return market_data.fetch_daily_history(symbol, years)
    return market_data.fetch_intraday_history(symbol, timeframe, int(years * 365))


def research(symbols: list[str], timeframe: str = "1d", years: int = 8,
             fetch: Callable | None = None) -> dict:
    fetch = fetch or _fetch
    out = {}
    for s in symbols:
        try:
            df = fetch(s, timeframe, years)
            if len(df) < WARMUP * 3:
                out[s] = {"error": f"only {len(df)} bars - need at least {WARMUP * 3}"}
                continue
            out[s] = {"bars": len(df), "results": [evaluate(df, st) for st in STRATEGIES_AR]}
        except Exception as e:
            out[s] = {"error": f"{type(e).__name__}: {e}"}
    promising = [f"{s} · {r['strategy_ar']}" for s, v in out.items() for r in v.get("results", [])
                 if r["verdict"] == "promising"]
    return {"timeframe": timeframe, "years": years, "per_symbol": out, "promising": promising,
            "rules": f"long only, whole swing budget per trade, no leverage, fees {FEE * 100:.1f}%/side + "
                     f"slippage {SLIPPAGE * 100:.2f}%, chandelier trailing stop, params chosen on the "
                     "first 70%, judged on the last 30%"}
