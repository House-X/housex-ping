"""Historical test of the scanner's long-only rules on daily bars.

Rules (the same ones scanner.technical_score uses today, computed for every past bar):
  - signal when score >= min_score and the setup is one of `setups`, judged on the bar's close
  - enter at the NEXT bar's open (no look-ahead)
  - stop = entry - atr_mult * ATR14, target = entry + reward_risk * (entry - stop)
  - position sized to risk `risk_pct` of equity, capped at `max_position_pct` of equity, no leverage
  - one position per symbol at a time; if a bar touches both stop and target, assume the stop
    (conservative); gaps through the stop exit at the open
  - fees charged on both sides

Not simulated: news, the AI's judgement, Sharia filtering, slippage beyond fees. Past results
do not guarantee future ones - treat this as a sanity check of the rules, not a forecast.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from . import market_data
from .indicators import adx, atr, ema, macd, rsi

SETUP_BREAKOUT = "breakout / new highs"
SETUP_PULLBACK = "pullback to EMA20 in uptrend"
SETUP_CONTINUATION = "trend continuation"
SETUP_REVERSAL = "early reversal (above EMA50, fresh MACD turn)"
DEFAULT_SETUPS = (SETUP_BREAKOUT, SETUP_PULLBACK, SETUP_REVERSAL)


def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised twin of scanner.technical_score (without relative strength) for every bar."""
    c, h, lo, v = df["close"], df["high"], df["low"], df["volume"]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c)
    a = atr(df)
    ad = adx(df)
    _, _, hist = macd(c)
    hi55 = h.rolling(55, min_periods=1).max()
    vavg = v.rolling(20, min_periods=1).mean()
    vol_ratio = (v / vavg).where(vavg > 0, 0.0)

    uptrend = (c > e50) & (e50 > e200)
    score = (15 * (c > e50) + 15 * (e50 > e200) + 10 * (c > e200)
             + np.select([(r >= 50) & (r <= 70), (r >= 40) & (r < 50), r > 75], [15, 5, -5], 0)
             + 5 * (ad > 20)).astype(float)

    breakout = (c >= 0.98 * hi55) & uptrend
    pullback = ~breakout & uptrend & ((lo - e20).abs() <= a) & (r >= 40) & (r <= 58)
    continuation = ~breakout & ~pullback & uptrend
    reversal = ~uptrend & (c > e50) & (e50 <= e200) & (hist > 0) & (hist.shift(4) < 0)

    score += np.select([breakout, pullback, continuation, reversal],
                       [15 + 5 * (vol_ratio > 1.5), 15, 5, 10], 0)
    setup = np.select([breakout, pullback, continuation, reversal],
                      [SETUP_BREAKOUT, SETUP_PULLBACK, SETUP_CONTINUATION, SETUP_REVERSAL], "none")
    return pd.DataFrame({"score": score.clip(0, 100), "setup": setup, "atr": a}, index=df.index)


