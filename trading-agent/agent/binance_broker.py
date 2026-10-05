"""Binance Global spot broker: real orders, same interface as PaperBroker.

Safety design:
  - Spot */USDT pairs only, market buys funded by free USDT. No margin, futures or shorting.
  - On real money the API key is inspected and refused if it can withdraw, transfer, or trade
    futures/margin/options, or (by default) if it is not restricted to your IP address.
  - Every buy is immediately protected on the exchange by an OCO sell order (take-profit
    LIMIT_MAKER + STOP_LOSS_LIMIT), so exits happen even if this program is closed.
  - A local watcher (check_stops) is the backup: it records exchange-side exits and market-sells
    if price gaps through the stop-limit or if the OCO could not be placed.
  - LIVE_MAX_ORDER_USD caps every order while you build trust in the system.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import ccxt

from . import risk, sharia
from .config import ROOT, settings
from .market_data import asset_class
from .paper_broker import PaperBroker, _now, _today

STOP_LIMIT_BUFFER = 0.005  # stop-limit sells at up to 0.5% below the stop trigger
FORBIDDEN_PERMISSIONS = ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer",
                         "enableFutures", "enableMargin", "enableVanillaOptions",
                         "enablePortfolioMarginTrading")


class SecurityError(RuntimeError):
    pass


def connect(env: str = settings.binance_env) -> ccxt.Exchange:
    if not settings.exchange_api_key or not settings.exchange_api_secret:
        raise SecurityError("EXCHANGE_API_KEY / EXCHANGE_API_SECRET are not set in .env")
    ex = ccxt.binance({
        "apiKey": settings.exchange_api_key, "secret": settings.exchange_api_secret,
        "enableRateLimit": True, "options": {"defaultType": "spot"},
    })
    if env == "demo":
        ex.enable_demo_trading(True)
    elif env == "testnet":
        ex.set_sandbox_mode(True)
    elif env != "live":
        raise ValueError("BINANCE_ENV must be demo, testnet or live")
    return ex


class BinanceBroker(PaperBroker):
    def __init__(self, exchange: ccxt.Exchange | None = None, env: str = settings.binance_env,
                 state_file: Path | None = None):
        self.env = env
        self.ex = exchange or connect(env)
        self.ex.load_markets()
        self.state_file = state_file or ROOT / "data" / f"binance_{env}_state.json"
        self.state = self._load()
        self.price = lambda s: float(self.ex.fetch_ticker(s)["last"])
        self.fx = lambda c: 1.0  # USDT pairs only

    def _load(self) -> dict:
        state = super()._load()
        for k in ("balance", "starting_balance"):
            state.pop(k, None)
        return state

    # ── safety ──────────────────────────────────────────
    def verify_account(self) -> dict:
        """Refuse to run with a dangerous API key. Call once at startup."""
        report = {"env": self.env, "checks": []}
        if self.env == "live":
            r = self.ex.sapi_get_account_apirestrictions()
            truthy = lambda v: v is True or str(v).lower() == "true"  # noqa: E731
            bad = [k for k in FORBIDDEN_PERMISSIONS if truthy(r.get(k))]
            if bad:
                raise SecurityError("API key has permissions it must not have: " + ", ".join(bad)
                                    + ". Edit the key on Binance and enable only Reading + Spot Trading.")
            if not truthy(r.get("enableSpotAndMarginTrading")):
                raise SecurityError("Spot trading is not enabled on this API key.")
            if settings.require_ip_whitelist and not truthy(r.get("ipRestrict")):
                raise SecurityError("API key is not restricted to your IP address. Add your IP on "
                                    "Binance (recommended) or set REQUIRE_IP_WHITELIST=false.")
            report["checks"].append("API key: read + spot only, no withdraw/transfer/futures/margin")
        else:
            report["checks"].append("Demo/testnet: key permission check skipped (not supported there)")
        bal = self.ex.fetch_balance()
        report["usdt_free"] = float(bal.get("free", {}).get("USDT", 0) or 0)
        report["checks"].append("Authenticated and balance readable")
        report["max_order_usd"] = settings.live_max_order_usd
        return report

    # ── account ─────────────────────────────────────────
    def free_cash(self) -> float:
        return float(self.ex.fetch_balance().get("free", {}).get("USDT", 0) or 0)

    def account(self) -> dict:
        events = self.check_stops()
        bal = self.ex.fetch_balance()
        usdt_total = float(bal.get("total", {}).get("USDT", 0) or 0)
        positions, value, unrealized = [], 0.0, 0.0
        for p in self.state["positions"]:
            px = self.price(p["symbol"])
            pnl = p["units"] * (px - p["entry"])
            value += p["units"] * px
            unrealized += pnl
            positions.append({**p, "current_price": px, "unrealized_pnl": round(pnl, 2),
                              "r_multiple": round(pnl / p["risk_amount"], 2) if p["risk_amount"] else None})
        tracked = {p["symbol"].split("/")[0] for p in self.state["positions"]}
        other = {a: amt for a, amt in (bal.get("total") or {}).items()
                 if amt and a not in tracked and a != "USDT"}
        return {
            "mode": f"binance-{self.env}", "account_currency": "USDT",
            "usdt_balance": round(usdt_total, 2), "free_cash": round(self.free_cash(), 2),
            "equity": round(usdt_total + value, 2), "unrealized_pnl": round(unrealized, 2),
            "today_realized_pnl": round(self.today_pnl(), 2),
            "open_positions": positions, "other_holdings_not_managed": other,
            "events_since_last_check": events,
            "note": "P&L excludes Binance trading fees (~0.1% per side).",
            "risk_rules": {**risk.rules_summary(), "max_order_usd": settings.live_max_order_usd},
        }

    # ── orders ──────────────────────────────────────────
    def preview_order(self, symbol: str, stop_loss: float, take_profit: float,
                      risk_pct: float | None = None, entry: float | None = None) -> dict:
        symbol = symbol.upper()
        errors = []
        market = self.ex.markets.get(symbol)
        if asset_class(symbol) != "crypto" or not symbol.endswith("/USDT"):
            errors.append("Binance execution supports spot */USDT pairs only. Stocks stay in paper mode.")
        elif not market or not market.get("spot") or not market.get("active"):
            errors.append(f"{symbol} is not an active Binance spot market")
        if errors:
            return {"symbol": symbol, "allowed": False, "violations": errors}
        compliance = sharia.check(symbol)
        if not compliance["tradable"]:
            errors.append(f"Sharia screen: {compliance['status']} - " + " ".join(compliance["reasons"]))
        if errors:
            return {"symbol": symbol, "allowed": False, "violations": errors, "sharia": compliance}

        entry = entry or self.price(symbol)
        equity = self.account()["equity"]
        cash = self.free_cash()
        errors += risk.validate_trade(entry, stop_loss, take_profit, len(self.state["positions"]),
                                      self.today_pnl(), equity, cash)
        sizing = None
        if not errors:
            sizing = risk.position_size(equity, cash, entry, stop_loss, risk_pct)
            if sizing["cost"] > settings.live_max_order_usd:
                sizing["units"] = settings.live_max_order_usd / entry
                sizing["capped_by"] = f"LIVE_MAX_ORDER_USD ({settings.live_max_order_usd})"
            amount = float(self.ex.amount_to_precision(symbol, sizing["units"]))
            min_cost = ((market.get("limits") or {}).get("cost") or {}).get("min") or 5
            min_amt = ((market.get("limits") or {}).get("amount") or {}).get("min") or 0
            if amount <= 0 or amount < min_amt or amount * entry < min_cost:
                errors.append(f"Order of {amount * entry:.2f} USDT is below Binance's minimum "
                              f"({min_cost} USDT). Increase capital or MAX_POSITION_PCT.")
            sizing.update(units=amount, cost=round(amount * entry, 2),
                          risk_amount=round(amount * (entry - stop_loss), 2),
                          risk_pct_of_equity=round(amount * (entry - stop_loss) / equity * 100, 3)
                          if equity else None,
                          allocation_pct_of_equity=round(amount * entry / equity * 100, 2)
                          if equity else None)
        return {
            "symbol": symbol, "side": "buy (spot)", "exchange": f"binance-{self.env}",
            "currency": "USD", "fx_to_usd": 1.0, "entry": entry,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "reward_risk": round((take_profit - entry) / (entry - stop_loss), 2) if entry > stop_loss else None,
            "free_cash": round(cash, 2), "sizing": sizing, "sharia": compliance,
            "allowed": not errors, "violations": errors,
        }

    def _place_oco(self, symbol: str, qty: float, stop_loss: float, take_profit: float) -> str:
        r = self.ex.private_post_orderlist_oco({
            "symbol": self.ex.market(symbol)["id"], "side": "SELL",
            "quantity": self.ex.amount_to_precision(symbol, qty),
            "aboveType": "LIMIT_MAKER", "abovePrice": self.ex.price_to_precision(symbol, take_profit),
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": self.ex.price_to_precision(symbol, stop_loss),
            "belowPrice": self.ex.price_to_precision(symbol, stop_loss * (1 - STOP_LIMIT_BUFFER)),
            "belowTimeInForce": "GTC",
            "listClientOrderId": f"hx-oco-{uuid.uuid4().hex[:10]}",
        })
        return str(r["orderListId"])

    def _cancel_oco(self, pos: dict) -> None:
        if not pos.get("oco_list_id"):
            return
        try:
            self.ex.private_delete_orderlist({"symbol": self.ex.market(pos["symbol"])["id"],
                                              "orderListId": pos["oco_list_id"]})
        except ccxt.OrderNotFound:
            pass  # already filled or cancelled
        pos["oco_list_id"] = None

    def _sellable(self, pos: dict) -> float:
        base = pos["symbol"].split("/")[0]
        free = float(self.ex.fetch_balance().get("free", {}).get(base, 0) or 0)
        return float(self.ex.amount_to_precision(pos["symbol"], min(pos["units"], free)))

    def open_position(self, symbol: str, stop_loss: float, take_profit: float,
                      rationale: str, risk_pct: float | None = None) -> dict:
        preview = self.preview_order(symbol, stop_loss, take_profit, risk_pct)
        if not preview["allowed"]:
            raise ValueError("Trade rejected: " + "; ".join(preview["violations"]))
        symbol = preview["symbol"]
        order = self.ex.create_order(symbol, "market", "buy", preview["sizing"]["units"],
                                     params={"newClientOrderId": f"hx-buy-{uuid.uuid4().hex[:10]}"})
        filled = float(order.get("filled") or 0)
        if filled <= 0:
            raise RuntimeError(f"Buy order not filled: {order.get('status')}")
        avg = float(order.get("average") or preview["entry"])

        pos = {
            "id": uuid.uuid4().hex[:8], "symbol": symbol, "side": "buy", "units": filled,
            "entry": avg, "stop_loss": stop_loss, "take_profit": take_profit,
            "risk_amount": round(filled * (avg - stop_loss), 2), "cost": round(filled * avg, 2),
            "currency": "USD", "fx_at_entry": 1.0, "opened_at": _now(), "rationale": rationale,
            "exchange": f"binance-{self.env}", "buy_order_id": order.get("id"),
            "oco_list_id": None, "protection": "local",
        }
        # The fee may have been taken from the coin, so protect what we can actually sell.
        pos["units"] = self._sellable(pos) or filled
        try:
            pos["oco_list_id"] = self._place_oco(symbol, pos["units"], stop_loss, take_profit)
            pos["protection"] = "exchange_oco"
        except Exception as e:
            pos["warning"] = (f"OCO protection failed ({e}). SL/TP are watched locally only: keep "
                              "`python main.py --watch` running or set the stop manually on Binance.")
        self.state["positions"].append(pos)
        self._save()
        return pos

    def _record_close(self, pos: dict, qty: float, exit_px: float, reason: str) -> dict:
        pnl = qty * (exit_px - pos["entry"])
        self.state["positions"].remove(pos)
        self.state["daily_pnl"][_today()] = self.today_pnl() + pnl
        closed = {**pos, "exit": exit_px, "closed_at": _now(), "close_reason": reason,
                  "pnl": round(pnl, 2),
                  "r_multiple": round(pnl / pos["risk_amount"], 2) if pos["risk_amount"] else None}
        self.state["history"].append(closed)
        self._save()
        return closed

    def close_position(self, position_id: str, reason: str, price: float | None = None) -> dict:
        pos = self._find(position_id)
        self._cancel_oco(pos)
        qty = self._sellable(pos)
        if qty <= 0:
            return self._record_close(pos, 0, pos["entry"], f"{reason} (nothing left to sell)")
        order = self.ex.create_order(pos["symbol"], "market", "sell", qty,
                                     params={"newClientOrderId": f"hx-sell-{uuid.uuid4().hex[:10]}"})
        exit_px = float(order.get("average") or self.price(pos["symbol"]))
        return self._record_close(pos, float(order.get("filled") or qty), exit_px, reason)

    def modify_position(self, position_id: str, stop_loss: float | None = None,
                        take_profit: float | None = None) -> dict:
        pos = self._find(position_id)
        new_sl = pos["stop_loss"] if stop_loss is None else stop_loss
        new_tp = pos["take_profit"] if take_profit is None else take_profit
        if new_sl >= new_tp:
            raise ValueError("Stop loss must stay below take profit")
        self._cancel_oco(pos)
        pos["stop_loss"], pos["take_profit"] = new_sl, new_tp
        try:
            pos["oco_list_id"] = self._place_oco(pos["symbol"], pos["units"], new_sl, new_tp)
            pos["protection"] = "exchange_oco"
            pos.pop("warning", None)
        except Exception as e:
            pos["protection"] = "local"
            pos["warning"] = f"OCO re-placement failed ({e}); watched locally only."
        self._save()
        return pos

    def check_stops(self) -> list[dict]:
        """Sync exchange-side exits into the journal; act locally when the exchange can't."""
        events = []
        for pos in list(self.state["positions"]):
            if pos.get("oco_list_id"):
                st = self.ex.private_get_orderlist({"orderListId": pos["oco_list_id"]})
                if st.get("listOrderStatus") == "ALL_DONE":
                    for leg in st.get("orders", []):
                        o = self.ex.fetch_order(str(leg["orderId"]), pos["symbol"])
                        if float(o.get("filled") or 0) > 0:
                            px = float(o.get("average") or o.get("price"))
                            reason = "take_profit hit" if px >= pos["entry"] else "stop_loss hit"
                            events.append(self._record_close(pos, float(o["filled"]), px, reason))
                            break
                    else:
                        pos["oco_list_id"], pos["protection"] = None, "local"
                        pos["warning"] = "OCO ended without a fill (cancelled?). Watching locally."
                        self._save()
                    continue
            px = self.price(pos["symbol"])
            gapped = pos.get("oco_list_id") and px <= pos["stop_loss"] * (1 - 2 * STOP_LIMIT_BUFFER)
            if gapped or (not pos.get("oco_list_id") and px <= pos["stop_loss"]):
                events.append(self.close_position(pos["id"], "stop_loss (local market exit)"))
            elif not pos.get("oco_list_id") and px >= pos["take_profit"]:
                events.append(self.close_position(pos["id"], "take_profit (local market exit)"))
        return events
