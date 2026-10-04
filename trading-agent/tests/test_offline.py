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
    s = position_size(10_000, 10_000, entry=100, stop_loss=90)
    assert s["risk_amount"] == pytest.approx(100)
    assert s["units"] == pytest.approx(10)
    assert s["leverage"] == 1.0


def test_never_spends_more_than_cash_or_allocation_cap():
    # Tight stop would need 50x leverage; must be capped at 30% of equity
    s = position_size(10_000, 10_000, entry=100, stop_loss=99.98)
    assert s["cost"] <= 3_000 and s["capped_by"].startswith("max position")
    # Only 500 free cash left
    s = position_size(10_000, 500, entry=100, stop_loss=90)
    assert s["cost"] == pytest.approx(500) and s["capped_by"] == "available cash"


def test_validation_rules():
    assert validate_trade(100, 98, 101, 0, 0, 10_000, 10_000)  # RR 0.5 rejected
    assert validate_trade(100, 102, 90, 0, 0, 10_000, 10_000)  # short-style levels rejected
    assert not validate_trade(100, 98, 104, 0, 0, 10_000, 10_000)
    assert validate_trade(100, 98, 104, 0, -400, 10_000, 10_000)  # daily loss hit
    assert validate_trade(100, 98, 104, 0, 0, 10_000, 0)  # no free cash


@pytest.mark.parametrize("symbol,status", [
    ("BTC/USDT", "compliant"), ("ETH/USDT", "compliant"), ("SPUS", "compliant"),
    ("BTC/USDT:USDT", "not_compliant"), ("DOGE/USDT", "not_compliant"),
    ("EURUSD", "not_compliant"), ("XAUUSD", "not_compliant"), ("NAS100", "not_compliant"),
    ("GC=F", "not_compliant"), ("ADA/USDT", "review_required"), ("AAPL", "review_required"),
])
def test_sharia_screen(symbol, status):
    from agent.sharia import check
    r = check(symbol)
    assert r["status"] == status
    assert r["tradable"] == (status == "compliant")


def test_non_compliant_symbol_is_rejected(broker):
    broker._prices["EURUSD"] = 1.10
    preview = broker.preview_order("EURUSD", 1.09, 1.13)
    assert not preview["allowed"] and "Sharia" in preview["violations"][0]


def test_paper_round_trip(broker):
    pos = broker.open_position("BTC/USDT", 90, 130, "test")
    assert broker.free_cash() == pytest.approx(9_000)
    broker._prices["BTC/USDT"] = 131
    closed = broker.check_stops()
    assert closed[0]["close_reason"] == "take_profit hit"
    assert closed[0]["r_multiple"] == pytest.approx(3.0)
    assert broker.account()["balance"] == pytest.approx(10_300)
    assert broker.free_cash() == pytest.approx(10_300)
    assert broker.history()["win_rate_pct"] == 100


def test_input_validation():
    assert validate_input("open_trade", {"symbol": "X"})
    assert validate_input("preview_trade", {"symbol": "X", "side": "sell", "stop_loss": 1, "take_profit": 2})
    assert validate_input("get_account", {}) is None


ORDER = {"symbol": "BTC/USDT", "stop_loss": 90, "take_profit": 130, "rationale": "x"}


def test_trade_refused_without_prior_analysis(broker):
    ex = ToolExecutor(broker, confirm=lambda a, d: True)
    with pytest.raises(PermissionError, match="analysis"):
        ex.run("open_trade", ORDER)
    assert broker.state["positions"] == []


def test_trade_allowed_after_analysis(broker, monkeypatch):
    monkeypatch.setattr(tools_mod.market_data, "fetch_ohlcv", lambda *a: synthetic_ohlcv())
    ex = ToolExecutor(broker, confirm=lambda a, d: True)
    ex.run("analyze_market", {"symbol": "BTC/USDT"})
    assert json.loads(ex.run("open_trade", ORDER))["opened"]["side"] == "buy"


def test_declined_order_is_not_opened(broker, monkeypatch):
    monkeypatch.setattr(tools_mod.market_data, "fetch_ohlcv", lambda *a: synthetic_ohlcv())
    ex = ToolExecutor(broker, confirm=lambda a, d: False)
    ex.run("analyze_market", {"symbol": "BTC/USDT"})
    with pytest.raises(PermissionError, match="declined"):
        ex.run("open_trade", ORDER)
    assert broker.state["positions"] == []


def test_agent_loop_with_fake_client(broker, monkeypatch):
    """Simulate Claude: call preview_trade, then answer."""
    from agent import core

    def msg(stop, content):
        return SimpleNamespace(stop_reason=stop, content=content)

    replies = iter([
        msg("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="preview_trade",
                                         input={"symbol": "BTC/USDT",
                                                "stop_loss": 98, "take_profit": 106})]),
        msg("end_turn", [SimpleNamespace(type="text", text="done")]),
    ])
    monkeypatch.setattr(core.anthropic, "Anthropic", lambda: None)
    agent = core.TradingAgent(broker, confirm=lambda a, d: True, on_text=lambda t: None)
    monkeypatch.setattr(agent, "_request", lambda: next(replies))
    assert agent.ask("preview") == "done"
    result = json.loads(agent.messages[2]["content"][0]["content"])
    assert result["allowed"] and result["reward_risk"] == 3.0
