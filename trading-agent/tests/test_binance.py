"""Binance broker against an in-memory fake exchange (no network, no keys)."""
import dataclasses

import ccxt
import pytest

from agent import binance_broker
from agent.binance_broker import BinanceBroker, SecurityError


class FakeBinance:
    """Implements just the ccxt surface BinanceBroker uses."""

    def __init__(self, price=100.0, usdt=10_000.0, restrictions=None):
        self.px = price
        self.bal = {"USDT": usdt, "ETH": 0.0}
        self.locked = {"USDT": 0.0, "ETH": 0.0}
        self.orders, self.lists = {}, {}
        self.markets = {"ETH/USDT": {"id": "ETHUSDT", "spot": True, "active": True,
                                     "limits": {"cost": {"min": 5}, "amount": {"min": 0.0001}}},
                        "APT/USDT": {"id": "APTUSDT", "spot": True, "active": True, "limits": {}}}
        self.restrictions = restrictions or {"ipRestrict": True, "enableSpotAndMarginTrading": True,
                                             "enableWithdrawals": False, "enableFutures": False}
        self.n = 0
        self.fee = 0.001  # taken in the base coin, like Binance without BNB

    def load_markets(self):
        return self.markets

    def market(self, s):
        return self.markets[s]

    def amount_to_precision(self, s, x):
        return f"{int(x * 1e4) / 1e4:.4f}"

    def price_to_precision(self, s, x):
        return f"{x:.2f}"

    def fetch_ticker(self, s):
        return {"last": self.px}

    def fetch_balance(self):
        return {"free": dict(self.bal),
                "total": {k: self.bal[k] + self.locked[k] for k in self.bal}}

    def sapi_get_account_apirestrictions(self):
        return self.restrictions

    def _id(self):
        self.n += 1
        return str(self.n)

    def create_order(self, s, typ, side, amount, params=None):
        amount = float(amount)
        if side == "buy":
            self.bal["USDT"] -= amount * self.px
            self.bal["ETH"] += amount * (1 - self.fee)
        else:
            self.bal["ETH"] -= amount
            self.bal["USDT"] += amount * self.px
        oid = self._id()
        self.orders[oid] = {"id": oid, "status": "closed", "filled": amount, "average": self.px}
        return self.orders[oid]

    def cost_to_precision(self, s, x):
        return f"{x:.2f}"

    def create_market_buy_order_with_cost(self, s, cost, params=None):
        return self.create_order(s, "market", "buy", cost / self.px, params)

    def private_post_orderlist_oco(self, p):
        q = float(p["quantity"])
        self.bal["ETH"] -= q
        self.locked["ETH"] += q
        tp, sl = self._id(), self._id()
        self.orders[tp] = {"id": tp, "filled": 0, "price": float(p["abovePrice"]), "status": "open"}
        self.orders[sl] = {"id": sl, "filled": 0, "price": float(p["belowPrice"]), "status": "open"}
        lid = self._id()
        self.lists[lid] = {"listOrderStatus": "EXECUTING", "orders": [{"orderId": tp}, {"orderId": sl}],
                           "qty": q, "params": p}
        return {"orderListId": int(lid)}

    def private_delete_orderlist(self, p):
        lst = self.lists[str(p["orderListId"])]
        if lst["listOrderStatus"] == "ALL_DONE":
            raise ccxt.OrderNotFound("done")
        lst["listOrderStatus"] = "ALL_DONE"
        self.locked["ETH"] -= lst["qty"]
        self.bal["ETH"] += lst["qty"]

    def private_get_orderlist(self, p):
        return self.lists[str(p["orderListId"])]

    def fetch_order(self, oid, s):
        return self.orders[oid]

    def fill_leg(self, lid, which):  # simulate the exchange executing TP (0) or SL (1)
        lst = self.lists[lid]
        o = self.orders[str(lst["orders"][which]["orderId"])]
        o.update(filled=lst["qty"], average=o["price"], status="closed")
        lst["listOrderStatus"] = "ALL_DONE"
        self.locked["ETH"] -= lst["qty"]
        self.bal["USDT"] += lst["qty"] * o["price"]


def _cap(monkeypatch, usd):
    monkeypatch.setattr(binance_broker, "settings",
                        dataclasses.replace(binance_broker.settings, live_max_order_usd=usd))


@pytest.fixture
def ex():
    return FakeBinance()


@pytest.fixture
def broker(ex, tmp_path, monkeypatch):
    _cap(monkeypatch, 50)
    return BinanceBroker(exchange=ex, env="demo", state_file=tmp_path / "b.json")


def test_buy_is_capped_and_protected_by_oco(broker, ex):
    pos = broker.open_position("ETH/USDT", 90, 130, "test")
    assert pos["cost"] <= 50.01                      # LIVE_MAX_ORDER_USD cap
    assert pos["protection"] == "exchange_oco"
    oco = ex.lists[pos["oco_list_id"]]["params"]
    assert oco["side"] == "SELL" and oco["aboveType"] == "LIMIT_MAKER"
    assert oco["belowType"] == "STOP_LOSS_LIMIT" and oco["belowStopPrice"] == "90.00"
    assert float(oco["belowPrice"]) < 90             # stop-limit buffer
    assert pos["units"] < 0.5                        # fee taken in coin: protects what we own


