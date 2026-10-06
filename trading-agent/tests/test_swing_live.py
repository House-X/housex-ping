"""Live wave-riding: signal -> proposal -> approval -> trailing stop raised daily -> exit synced."""
import dataclasses
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from agent import notify, swing_live
from agent.paper_broker import PaperBroker


def uptrend_with_breakout(n=800, seed=1):
    rng = np.random.default_rng(seed)
    close = np.linspace(100, 200, n) * (1 + 0.02 * np.sin(np.arange(n) / 6)) + rng.normal(0, 0.3, n)
    close[-1] = close[:-1].max() * 1.06                                   # fresh breakout on the last bar
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close * 1.005, "low": close * 0.995,
                         "close": close, "volume": 1.0}, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(swing_live, "STORE", tmp_path / "swing.json")
    monkeypatch.setattr(swing_live, "settings", dataclasses.replace(
        swing_live.settings, swing_symbols="ETH/USDT", swing_max_chase_pct=3))
    sent = []
    monkeypatch.setattr(notify, "send", lambda t, **kw: sent.append((t, kw)) or True)
    df = uptrend_with_breakout()
    px = {"ETH/USDT": float(df["close"].iloc[-1])}
    broker = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: px[s])
    now = df.index[-1].to_pydatetime() + timedelta(days=1, hours=1)
    return broker, df, px, now, sent


def test_daily_schedule_waits_for_the_candle_to_close(env):
    assert not swing_live.due(datetime(2026, 10, 7, 0, 5, tzinfo=timezone.utc))
    assert swing_live.due(datetime(2026, 10, 7, 0, 15, tzinfo=timezone.utc))


def test_breakout_creates_one_proposal_with_buttons(env):
    broker, df, px, now, sent = env
    r = swing_live.run(broker, now=now, fetch=lambda s: df)
    assert len(r["proposals"]) == 1 and not r["errors"]
    text, kw = sent[-1]
    assert "ركوب موجة" in text and "sw:ok:" in str(kw["buttons"])
    assert not swing_live.due(now + timedelta(hours=2))                  # once per day
    r2 = swing_live.run(broker, now=now + timedelta(days=1), fetch=lambda s: df)
    assert not r2["proposals"]                                           # no duplicate while pending


def test_approve_opens_and_trailing_stop_only_rises(env):
    broker, df, px, now, sent = env
    prop = swing_live.run(broker, now=now, fetch=lambda s: df)["proposals"][0]
    pos = swing_live.approve(broker, prop["id"], price_fn=lambda s: px[s], now=now.timestamp())
    assert pos["stop_loss"] < pos["entry"] < pos["take_profit"]
    first_stop = pos["stop_loss"]

    up = pd.concat([df, df.iloc[[-1]].set_axis([df.index[-1] + timedelta(days=1)]) * 1.10])
    r = swing_live.run(broker, now=now + timedelta(days=1), fetch=lambda s: up)
    assert r["raised"] and broker.state["positions"][0]["stop_loss"] > first_stop

    raised = broker.state["positions"][0]["stop_loss"]
    down = pd.concat([up, up.iloc[[-1]].set_axis([up.index[-1] + timedelta(days=1)]) * 0.97])
    swing_live.run(broker, now=now + timedelta(days=2), fetch=lambda s: down)
    assert broker.state["positions"][0]["stop_loss"] == raised           # never lowered


def test_chase_guard_and_expiry(env):
    broker, df, px, now, sent = env
    prop = swing_live.run(broker, now=now, fetch=lambda s: df)["proposals"][0]
    with pytest.raises(ValueError, match="قفز"):
        swing_live.approve(broker, prop["id"], price_fn=lambda s: px[s] * 1.05, now=now.timestamp())
    assert not broker.state["positions"]
    swing_live.reject(prop["id"])
    prop2 = swing_live.run(broker, now=now + timedelta(days=1), fetch=lambda s: df)["proposals"][0]
    with pytest.raises(ValueError, match="صلاحية"):
        swing_live.approve(broker, prop2["id"], price_fn=lambda s: px[s],
                           now=(now + timedelta(days=3)).timestamp())


def test_closed_trades_are_forgotten_so_new_signals_can_come(env):
    broker, df, px, now, sent = env
    prop = swing_live.run(broker, now=now, fetch=lambda s: df)["proposals"][0]
    pos = swing_live.approve(broker, prop["id"], price_fn=lambda s: px[s], now=now.timestamp())
    broker.close_position(pos["id"], reason="trailing stop")
    r = swing_live.run(broker, now=now + timedelta(days=1), fetch=lambda s: df)
    assert r["proposals"]                                                # free to signal again
    assert "ETH/USDT" in swing_live.status_text(broker)


def test_no_signal_without_breakout(env):
    broker, df, px, now, sent = env
    flat = df.copy()
    flat.iloc[-1] = flat.iloc[-2]
    assert not swing_live.run(broker, now=now, fetch=lambda s: flat)["proposals"]
