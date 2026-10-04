"""Technical indicators and a compact, model-friendly market snapshot."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return mid + k * sd, mid, mid - k * sd


def swing_levels(df: pd.DataFrame, window: int = 5, count: int = 4) -> dict:
    """Recent swing highs/lows (fractal pivots) as support/resistance candidates."""
    h, l = df["high"], df["low"]
    is_high = h == h.rolling(2 * window + 1, center=True).max()
    is_low = l == l.rolling(2 * window + 1, center=True).min()
    price = df["close"].iloc[-1]
    highs = sorted({round(v, 8) for v in h[is_high].tail(20)})
    lows = sorted({round(v, 8) for v in l[is_low].tail(20)})
    resistance = [v for v in highs if v > price][:count]
    support = [v for v in reversed(lows) if v < price][:count]
    return {"resistance_above": resistance, "support_below": support}


def _r(x: float, ref: float) -> float:
    """Round to a precision that suits the instrument's price scale."""
    if ref >= 1000:
        return round(float(x), 2)
    if ref >= 10:
        return round(float(x), 3)
    return round(float(x), 5)


def snapshot(df: pd.DataFrame) -> dict:
    """Summarise a timeframe into numbers + plain-language reads the model can reason on."""
    c = df["close"]
    price = float(c.iloc[-1])
    e20, e50, e200 = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1], ema(c, 200).iloc[-1]
    r = rsi(c).iloc[-1]
    m_line, m_sig, m_hist = macd(c)
    a = atr(df).iloc[-1]
    ad = adx(df).iloc[-1]
    bu, bm, bl = bollinger(c)

    if price > e50 > e200:
        trend = "uptrend"
    elif price < e50 < e200:
        trend = "downtrend"
    else:
        trend = "range/transition"

    recent = df.tail(20)
    return {
        "price": _r(price, price),
        "change_pct_last_20_bars": round((price / float(c.iloc[-21]) - 1) * 100, 2) if len(c) > 21 else None,
        "trend": trend,
        "trend_strength_adx": round(float(ad), 1),
        "ema20": _r(e20, price), "ema50": _r(e50, price), "ema200": _r(e200, price),
        "rsi14": round(float(r), 1),
        "rsi_read": "overbought" if r > 70 else "oversold" if r < 30 else "neutral",
        "macd_hist": _r(m_hist.iloc[-1], price),
        "macd_cross": ("bullish" if m_line.iloc[-1] > m_sig.iloc[-1] else "bearish")
                      + (" (fresh)" if np.sign(m_hist.iloc[-1]) != np.sign(m_hist.iloc[-3]) else ""),
        "atr14": _r(a, price),
        "atr_pct": round(float(a / price * 100), 3),
        "bollinger": {"upper": _r(bu.iloc[-1], price), "mid": _r(bm.iloc[-1], price),
                      "lower": _r(bl.iloc[-1], price)},
        "range_20_bars": {"high": _r(recent["high"].max(), price), "low": _r(recent["low"].min(), price)},
        "volume_vs_avg20": round(float(df["volume"].iloc[-1] / df["volume"].tail(20).mean()), 2)
                           if df["volume"].tail(20).mean() > 0 else None,
        **swing_levels(df),
        "last_bar_utc": df.index[-1].isoformat(),
    }
