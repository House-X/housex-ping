"""Market data: crypto via ccxt (public endpoints, no key), forex/metals/indices via yfinance.

Symbol conventions accepted from the agent:
  crypto : BTC/USDT, ETH/USDT, SOL/USDT ...  (anything containing "/" with a crypto quote)
  forex  : EURUSD, GBPJPY, USDTRY ...       (two ISO currency codes)
  BIST   : THYAO.IS, ASELS.IS ...           (Borsa Istanbul, priced in TRY)
  other  : XAUUSD (gold), XAGUSD, US30, NAS100, SPX500, OIL, or any raw yfinance ticker
"""
from __future__ import annotations

import logging

import ccxt
import pandas as pd
import yfinance as yf

from .config import settings

logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # delisted tickers are skipped quietly

CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "FDUSD")
CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "TRY", "SAR", "AED", "KWD",
              "QAR", "EGP", "CNY", "SEK", "NOK", "DKK", "PLN", "ZAR", "MXN", "SGD", "HKD", "INR"}

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
    if len(s) == 6 and s[:3] in CURRENCIES and s[3:] in CURRENCIES:
        return "forex"
    return "stock"


def currency_of(symbol: str) -> str:
    """Currency the instrument is priced in."""
    s = symbol.upper()
    if asset_class(s) == "crypto":
        quote = s.split("/")[1].split(":")[0]
        return "USD" if quote in ("USDT", "USDC", "BUSD", "FDUSD") else quote
    if s.endswith(".IS"):
        return "TRY"
    return "USD"


def fx_to_usd(currency: str) -> float:
    """Multiply an amount in `currency` by this to get USD."""
    if currency == "USD":
        return 1.0
    if currency in CRYPTO_QUOTES:  # e.g. ETH-quoted pair
        return last_price(f"{currency}/USDT")
    rate = float(fetch_ohlcv(f"USD{currency}", "1d", 5)["close"].iloc[-1])
    return 1.0 / rate


def fetch_daily_many(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """Batch-download daily bars for many Yahoo tickers in one request (stocks scanner)."""
    raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                      progress=False, auto_adjust=False, threads=True)
    out = {}
    for t in tickers:
        try:
            df = (raw[t] if len(tickers) > 1 else raw).rename(columns=str.lower)
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            if len(df) >= 60:
                df.index = pd.to_datetime(df.index, utc=True)
                out[t] = df
        except KeyError:
            continue
    return out


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


def fetch_daily_history(symbol: str, years: int = 4) -> pd.DataFrame:
    """Several years of daily bars (paginated for crypto) for backtesting."""
    if asset_class(symbol) == "crypto":
        ex = _crypto_exchange()
        since = ex.milliseconds() - int(years * 365.25 * 86_400_000)
        rows: list = []
        while True:
            batch = ex.fetch_ohlcv(symbol.upper(), "1d", since=since, limit=1000)
            if not batch:
                break
            rows += batch
            if len(batch) < 1000:
                break
            since = batch[-1][0] + 86_400_000
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.drop_duplicates("ts").set_index("ts")
    raw = yf.download(_yf_ticker(symbol), period=f"{years}y", interval="1d",
                      progress=False, auto_adjust=False, multi_level_index=False)
    if raw.empty:
        raise ValueError(f"No history for {symbol}")
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def last_completed_daily_close(symbol: str, now=None) -> float:
    """Close of the most recent FINISHED daily bar (today's bar is still moving)."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    df = fetch_ohlcv(symbol, "1d", 5)
    if asset_class(symbol) == "crypto":  # exchange daily bars roll at 00:00 UTC; last one is live
        return float(df["close"].iloc[-2])
    close_utc = (15, 15) if symbol.upper().endswith(".IS") else (21, 15)  # BIST / US session end
    last_is_today = df.index[-1].date() == now.date()
    still_open = (now.hour, now.minute) < close_utc
    return float(df["close"].iloc[-2] if last_is_today and still_open else df["close"].iloc[-1])
