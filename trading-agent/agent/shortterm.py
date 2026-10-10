"""Short-term (intraday) spot strategies and an honest test of them before any money is used.

Long-only, cash-funded, one position at a time. Three classic rule sets, all only trading WITH the
bigger trend (price above its 200-bar average), because buying dips in a falling market is how
short-term traders lose:

  pullback  RSI(14) dips below 35 and turns back up            -> buy the dip in an uptrend
  breakout  close above the highest high of the last 24 bars,  -> ride a range break
            on volume >= 1.5x its 20-bar average
  bounce    close back inside the lower Bollinger band          -> snap-back from an over-stretch

Every trade: entry at the NEXT bar's open, stop = entry - 1.5 x ATR(14), target = entry + RR x risk,
plus a time stop (exit at the close after N bars - short-term trades must work quickly). Fees
(0.1% per side on Binance) and slippage are charged on every fill, because on small targets
costs decide whether a strategy wins.

Honesty against curve-fitting: parameters are chosen on the first 70% of the history only
("training"), then judged on the last 30% that the choice never saw ("test"). Only the test
result counts for the verdict.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from . import market_data
from .backtest import _stats
from .indicators import atr, bollinger, ema, rsi

STRATEGIES_AR = {
    "pullback": "شراء التراجع داخل اتجاه صاعد",
    "breakout": "اختراق قمة النطاق مع حجم تداول",
    "bounce": "ارتداد من أسفل نطاق بولينجر",
}
GRID = [(rr, ts) for rr in (1.0, 1.5, 2.0) for ts in (12, 24, 48)]
STOP_ATR = 1.5
FEE, SLIPPAGE = 0.001, 0.0002
MIN_TEST_TRADES = 20
WARMUP = 210


def signals(df: pd.DataFrame, strategy: str) -> pd.Series:
    """True on the bar whose CLOSE triggers an entry (the trade opens at the next bar's open)."""
    c = df["close"]
    uptrend = (c > ema(c, 200)) & (ema(c, 50) > ema(c, 200))
    if strategy == "pullback":
        r = rsi(c, 14)
        trig = (r.shift(1) < 35) & (r >= 35)
    elif strategy == "breakout":
        prior_high = df["high"].shift(1).rolling(24).max()
        trig = (c > prior_high) & (df["volume"] >= 1.5 * df["volume"].rolling(20).mean())
    elif strategy == "bounce":
        _, _, lower = bollinger(c, 20, 2.0)
        trig = (c.shift(1) < lower.shift(1)) & (c > lower)
    else:
        raise ValueError(f"unknown strategy {strategy}")
    return (uptrend & trig).fillna(False)


def simulate(df: pd.DataFrame, entries: pd.Series, reward_risk: float = 1.5, time_stop: int = 24,
             risk_pct: float = 1.0, max_position_pct: float = 30.0, fee: float = FEE,
             slippage: float = SLIPPAGE, start_equity: float = 10_000, warmup: int = WARMUP) -> dict:
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(df, 14).to_numpy(float)
    sig = entries.to_numpy(bool)
    idx = df.index
    cash = equity = start_equity
    pos, trades, curve, fees_paid, bars_in = None, [], [], 0.0, 0

    def close(i, px, reason):
        nonlocal cash, pos, fees_paid
        fill = px * (1 - slippage)
        proceeds = pos["units"] * fill * (1 - fee)
        fees_paid += pos["units"] * fill * fee
        cash += proceeds
        pnl = proceeds - pos["cost"]
        trades.append({"entry_date": pos["date"], "exit_date": idx[i], "entry": pos["entry"],
                       "exit": float(fill), "reason": reason, "bars": i - pos["i"] + 1,
                       "pnl": float(pnl), "r": float(pnl / pos["risk"])})
        pos = None

    for i in range(warmup, len(df)):
        if pos is None and i > warmup and sig[i - 1] and not math.isnan(a[i - 1]):
            entry = o[i] * (1 + slippage)
            stop = entry - STOP_ATR * a[i - 1]
            if stop > 0:
                units = min(equity * risk_pct / 100 / (entry - stop),
                            equity * max_position_pct / 100 / entry, cash / (entry * (1 + fee)))
                if units > 0:
                    cost = units * entry * (1 + fee)
                    fees_paid += units * entry * fee
                    cash -= cost
                    pos = {"i": i, "date": idx[i], "entry": entry, "stop": stop, "units": units,
                           "target": entry + reward_risk * (entry - stop), "cost": cost,
                           "risk": units * (entry - stop)}
        if pos is not None:
            bars_in += 1
            if pos["i"] < i and o[i] <= pos["stop"]:
                close(i, o[i], "stop (gap)")
            elif lo[i] <= pos["stop"]:              # stop first when both are touched: conservative
                close(i, pos["stop"], "stop")
            elif pos["i"] < i and o[i] >= pos["target"]:
                close(i, o[i], "target (gap)")
            elif h[i] >= pos["target"]:
                close(i, pos["target"], "target")
            elif i - pos["i"] + 1 >= time_stop:
                close(i, c[i], "time")
        equity = cash + (pos["units"] * c[i] if pos else 0.0)
        curve.append(equity)

    if pos is not None:
        close(len(df) - 1, c[-1], "open at end")
        curve[-1] = cash
    eq = pd.Series(curve, index=idx[warmup:warmup + len(curve)], dtype=float)
    stats = _stats(trades, eq, start_equity, c[warmup] if len(c) > warmup else np.nan, c[-1])
    days = max((idx[-1] - idx[warmup]).total_seconds() / 86_400, 1) if len(idx) > warmup else 1
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    stats.update({
        "time_in_market_pct": round(bars_in / max(len(eq), 1) * 100, 1),
        "trades_per_week": round(len(trades) / days * 7, 1),
        "avg_hold_bars": round(float(np.mean([t["bars"] for t in trades])), 1) if trades else None,
        "fees_usd": round(fees_paid, 2),
        "fees_vs_gross_profit_pct": round(fees_paid / gross_profit * 100, 1) if gross_profit else None,
    })
    return {"stats": stats, "trades": trades, "equity_curve": eq}


def _score(st: dict) -> float:
    """How training results are ranked: profit factor, but only with enough trades to mean anything."""
    if st["trades"] < 10 or st["profit_factor"] is None:
        return -1.0
    return st["profit_factor"]


def verdict(test: dict) -> tuple[str, str]:
    pf, n, ret = test.get("profit_factor"), test["trades"], test["total_return_pct"]
    if n < MIN_TEST_TRADES:
        return "insufficient", "صفقات قليلة جداً في فترة الاختبار — النتيجة غير موثوقة"
    if pf and pf >= 1.3 and ret > 0:
        return "promising", "واعدة: ربحت على بيانات لم ترها — تستحق تجربة على الحساب التجريبي"
    if pf and pf >= 1.0 and ret > 0:
        return "weak", "ضعيفة: ربح هامشي بالكاد يغطي الرسوم — لا تستحق المخاطرة"
    return "fails", "لا تعمل: خسرت بعد الرسوم على بيانات لم ترها"


def evaluate(df: pd.DataFrame, strategy: str, split: float = 0.7) -> dict:
    """Pick (reward/risk, time stop) on the training part, report the untouched test part."""
    entries = signals(df, strategy)
    cut = int(len(df) * split)
    train_df, train_sig = df.iloc[:cut], entries.iloc[:cut]
    best, best_score = GRID[0], -2.0
    for rr, ts in GRID:
        sc = _score(simulate(train_df, train_sig, rr, ts)["stats"])
        if sc > best_score:
            best, best_score = (rr, ts), sc
    rr, ts = best
    train = simulate(train_df, train_sig, rr, ts)["stats"]
    # the test part keeps WARMUP bars of history before it so indicators are already warmed up
    test_df, test_sig = df.iloc[cut - WARMUP:], entries.iloc[cut - WARMUP:]
    test_run = simulate(test_df, test_sig, rr, ts)
    code, text = verdict(test_run["stats"])
    return {"strategy": strategy, "strategy_ar": STRATEGIES_AR[strategy],
            "params": {"reward_risk": rr, "time_stop_bars": ts, "stop_atr": STOP_ATR},
            "train": train, "test": test_run["stats"], "verdict": code, "verdict_ar": text,
            "test_from": str(df.index[cut])[:10], "test_to": str(df.index[-1])[:10],
            "last_trades": [{**t, "entry_date": str(t["entry_date"])[:16], "exit_date": str(t["exit_date"])[:16],
                             "pnl": round(t["pnl"], 2), "r": round(t["r"], 2)}
                            for t in test_run["trades"][-5:]]}


def research(symbols: list[str], timeframe: str = "1h", days: int = 365,
             strategies: tuple = tuple(STRATEGIES_AR), fetch: Callable | None = None) -> dict:
    fetch = fetch or market_data.fetch_intraday_history
    out = {}
    for s in symbols:
        try:
            df = fetch(s, timeframe, days)
            if len(df) < WARMUP * 3:
                out[s] = {"error": f"only {len(df)} bars - need at least {WARMUP * 3}"}
                continue
            out[s] = {"bars": len(df), "results": [evaluate(df, st) for st in strategies]}
        except Exception as e:
            out[s] = {"error": f"{type(e).__name__}: {e}"}
    promising = [f"{s} · {r['strategy_ar']}" for s, v in out.items() for r in v.get("results", [])
                 if r["verdict"] == "promising"]
    return {"timeframe": timeframe, "days": days, "per_symbol": out, "promising": promising,
            "rules": f"long only, no leverage, fees {FEE * 100:.1f}%/side + slippage "
                     f"{SLIPPAGE * 100:.2f}%, stop {STOP_ATR}xATR, params chosen on the first 70%, "
                     "judged on the last 30%"}
