"""Tool definitions exposed to Claude and their Python implementations."""
from __future__ import annotations

import json
from typing import Any, Callable

from . import market_data
from .indicators import snapshot

TIMEFRAMES = ["15m", "1h", "4h", "1d", "1w"]

CLIENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "analyze_market",
        "description": (
            "Multi-timeframe technical snapshot for one instrument: trend, EMA20/50/200, RSI, MACD, "
            "ADX, ATR, Bollinger, recent swing support/resistance, volume. Call this before forming "
            "any view or trade idea. Symbols: crypto 'BTC/USDT', forex 'EURUSD', gold 'XAUUSD', "
            "indices 'NAS100','US30','SPX500', oil 'OIL', or a raw Yahoo ticker."
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
        "name": "get_account",
        "description": "Account balance, equity, open positions with live P&L and R-multiple, and active risk rules.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "preview_trade",
        "description": (
            "Dry-run a trade: returns current entry price, position size from the risk rules, "
            "reward/risk, leverage and any rule violations. Always preview before open_trade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "risk_pct": {"type": "number", "description": "Optional, capped by the configured max"},
            },
            "required": ["symbol", "side", "stop_loss", "take_profit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_trade",
        "description": (
            "Open a market position. The human trader must approve it in the terminal; if they decline "
            "you will get an error result - respect it and do not retry the same order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "rationale": {"type": "string", "description": "Setup, confluences and invalidation - saved to the journal"},
                "risk_pct": {"type": "number"},
            },
            "required": ["symbol", "side", "stop_loss", "take_profit", "rationale"],
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
_PY_TYPES = {"string": str, "number": (int, float), "integer": int, "array": list, "object": dict}


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
        if not isinstance(val, _PY_TYPES[spec["type"]]) or isinstance(val, bool):
            return f"Field '{key}' must be {spec['type']}"
        if "enum" in spec and val not in spec["enum"]:
            return f"Field '{key}' must be one of {spec['enum']}"
    return None


class ToolExecutor:
    """Runs client tools. `confirm` is the human-approval gate for anything that moves money."""

    def __init__(self, broker, confirm: Callable[[str, dict], bool]):
        self.broker = broker
        self.confirm = confirm

    def run(self, name: str, args: dict) -> str:
        return json.dumps(getattr(self, f"_{name}")(**args), ensure_ascii=False, default=str)

    def _analyze_market(self, symbol: str, timeframes: list[str] | None = None) -> dict:
        out = {"symbol": symbol.upper(), "asset_class": market_data.asset_class(symbol), "timeframes": {}}
        for tf in timeframes or ["1d", "4h", "1h"]:
            try:
                out["timeframes"][tf] = snapshot(market_data.fetch_ohlcv(symbol, tf, 300))
            except Exception as e:
                out["timeframes"][tf] = {"error": str(e)}
        return out

    def _get_account(self) -> dict:
        return self.broker.account()

    def _preview_trade(self, **kw) -> dict:
        return self.broker.preview_order(**kw)

    def _open_trade(self, rationale: str, **kw) -> dict:
        preview = self.broker.preview_order(**kw)
        if not preview["allowed"]:
            raise ValueError("Rejected by risk rules: " + "; ".join(preview["violations"]))
        if not self.confirm("OPEN TRADE", {**preview, "rationale": rationale}):
            raise PermissionError("Trader declined this order.")
        return {"opened": self.broker.open_position(rationale=rationale, **kw)}

    def _modify_trade(self, position_id: str, **kw) -> dict:
        if not self.confirm("MODIFY TRADE", {"position_id": position_id, **kw}):
            raise PermissionError("Trader declined this modification.")
        return {"modified": self.broker.modify_position(position_id, **kw)}

    def _close_trade(self, position_id: str, reason: str) -> dict:
        if not self.confirm("CLOSE TRADE", {"position_id": position_id, "reason": reason}):
            raise PermissionError("Trader declined closing this position.")
        return {"closed": self.broker.close_position(position_id, reason)}

    def _trade_history(self, limit: int = 20) -> dict:
        return self.broker.history(limit)
