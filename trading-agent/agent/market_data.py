"""Market data: crypto via ccxt (public endpoints, no key), forex/metals/indices via yfinance.

Symbol conventions accepted from the agent:
  crypto : BTC/USDT, ETH/USDT, SOL/USDT ...  (anything containing "/" with a crypto quote)
  forex  : EURUSD, GBPJPY, USDTRY ...       (6 letters)
  other  : XAUUSD (gold), XAGUSD, US30, NAS100, SPX500, OIL, or any raw yfinance ticker
"""
from __future__ import annotations

import ccxt
import pandas as pd
import yfinance as yf

from .config import settings

CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "FDUSD")

YF_ALIASES = {
    "XAUUSD": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "SILVER": "SI=F",
    "OIL": "CL=F", "WTI": "CL=F", "BRENT": "BZ=F",
    "US30": "^DJI", "NAS100": "^NDX", "SPX500": "^GSPC", "GER40": "^GDAXI",
    "DXY": "DX-Y.NYB", "BIST100": "XU100.IS",
}

# yfinance interval / max lookback period
YF_TF = {
    "5m": ("5m", "30d"), "15m": ("15m", "30d"), "30m": ("30m", "30d"),
    "1h": ("60m", "180d"), "4h": ("60m", "360d"), "1d": ("1d", "5y"), "1w": ("1wk", "10y"),
}

_exchange: ccxt.Exchange | None = None


def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if "/" in s and s.split("/")[1].split(":")[0] in CRYPTO_QUOTES:
        return "crypto"
    if s in YF_ALIASES or s.startswith("^") or "=" in s:
        return "index_commodity"
    if len(s) == 6 and s.isalpha():
        return "forex"
    return "stock"


def _crypto_exchange() -> ccxt.Exchange:
    global _exchange
    if _exchange is None:
        _exchange = getattr(ccxt, settings.crypto_exchange)({"enableRateLimit": True})
    return _exchange


def _yf_ticker(symbol: str) -> str:
    s = symbol.upper()
    if s in YF_ALIASES:
        return YF_ALIASES[s]
    if asset_class(s) == "forex":
        return f"{s}=X"
    return symbol


def fetch_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 300) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC timestamp with open/high/low/close/volume."""
    if asset_class(symbol) == "crypto":
        rows = _crypto_exchange().fetch_ohlcv(symbol.upper(), timeframe=timeframe, limit=limit)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("ts")

    if timeframe not in YF_TF:
        raise ValueError(f"Unsupported timeframe {timeframe} for {symbol}. Use one of {list(YF_TF)}")
    interval, period = YF_TF[timeframe]
    raw = yf.download(_yf_ticker(symbol), period=period, interval=interval,
                      progress=False, auto_adjust=False, multi_level_index=False)
    if raw.empty:
        raise ValueError(f"No data returned for {symbol} ({_yf_ticker(symbol)})")
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    if timeframe == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last", "volume": "sum"}).dropna()
    df.index = pd.to_datetime(df.index, utc=True)
    return df.tail(limit)


def last_price(symbol: str) -> float:
    if asset_class(symbol) == "crypto":
        return float(_crypto_exchange().fetch_ticker(symbol.upper())["last"])
    return float(fetch_ohlcv(symbol, "5m", 5)["close"].iloc[-1])