def run(df: pd.DataFrame, min_score: int = 70, setups: tuple = DEFAULT_SETUPS,
        reward_risk: float = 2.0, atr_mult: float = 2.0, risk_pct: float = 1.0,
        max_position_pct: float = 30.0, fee: float = 0.001, start_equity: float = 10_000,
        warmup: int = 200) -> dict:
    sig = score_frame(df)
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = df.index
    equity, cash = start_equity, start_equity
    pos = None
    trades, curve = [], []

    for i in range(warmup, len(df)):
        # 1) manage an open position on this bar
        if pos is not None:
            exit_px = reason = None
            if o[i] <= pos["stop"]:
                exit_px, reason = o[i], "stop (gap)"
            elif lo[i] <= pos["stop"]:
                exit_px, reason = pos["stop"], "stop"
            elif o[i] >= pos["target"]:
                exit_px, reason = o[i], "target (gap)"
            elif h[i] >= pos["target"]:
                exit_px, reason = pos["target"], "target"
            if exit_px is not None:
                proceeds = pos["units"] * exit_px * (1 - fee)
                cash += proceeds
                pnl = proceeds - pos["cost"]
                trades.append({"entry_date": pos["date"], "exit_date": idx[i], "setup": pos["setup"],
                               "entry": pos["entry"], "exit": float(exit_px), "reason": reason,
                               "pnl": float(pnl), "r": float(pnl / pos["risk"])})
                pos = None

        # 2) open a new position at this bar's open if the previous bar signalled
        if pos is None and i > warmup:
            prev = sig.iloc[i - 1]
            if prev["score"] >= min_score and prev["setup"] in setups and not math.isnan(prev["atr"]):
                entry = o[i]
                stop = entry - atr_mult * prev["atr"]
                if stop > 0:
                    risk_amount = equity * risk_pct / 100
                    units = min(risk_amount / (entry - stop), equity * max_position_pct / 100 / entry,
                                cash / (entry * (1 + fee)))
                    if units > 0:
                        cost = units * entry * (1 + fee)
                        cash -= cost
                        pos = {"date": idx[i], "setup": prev["setup"], "entry": entry, "stop": stop,
                               "target": entry + reward_risk * (entry - stop), "units": units,
                               "cost": cost, "risk": units * (entry - stop)}
                        # the entry bar itself can already hit the stop or target
                        if lo[i] <= stop or h[i] >= pos["target"]:
                            hit_stop = lo[i] <= stop
                            exit_px = stop if hit_stop else pos["target"]
                            proceeds = units * exit_px * (1 - fee)
                            cash += proceeds
                            pnl = proceeds - cost
                            trades.append({"entry_date": idx[i], "exit_date": idx[i], "setup": prev["setup"],
                                           "entry": entry, "exit": exit_px,
                                           "reason": "stop" if hit_stop else "target",
                                           "pnl": pnl, "r": pnl / pos["risk"]})
                            pos = None

        equity = cash + (pos["units"] * c[i] if pos else 0.0)
        curve.append((idx[i], equity))

    if pos is not None:  # mark the open position at the last close
        trades.append({"entry_date": pos["date"], "exit_date": idx[-1], "setup": pos["setup"],
                       "entry": pos["entry"], "exit": c[-1], "reason": "open at end",
                       "pnl": pos["units"] * c[-1] * (1 - fee) - pos["cost"],
                       "r": (pos["units"] * c[-1] * (1 - fee) - pos["cost"]) / pos["risk"]})

    eq = pd.Series([e for _, e in curve], index=[d for d, _ in curve], dtype=float)
    return {"stats": _stats(trades, eq, start_equity, c[warmup] if len(c) > warmup else np.nan, c[-1]),
            "trades": trades, "equity_curve": eq}


def _stats(trades: list[dict], eq: pd.Series, start: float, first_close: float, last_close: float) -> dict:
    rs = [t["r"] for t in trades]
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9) if len(eq) > 1 else 0
    final = float(eq.iloc[-1]) if len(eq) else start
    dd = float(((eq / eq.cummax()) - 1).min() * 100) if len(eq) else 0.0
    in_market = sum((t["exit_date"] - t["entry_date"]).days + 1 for t in trades)
    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_r": round(float(np.mean(rs)), 2) if rs else None,
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
        "total_return_pct": round((final / start - 1) * 100, 1),
        "cagr_pct": round(((final / start) ** (1 / years) - 1) * 100, 1) if years >= 0.5 else None,
        "max_drawdown_pct": round(dd, 1),
        "buy_and_hold_pct": round(float(last_close / first_close - 1) * 100, 1)
                            if first_close and not np.isnan(first_close) else None,
        "time_in_market_pct": round(min(in_market / max(len(eq), 1) * 100, 100), 1),
        "years": round(years, 1),
    }


def backtest(symbols: list[str], years: int = 4, fetch: Callable | None = None, **params) -> dict:
    fetch = fetch or market_data.fetch_daily_history
    results, all_trades = {}, []
    for s in symbols:
        try:
            df = fetch(s, years)
            if len(df) < 260:
                results[s] = {"error": f"only {len(df)} daily bars - need at least 260"}
                continue
            r = run(df, **params)
            results[s] = {"stats": r["stats"], "last_trades": _fmt(r["trades"][-5:])}
            all_trades += r["trades"]
        except Exception as e:
            results[s] = {"error": f"{type(e).__name__}: {e}"}
    rs = [t["r"] for t in all_trades]
    return {
        "rules": {"min_score": params.get("min_score", 70), "reward_risk": params.get("reward_risk", 2.0),
                  "atr_mult": params.get("atr_mult", 2.0), "years": years,
                  "setups": list(params.get("setups", DEFAULT_SETUPS))},
        "aggregate": {"trades": len(all_trades),
                      "win_rate_pct": round(sum(t["pnl"] > 0 for t in all_trades) / len(all_trades) * 100, 1)
                      if all_trades else None,
                      "avg_r": round(float(np.mean(rs)), 2) if rs else None},
        "per_symbol": results,
        "caveats": "Rules only: no news, AI judgement or Sharia filter; fees 0.1%/side; past results "
                   "are not a forecast. Few trades (<30) means the numbers are not statistically reliable.",
    }


def _fmt(trades: list[dict]) -> list[dict]:
    return [{**t, "entry_date": str(t["entry_date"])[:10], "exit_date": str(t["exit_date"])[:10],
             "entry": round(t["entry"], 6), "exit": round(t["exit"], 6), "pnl": round(t["pnl"], 2),
             "r": round(t["r"], 2)} for t in trades]
