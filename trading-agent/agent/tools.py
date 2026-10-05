"""Tool definitions exposed to Claude and their Python implementations."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from . import backtest, crypto_research, market_data, scanner, sharia
from .config import settings
from .indicators import snapshot

TIMEFRAMES = ["15m", "1h", "4h", "1d", "1w"]

CLIENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "analyze_market",
        "description": (
            "Multi-timeframe technical snapshot for one instrument: trend, EMA20/50/200, RSI, MACD, "
            "ADX, ATR, Bollinger, recent swing support/resistance, volume. Call this before forming "
            "any view or trade idea. Symbols: crypto 'BTC/USDT', Turkish stocks with .IS suffix "
            "'THYAO.IS', US stocks 'AAPL', forex 'USDTRY', gold 'XAUUSD', indices 'BIST100','NAS100', "
            "'SPX500', 'DXY', oil 'OIL', or a raw Yahoo ticker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframes": {"type": "array", "items": {"type": "string", "enum": TIMEFRAMES},
                               "description": "Default ['1d','4h','1h']"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_sharia",
        "description": (
            "Sharia compliance screen: compliant / not_compliant / review_required, with reasons and a "
            "halal alternative. Stocks (US and .IS) are screened automatically on business activity "
            "and AAOIFI ratios (debt, cash, interest income) with the purification %. Crypto is "
            "tradable only if the trader approved it. Only 'compliant' instruments can be traded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "scan_turkish_stocks",
        "description": (
            "Scan liquid Borsa Istanbul stocks for long setups (breakout, pullback in uptrend, trend "
            "continuation, early reversal), ranked by a 0-100 technical score with relative strength "
            "vs BIST100 and USD-adjusted returns, then filtered by the automatic Sharia screen. "
            "Takes ~1 minute. Follow up on the best names with analyze_market and news."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "minimum": 1, "maximum": 25},
                "include_review": {"type": "boolean",
                                   "description": "Also list stocks whose Sharia status needs manual review"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "scan_crypto",
        "description": (
            "Scan Binance spot USDT markets (stablecoins, leveraged tokens and blocked coins removed) "
            "for long setups, scored 0-100 with relative strength vs BTC. Modes: 'established' = most "
            "liquid coins; 'new_listings' = listed in the last ~120 days; 'trending' = CoinGecko "
            "trending coins available on Binance. Each result shows whether it is approved to trade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["established", "new_listings", "trending"]},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "research_crypto",
        "description": (
            "Fundamental research on one coin (CoinGecko): what it does, categories, market cap, FDV "
            "vs market cap (unlock/dilution risk), supply, ATH drawdown, developer activity, red "
            "flags, and a category-based Sharia pre-screen. Use before recommending any coin, and "
            "pair it with web_search for team, unlock schedule, audits and recent news."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "e.g. 'SOL' or 'SOL/USDT'"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "backtest_strategy",
        "description": (
            "Test the scanner's long-only rules on years of daily history for up to 6 symbols: entry "
            "at next open after a qualifying setup, ATR stop, fixed reward/risk target, 1% risk, no "
            "leverage, fees included. Returns win rate, average R, profit factor, max drawdown and "
            "buy-and-hold for comparison. Use it to check whether a setup has worked on a symbol "
            "before recommending it, and say clearly when there are too few trades to trust."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "years": {"type": "integer", "minimum": 1, "maximum": 8},
                "min_score": {"type": "integer", "minimum": 40, "maximum": 100},
                "reward_risk": {"type": "number", "minimum": 1, "maximum": 5},
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_account",
        "description": "Account balance, equity, open positions with live P&L and R-multiple, and active risk rules.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "preview_trade",
        "description": (
            "Dry-run a spot BUY: returns current entry price, Sharia screen, position size from the "
            "risk rules (cash-funded, no leverage), reward/risk and any violations. Always preview "
            "before open_trade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "risk_pct": {"type": "number", "description": "Optional, capped by the configured max"},
            },
            "required": ["symbol", "stop_loss", "take_profit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_trade",
        "description": (
            "Buy a spot position at market with cash (no leverage, no shorting). Refused unless the "
            "symbol passes the Sharia screen and was analysed with analyze_market recently. The human "
            "trader must approve it; if they decline, respect it and do not retry the same order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "rationale": {"type": "string", "description": "Setup, confluences and invalidation - saved to the journal"},
                "risk_pct": {"type": "number"},
            },
            "required": ["symbol", "stop_loss", "take_profit", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": "modify_trade",
        "description": "Move stop loss and/or take profit of an open position (e.g. to breakeven or to trail). Needs human approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
            },
            "required": ["position_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "close_trade",
        "description": "Close an open position at market. Needs human approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["position_id", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "trade_history",
        "description": "Closed trades with win rate, average R and net P&L - use it to review performance and mistakes.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
    },
]

for _t in CLIENT_TOOLS:  # inputs stream as generated; we validate them ourselves below
    _t["eager_input_streaming"] = True

SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]

ALL_TOOLS = CLIENT_TOOLS + SERVER_TOOLS
_SCHEMAS = {t["name"]: t["input_schema"] for t in CLIENT_TOOLS}
_PY_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool,
             "array": list, "object": dict}


def validate_input(name: str, data: Any) -> str | None:
    """Minimal JSON-schema check (eager streaming means the API no longer validates for us)."""
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"Unknown tool {name}"
    if not isinstance(data, dict):
        return "Tool input must be a JSON object"
    props = schema["properties"]
    for key in schema.get("required", []):
        if key not in data:
            return f"Missing required field '{key}'"
    for key, val in data.items():
        if key not in props:
            return f"Unexpected field '{key}'"
        spec = props[key]
        is_bool = isinstance(val, bool)
        if not isinstance(val, _PY_TYPES[spec["type"]]) or (is_bool and spec["type"] != "boolean"):
            return f"Field '{key}' must be {spec['type']}"
        if "enum" in spec and val not in spec["enum"]:
            return f"Field '{key}' must be one of {spec['enum']}"
    return None


class ToolExecutor:
    """Runs client tools. Anything that moves money passes a human-approval gate:
    approval_mode="prompt"   -> `confirm(action, details)` is asked synchronously (terminal)
    approval_mode="deferred" -> the order is queued in `self.pending` and the tool returns at once;
                                the UI shows Approve/Reject and calls approve()/reject() (browser)
    """

    def __init__(self, broker, confirm: Callable[[str, dict], bool] | None = None,
                 approval_mode: str = "prompt"):
        self.broker = broker
        self.confirm = confirm
        self.approval_mode = approval_mode
        self.pending: list[dict] = []
        self.analysed_at: dict[str, float] = {}  # symbol -> time of last successful analysis

    def _gate(self, action: str, details: dict, execute: Callable[[], dict]) -> dict:
        if self.approval_mode == "deferred":
            item = {"id": uuid.uuid4().hex[:6], "action": action, "details": details, "execute": execute}
            self.pending.append(item)
            return {"status": "awaiting_trader_approval", "approval_id": item["id"],
                    "note": "Shown to the trader with Approve/Reject buttons. Do not call this tool "
                            "again for the same order; summarise it and let them decide."}
        if not self.confirm or not self.confirm(action, details):
            raise PermissionError(f"Trader declined: {action}")
        return execute()

    def approve(self, approval_id: str) -> dict:
        """Execute a queued order. Risk and Sharia rules are re-checked inside the broker call."""
        item = self._pop(approval_id)
        return {"action": item["action"], "result": item["execute"]()}

    def reject(self, approval_id: str) -> dict:
        item = self._pop(approval_id)
        return {"action": item["action"], "result": "rejected by trader"}

    def _pop(self, approval_id: str) -> dict:
        for item in self.pending:
            if item["id"] == approval_id:
                self.pending.remove(item)
                return item
        raise ValueError(f"No pending approval {approval_id}")

    def run(self, name: str, args: dict) -> str:
        return json.dumps(getattr(self, f"_{name}")(**args), ensure_ascii=False, default=str)

    def _analyze_market(self, symbol: str, timeframes: list[str] | None = None) -> dict:
        out = {"symbol": symbol.upper(), "asset_class": market_data.asset_class(symbol), "timeframes": {}}
        for tf in timeframes or ["1d", "4h", "1h"]:
            try:
                out["timeframes"][tf] = snapshot(market_data.fetch_ohlcv(symbol, tf, 300))
            except Exception as e:
                out["timeframes"][tf] = {"error": str(e)}
        if any("error" not in v for v in out["timeframes"].values()):
            self.analysed_at[symbol.upper()] = time.time()
        out["sharia"] = sharia.check(symbol)
        return out

    def _check_sharia(self, symbol: str) -> dict:
        return sharia.check(symbol)

    def _require_fresh_analysis(self, symbol: str) -> None:
        ts = self.analysed_at.get(symbol.upper())
        max_age = settings.analysis_max_age_min * 60
        if ts is None or time.time() - ts > max_age:
            raise PermissionError(
                f"No data analysis of {symbol.upper()} in the last {settings.analysis_max_age_min} "
                "minutes. Run analyze_market (and check the news) before opening a trade.")

    def _scan_turkish_stocks(self, top_n: int = 10, include_review: bool = False) -> dict:
        return scanner.scan_bist(top_n=top_n, include_review=include_review)

    def _scan_crypto(self, mode: str = "established", top_n: int = 10) -> dict:
        return scanner.scan_crypto(mode=mode, top_n=top_n)

    def _research_crypto(self, symbol: str) -> dict:
        r = crypto_research.research(symbol)
        r["approval_status"] = sharia.check(f"{symbol.upper().split('/')[0]}/USDT")["status"]
        return r

    def _backtest_strategy(self, symbols: list[str], years: int = 4, min_score: int = 70,
                           reward_risk: float = 2.0) -> dict:
        return backtest.backtest(symbols[:6], years=years, min_score=min_score, reward_risk=reward_risk)

    def _get_account(self) -> dict:
        return self.broker.account()

    def _preview_trade(self, **kw) -> dict:
        return self.broker.preview_order(**kw)

    def _open_trade(self, rationale: str, **kw) -> dict:
        self._require_fresh_analysis(kw["symbol"])
        preview = self.broker.preview_order(**kw)
        if not preview["allowed"]:
            raise ValueError("Rejected: " + "; ".join(preview["violations"]))
        return self._gate("OPEN TRADE", {**preview, "rationale": rationale},
                          lambda: {"opened": self.broker.open_position(rationale=rationale, **kw)})

    def _modify_trade(self, position_id: str, **kw) -> dict:
        return self._gate("MODIFY TRADE", {"position_id": position_id, **kw},
                          lambda: {"modified": self.broker.modify_position(position_id, **kw)})

    def _close_trade(self, position_id: str, reason: str) -> dict:
        return self._gate("CLOSE TRADE", {"position_id": position_id, "reason": reason},
                          lambda: {"closed": self.broker.close_position(position_id, reason)})

    def _trade_history(self, limit: int = 20) -> dict:
        return self.broker.history(limit)
