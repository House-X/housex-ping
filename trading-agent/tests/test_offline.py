"""Offline tests: synthetic prices, no network, no API key."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agent import tools as tools_mod
from agent.indicators import snapshot
from agent.paper_broker import PaperBroker
from agent.risk import position_size, validate_trade
from agent.tools import ToolExecutor, validate_input


def synthetic_ohlcv(n=300, start=100.0, drift=0.001, seed=1):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": rng.uniform(100, 200, n)}, index=idx)


@pytest.fixture
def broker(tmp_path):
    prices = {"BTC/USDT": 100.0}
    b = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: prices[s.upper()])
    b._prices = prices
    return b


def test_snapshot_uptrend():
    snap = snapshot(synthetic_ohlcv(drift=0.003))
    assert snap["trend"] == "uptrend"
    assert 0 <= snap["rsi14"] <= 100
    assert snap["atr14"] > 0


def test_position_size_risks_exactly_one_pct():
    s = position_size(10_000, entry=100, stop_loss=98)
    assert s["risk_amount"] == pytest.approx(100)
    assert s["units"] == pytest.approx(50)


def test_leverage_cap():
    s = position_size(10_000, entry=100, stop_loss=99.99)
    assert s["effective_leverage"] <= 5


def test_validation_rules():
    assert validate_trade("buy", 100, 98, 101, 0, 0, 10_000)  # RR 0.5 rejected
    assert validate_trade("buy", 100, 102, 110, 0, 0, 10_000)  # SL above entry
    assert not validate_trade("buy", 100, 98, 104, 0, 0, 10_000)
    assert validate_trade("sell", 100, 102, 96, 0, -400, 10_000)  # daily loss hit


def test_paper_round_trip(broker):
    pos = broker.open_position("BTC/USDT", "buy", 98, 106, "test")
    broker._prices["BTC/USDT"] = 106.5
    closed = broker.check_stops()
    assert closed[0]["close_reason"] == "take_profit hit"
    assert closed[0]["r_multiple"] == pytest.approx(3.0)
    assert broker.account()["balance"] == pytest.approx(10_300)
    assert broker.history()["win_rate_pct"] == 100


def test_input_validation():
    assert validate_input("open_trade", {"symbol": "X"})
    assert validate_input("preview_trade", {"symbol": "X", "side": "hold", "stop_loss": 1, "take_profit": 2})
    assert validate_input("get_account", {}) is None


def test_declined_order_is_not_opened(broker):
    ex = ToolExecutor(broker, confirm=lambda a, d: False)
    with pytest.raises(PermissionError):
        ex.run("open_trade", {"symbol": "BTC/USDT", "side": "buy", "stop_loss": 98,
                              "take_profit": 106, "rationale": "x"})
    assert broker.state["positions"] == []


def test_agent_loop_with_fake_client(broker, monkeypatch):
    """Simulate Claude: call preview_trade, then answer."""
    from agent import core

    def msg(stop, content):
        return SimpleNamespace(stop_reason=stop, content=content)

    replies = iter([
        msg("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="preview_trade",
                                         input={"symbol": "BTC/USDT", "side": "buy",
                                                "stop_loss": 98, "take_profit": 106})]),
        msg("end_turn", [SimpleNamespace(type="text", text="done")]),
    ])
    monkeypatch.setattr(core.anthropic, "Anthropic", lambda: None)
    agent = core.TradingAgent(broker, confirm=lambda a, d: True, on_text=lambda t: None)
    monkeypatch.setattr(agent, "_request", lambda: next(replies))
    assert agent.ask("preview") == "done"
    result = json.loads(agent.messages[2]["content"][0]["content"])
    assert result["allowed"] and result["reward_risk"] == 3.0
