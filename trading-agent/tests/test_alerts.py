"""Alerts and pre-approved automatic buys: every safety path."""
import dataclasses
import time

import pytest

from agent import alerts, notify
from agent.paper_broker import PaperBroker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "STORE", tmp_path / "alerts.json")
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: sent.append(text) or True)
    return sent


@pytest.fixture
def market():
    return {"ETH/USDT": 100.0}


@pytest.fixture
def broker(tmp_path, market):
    return PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: market[s])


def run(broker, market, closes=None):
    return alerts.check(broker, price_fn=lambda s: market[s],
                        close_fn=lambda s: (closes or market)[s], now=time.time())


def test_notify_alert_fires_once(broker, market, isolated):
    a = alerts.create("ETH/USDT", "price_above", 110, note="اختراق")
    assert a["status"] == "armed"
    assert run(broker, market) == []
    market["ETH/USDT"] = 111
    ev = run(broker, market)
    assert ev[0]["status"] == "triggered" and "تحقق الشرط" in isolated[-1]
    assert run(broker, market) == []                      # never fires twice


def test_close_conditions_use_daily_close_not_live_price(broker, market):
    alerts.create("ETH/USDT", "close_above", 110)
    market["ETH/USDT"] = 115                              # live price above...
    assert run(broker, market, closes={"ETH/USDT": 105}) == []   # ...but last daily close is not
    assert run(broker, market, closes={"ETH/USDT": 112})[0]["status"] == "triggered"


def test_auto_buy_needs_approval_then_executes(broker, market, isolated):
    a = alerts.create("ETH/USDT", "price_above", 105, "auto_buy", stop_loss=95, take_profit=130)
    assert a["status"] == "proposed" and "بانتظار موافقتك" in isolated[-1]
    market["ETH/USDT"] = 106
    assert run(broker, market) == [] and broker.state["positions"] == []   # not before approval
    alerts.approve(a["id"])
    ev = run(broker, market)
    assert ev[0]["status"] == "executed"
    pos = broker.state["positions"][0]
    assert pos["stop_loss"] == 95 and pos["take_profit"] == 130
    assert "شراء تلقائي نُفّذ" in isolated[-1]


def test_auto_buy_does_not_chase(broker, market, isolated):
    a = alerts.create("ETH/USDT", "price_above", 105, "auto_buy", stop_loss=95, take_profit=130)
    alerts.approve(a["id"])
    market["ETH/USDT"] = 110                              # 4.8% past the trigger (> 1.5%)
    ev = run(broker, market)
    assert ev[0]["status"] == "failed" and "قفز" in ev[0]["result"]
    assert broker.state["positions"] == []


def test_auto_buy_still_obeys_risk_rules(broker, market):
    # by the time it triggers, reward/risk from the live price is below the 1.5 minimum
    a = alerts.create("ETH/USDT", "price_below", 100, "auto_buy", stop_loss=90, take_profit=112)
    alerts.approve(a["id"])
    market["ETH/USDT"] = 99.5                             # R:R = 12.5/9.5 = 1.3
    ev = run(broker, market)
    assert ev[0]["status"] == "failed" and "Reward/risk" in ev[0]["result"]


def test_auto_buy_still_obeys_sharia(broker, market):
    market["WLD/USDT"] = 100
    a = alerts.create("WLD/USDT", "price_above", 100, "auto_buy", stop_loss=90, take_profit=130)
    alerts.approve(a["id"])
    ev = run(broker, market)
    assert ev[0]["status"] == "failed" and "Sharia" in ev[0]["result"]


def test_real_money_auto_buy_disabled_by_default(broker, market):
    broker.env = "live"                                   # pretend we are a live Binance broker
    a = alerts.create("ETH/USDT", "price_above", 100, "auto_buy", stop_loss=90, take_profit=130)
    alerts.approve(a["id"])
    ev = run(broker, market)
    assert ev[0]["status"] == "failed" and "AUTO_BUY_LIVE" in ev[0]["result"]
    assert broker.state["positions"] == []


def test_alerts_expire(broker, market, isolated):
    a = alerts.create("ETH/USDT", "price_above", 999, expires_days=1)
    ev = alerts.check(broker, price_fn=lambda s: market[s], close_fn=lambda s: market[s],
                      now=time.time() + 2 * 86400)
    assert ev[0]["id"] == a["id"] and ev[0]["status"] == "expired" and "انتهت" in isolated[-1]


def test_invalid_plans_and_duplicates():
    with pytest.raises(ValueError):
        alerts.create("ETH/USDT", "price_above", 100, "auto_buy")                         # no plan
    with pytest.raises(ValueError):
        alerts.create("ETH/USDT", "price_above", 100, "auto_buy", stop_loss=105, take_profit=130)
    a = alerts.create("ETH/USDT", "price_above", 120)
    b = alerts.create("ETH/USDT", "price_above", 120)
    assert b["duplicate"] and b["id"] == a["id"] and len(alerts.active()) == 1


def test_cannot_approve_twice_or_cancel_finished():
    a = alerts.create("ETH/USDT", "price_above", 105, "auto_buy", stop_loss=95, take_profit=130)
    alerts.approve(a["id"])
    with pytest.raises(ValueError):
        alerts.approve(a["id"])
    alerts.cancel(a["id"])
    with pytest.raises(ValueError):
        alerts.cancel(a["id"])


def test_chase_limit_is_configurable(broker, market, monkeypatch):
    monkeypatch.setattr(alerts, "settings", dataclasses.replace(alerts.settings, auto_buy_max_chase_pct=10))
    a = alerts.create("ETH/USDT", "price_above", 100, "auto_buy", stop_loss=80, take_profit=150)
    alerts.approve(a["id"])
    market["ETH/USDT"] = 105
    assert run(broker, market)[0]["status"] == "executed"


def test_agent_tool_creates_proposal_and_explains_next_step():
    from agent.tools import ToolExecutor
    import json
    ex = ToolExecutor(broker=None)
    out = json.loads(ex.run("create_alert", {"symbol": "ETH/USDT", "condition": "close_above", "level": 105,
                                             "action": "auto_buy", "stop_loss": 95, "take_profit": 130}))
    assert out["status"] == "proposed" and "approve" in out["next_step"]


def test_explorer_cannot_alert_on_unapproved_coins(broker):
    from agent.tools import ToolExecutor
    ex = ToolExecutor(broker)
    ex.alert_source = "explorer"
    assert "error" in ex._create_alert("WLD/USDT", "price_above", 125, "notify")
    assert "id" in ex._create_alert("BTC/USDT", "price_above", 90000, "notify")