def test_exchange_take_profit_is_synced_to_journal(broker, ex):
    pos = broker.open_position("ETH/USDT", 90, 130, "test")
    ex.fill_leg(pos["oco_list_id"], 0)
    events = broker.check_stops()
    assert events[0]["close_reason"] == "take_profit hit"
    assert events[0]["r_multiple"] == pytest.approx(3.0, rel=0.01)
    assert broker.state["positions"] == []


def test_manual_close_cancels_oco_then_sells(broker, ex):
    pos = broker.open_position("ETH/USDT", 90, 130, "test")
    lid = pos["oco_list_id"]
    ex.px = 110
    closed = broker.close_position(pos["id"], "thesis invalid")
    assert ex.lists[lid]["listOrderStatus"] == "ALL_DONE"
    assert closed["pnl"] > 0 and ex.bal["ETH"] == pytest.approx(0, abs=1e-4)


def test_modify_replaces_oco(broker, ex):
    pos = broker.open_position("ETH/USDT", 90, 130, "test")
    old = pos["oco_list_id"]
    new = broker.modify_position(pos["id"], stop_loss=100)
    assert new["oco_list_id"] != old
    assert ex.lists[new["oco_list_id"]]["params"]["belowStopPrice"] == "100.00"


def test_gap_through_stop_triggers_local_exit(broker, ex):
    broker.open_position("ETH/USDT", 90, 130, "test")
    ex.px = 85  # crashed through the stop-limit price; OCO leg would not fill
    events = broker.check_stops()
    assert events and "local market exit" in events[0]["close_reason"]


def test_oco_failure_falls_back_to_local_watch(broker, ex, monkeypatch):
    def boom(p):
        raise ccxt.InvalidOrder("PERCENT_PRICE_BY_SIDE")
    monkeypatch.setattr(ex, "private_post_orderlist_oco", boom)
    pos = broker.open_position("ETH/USDT", 90, 130, "test")
    assert pos["protection"] == "local" and "warning" in pos
    ex.px = 89
    assert broker.check_stops()[0]["close_reason"].startswith("stop_loss")


@pytest.mark.parametrize("symbol,msg", [
    ("APT/USDT", "Sharia"),           # active market, but not in the approved list
    ("THYAO.IS", "spot */USDT"),      # stocks can't go to Binance
    ("BTC/USDT", "not an active"),    # not in this fake exchange
])
def test_rejections(broker, symbol, msg):
    p = broker.preview_order(symbol, 90, 130)
    assert not p["allowed"] and any(msg in v for v in p["violations"])


def test_order_below_binance_minimum_is_rejected(broker, monkeypatch):
    _cap(monkeypatch, 3)
    p = broker.preview_order("ETH/USDT", 90, 130)
    assert not p["allowed"] and "minimum" in p["violations"][0]


@pytest.mark.parametrize("restrictions,msg", [
    ({"enableWithdrawals": True, "enableSpotAndMarginTrading": True, "ipRestrict": True}, "enableWithdrawals"),
    ({"enableFutures": True, "enableSpotAndMarginTrading": True, "ipRestrict": True}, "enableFutures"),
    ({"enableMargin": True, "enableSpotAndMarginTrading": True, "ipRestrict": True}, "enableMargin"),
    ({"enableSpotAndMarginTrading": False, "ipRestrict": True}, "Spot trading"),
    ({"enableSpotAndMarginTrading": True, "ipRestrict": False}, "IP"),
])
def test_live_key_with_dangerous_permissions_is_refused(tmp_path, restrictions, msg):
    b = BinanceBroker(exchange=FakeBinance(restrictions=restrictions), env="live",
                      state_file=tmp_path / "l.json")
    with pytest.raises(SecurityError, match=msg):
        b.verify_account()


def test_live_key_with_safe_permissions_passes(tmp_path):
    b = BinanceBroker(exchange=FakeBinance(), env="live", state_file=tmp_path / "l.json")
    assert b.verify_account()["usdt_free"] == 10_000


def test_capped_preview_reports_consistent_sizing(broker):
    """After the LIVE_MAX_ORDER_USD cap every sizing figure must describe the capped order."""
    s = broker.preview_order("ETH/USDT", 90, 130)["sizing"]
    assert s["cost"] <= 50.01
    assert s["allocation_pct_of_equity"] == pytest.approx(s["cost"] / 10_000 * 100, abs=0.01)
    assert s["risk_pct_of_equity"] == pytest.approx(s["risk_amount"] / 10_000 * 100, abs=0.001)


def test_core_buy_is_separate_from_trading_positions(broker, ex):
    fill = broker.buy_core("ETH/USDT", 40)
    assert fill["cost"] == pytest.approx(40) and not broker.state["positions"]
    pos = broker.open_position("ETH/USDT", stop_loss=95, take_profit=110, rationale="t")
    broker.close_position(pos["id"], reason="test")
    assert ex.bal["ETH"] == pytest.approx(fill["units"] * (1 - ex.fee), rel=0.02)  # core coins stay


def test_core_buy_refuses_non_usdt_and_too_much(broker):
    with pytest.raises(ValueError):
        broker.buy_core("BIMAS.IS", 40)
    with pytest.raises(ValueError):
        broker.buy_core("ETH/USDT", 10**9)
