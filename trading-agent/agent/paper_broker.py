"""Paper-trading broker with a persistent JSON journal.

Every position carries the agent's rationale so the journal doubles as a learning log.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import risk, sharia
from .config import settings
from .market_data import currency_of, fx_to_usd, last_price


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class PaperBroker:
    def __init__(self, state_file: Path = settings.state_file,
                 price_fn: Callable[[str], float] = last_price,
                 fx_fn: Callable[[str], float] = fx_to_usd):
        """Account currency is USD. Positions priced in other currencies (e.g. TRY for BIST)
        are converted at the live FX rate, so P&L reflects currency moves too."""
        self.state_file = state_file
        self.price = price_fn
        self.fx = fx_fn
        self.state = self._load()

    # ── persistence ─────────────────────────────────────
    def _load(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"balance": settings.paper_starting_balance,
                "starting_balance": settings.paper_starting_balance,
                "positions": [], "history": [], "daily_pnl": {}}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── helpers ─────────────────────────────────────────
    def _pnl(self, pos: dict, price: float) -> float:
        """USD P&L of a spot long position."""
        fx_now = self.fx(pos.get("currency", "USD"))
        return pos["units"] * (price * fx_now - pos["entry"] * pos.get("fx_at_entry", 1.0))

    def _find(self, position_id: str) -> dict:
        for p in self.state["positions"]:
            if p["id"] == position_id:
                return p
        raise ValueError(f"No open position with id {position_id}")

    def today_pnl(self) -> float:
        return self.state["daily_pnl"].get(_today(), 0.0)

    # ── public API (mirrors what a live broker will expose) ──
    def account(self) -> dict:
        unrealized = 0.0
        positions = []
        for p in self.state["positions"]:
            try:
                px = self.price(p["symbol"])
                pnl = self._pnl(p, px)
            except Exception as e:  # data hiccup shouldn't break the account view
                px, pnl = None, 0.0
                p = {**p, "price_error": str(e)}
            unrealized += pnl
            positions.append({**p, "current_price": px, "unrealized_pnl": round(pnl, 2),
                              "r_multiple": round(pnl / p["risk_amount"], 2) if p["risk_amount"] else None})
        equity = self.state["balance"] + unrealized
        return {
            "mode": "paper", "account_currency": "USD",
            "balance": round(self.state["balance"], 2),
            "free_cash": round(self.free_cash(), 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized, 2),
            "today_realized_pnl": round(self.today_pnl(), 2),
            "total_return_pct": round((equity / self.state["starting_balance"] - 1) * 100, 2),
            "open_positions": positions,
            "risk_rules": risk.rules_summary(),
        }

    def free_cash(self) -> float:
        """Cash not tied up in open positions. Spot only: we can never spend more than this."""
        return self.state["balance"] - sum(p["cost"] for p in self.state["positions"])

    def preview_order(self, symbol: str, stop_loss: float, take_profit: float,
                      risk_pct: float | None = None, entry: float | None = None) -> dict:
        compliance = sharia.check(symbol)
        entry = entry or self.price(symbol)
        ccy = currency_of(symbol)
        fx = self.fx(ccy)
        equity = self.account()["equity"]
        cash = self.free_cash()
        errors = [] if compliance["tradable"] else \
            [f"Sharia screen: {compliance['status']} - " + " ".join(compliance["reasons"])]
        errors += risk.validate_trade(entry, stop_loss, take_profit, len(self.state["positions"]),
                                      self.today_pnl(), equity, cash)
        sizing = risk.position_size(equity, cash, entry * fx, stop_loss * fx, risk_pct) \
            if not errors else None
        return {
            "symbol": symbol.upper(), "side": "buy (spot)", "currency": ccy,
            "fx_to_usd": fx, "entry": entry,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "reward_risk": round((take_profit - entry) / (entry - stop_loss), 2)
                           if entry > stop_loss else None,
            "free_cash": round(cash, 2), "sizing": sizing, "sharia": compliance,
            "allowed": not errors, "violations": errors,
        }

    def open_position(self, symbol: str, stop_loss: float, take_profit: float,
                      rationale: str, risk_pct: float | None = None) -> dict:
        preview = self.preview_order(symbol, stop_loss, take_profit, risk_pct)
        if not preview["allowed"]:
            raise ValueError("Trade rejected: " + "; ".join(preview["violations"]))
        s = preview["sizing"]
        pos = {
            "id": uuid.uuid4().hex[:8], "symbol": symbol.upper(), "side": "buy",
            "units": s["units"], "entry": preview["entry"],
            "currency": preview["currency"], "fx_at_entry": preview["fx_to_usd"],
            "stop_loss": stop_loss, "take_profit": take_profit,
            "risk_amount": s["risk_amount"], "cost": s["cost"],
            "opened_at": _now(), "rationale": rationale,
        }
        self.state["positions"].append(pos)
        self._save()
        return pos

    def close_position(self, position_id: str, reason: str, price: float | None = None) -> dict:
        pos = self._find(position_id)
        px = price if price is not None else self.price(pos["symbol"])
        pnl = self._pnl(pos, px)
        self.state["positions"].remove(pos)
        self.state["balance"] += pnl
        self.state["daily_pnl"][_today()] = self.today_pnl() + pnl
        closed = {**pos, "exit": px, "closed_at": _now(), "close_reason": reason,
                  "pnl": round(pnl, 2),
                  "r_multiple": round(pnl / pos["risk_amount"], 2) if pos["risk_amount"] else None}
        self.state["history"].append(closed)
        self._save()
        return closed

    def modify_position(self, position_id: str, stop_loss: float | None = None,
                        take_profit: float | None = None) -> dict:
        pos = self._find(position_id)
        new_sl = pos["stop_loss"] if stop_loss is None else stop_loss
        new_tp = pos["take_profit"] if take_profit is None else take_profit
        if new_sl >= new_tp:
            raise ValueError("Stop loss must stay below take profit")
        if stop_loss is not None:
            pos["stop_loss"] = stop_loss
        if take_profit is not None:
            pos["take_profit"] = take_profit
        self._save()
        return pos

    def check_stops(self) -> list[dict]:
        """Close any position whose SL/TP has been touched. Call this on a schedule."""
        closed = []
        for pos in list(self.state["positions"]):
            px = self.price(pos["symbol"])
            if px <= pos["stop_loss"]:
                closed.append(self.close_position(pos["id"], "stop_loss hit", pos["stop_loss"]))
            elif px >= pos["take_profit"]:
                closed.append(self.close_position(pos["id"], "take_profit hit", pos["take_profit"]))
        return closed

    def history(self, limit: int = 20) -> dict:
        h = self.state["history"]
        wins = [t for t in h if t["pnl"] > 0]
        rs = [t["r_multiple"] for t in h if t.get("r_multiple") is not None]
        return {
            "total_trades": len(h),
            "win_rate_pct": round(len(wins) / len(h) * 100, 1) if h else None,
            "avg_r_multiple": round(sum(rs) / len(rs), 2) if rs else None,
            "net_pnl": round(sum(t["pnl"] for t in h), 2),
            "recent": h[-limit:],
        }
