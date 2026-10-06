"""Core (weekly DCA) and swing (trailing-stop) engines."""
import dataclasses
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from agent import dca, notify, swing
from agent.paper_broker import PaperBroker
from test_offline import synthetic_ohlcv


# ── core / DCA ───────────────────────────────────────────────
@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setattr(dca, "STORE", tmp_path / "dca.json")
    monkeypatch.setattr(dca, "settings", dataclasses.replace(
        dca.settings, dca_weekly_usd=100, dca_allocation="BTC/USDT:60,ETH/USDT:40",
        dca_weekday=0, dca_hour_utc=9, dca_live=False))
    monkeypatch.setattr(dca, "trend_note", lambda symbol="BTC/USDT": "")
    sent = []
    monkeypatch.setattr(notify, "send", lambda t, **kw: sent.append(t) or True)
    prices = {"BTC/USDT": 50_000.0, "ETH/USDT": 2_500.0}
    broker = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: prices[s])
    return broker, prices, sent


MON_10 = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)   # a Monday


def test_allocation_is_normalised(core):
    assert dca.allocation() == {"BTC/USDT": 0.6, "ETH/USDT": 0.4}


def test_weekly_schedule_and_catch_up(core):
    assert not dca.due(datetime(2026, 10, 5, 8, tzinfo=timezone.utc))   # Monday before 09:00
    assert dca.due(MON_10)
    dca.run(core[0], now=MON_10, send=False)
    assert not dca.due(datetime(2026, 10, 8, 12, tzinfo=timezone.utc))  # same week
    assert dca.due(datetime(2026, 10, 14, 12, tzinfo=timezone.utc))     # laptop was off on Monday
    dca.run(core[0], now=datetime(2026, 10, 14, 12, tzinfo=timezone.utc), send=False)
    assert not dca.due(datetime(2026, 10, 15, 12, tzinfo=timezone.utc))  # caught up once, not doubled


def test_run_buys_split_and_reports(core):
    broker, prices, sent = core
    cash = broker.free_cash()
    b = dca.run(broker, now=MON_10)
    assert [x["cost"] for x in b["buys"]] == [60.0, 40.0]
    assert broker.free_cash() == pytest.approx(cash - 100)
    prices["BTC/USDT"] = 55_000.0
    h = dca.holdings(price_fn=lambda s: prices[s])
    assert h["invested"] == 100 and h["value"] > 100
    assert "الشراء الدوري" in sent[-1]


def test_unapproved_assets_are_skipped(core, monkeypatch):
    monkeypatch.setattr(dca, "settings", dataclasses.replace(dca.settings, dca_allocation="SOL/USDT:100"))
    b = dca.run(core[0], now=MON_10, send=False)
    assert not b["buys"] and "شرعياً" in b["skipped"][0]["why"]


def test_real_money_dca_is_off_by_default(core):
    broker = core[0]
    broker.env = "live"
    b = dca.run(broker, now=MON_10, send=False)
    assert not b["buys"] and "DCA_LIVE" in b["skipped"][0]["why"]


def test_not_enough_cash_is_skipped_not_borrowed(core, monkeypatch):
    monkeypatch.setattr(dca, "settings", dataclasses.replace(dca.settings, dca_weekly_usd=10**9))
    b = dca.run(core[0], now=MON_10, send=False)
    assert not b["buys"] and len(b["skipped"]) == 2


# ── swing ────────────────────────────────────────────────────
def trend_then_crash(n_up=500, n_down=200):
    up = np.linspace(100, 300, n_up)
    down = np.linspace(300, 120, n_down)
    close = np.r_[up, down]
    idx = pd.date_range("2018-01-01", periods=len(close), freq="D", tz="UTC")
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1.0}, index=idx)


def test_trailing_stop_rides_trend_and_exits_the_crash():
    df = trend_then_crash()
    sig = pd.Series(False, index=df.index)
    sig.iloc[220] = True
    r = swing.simulate(df, sig, trail_atr=3, fee=0, slippage=0)
    t = r["trades"][0]
    assert t["reason"] == "trailing stop" and t["exit"] > 250          # kept most of 100 -> 300
    assert r["stats"]["max_drawdown_pct"] > r["stats"]["buy_and_hold_max_drawdown_pct"]  # smaller loss


@pytest.mark.parametrize("strategy", list(swing.STRATEGIES_AR))
def test_swing_signals_never_use_future_bars(strategy):
    df = synthetic_ohlcv(n=900, drift=0.001, seed=2)
    p = swing.GRIDS[strategy][0]
    assert swing.signals(df, strategy, p).iloc[:600].equals(swing.signals(df.iloc[:600], strategy, p))


def test_swing_research_reports_test_and_whole_history():
    fetch = lambda s, tf, y: synthetic_ohlcv(n=2500, drift=0.0008, seed=9)  # noqa: E731
    r = swing.research(["BTC/USDT"], fetch=fetch)
    res = r["per_symbol"]["BTC/USDT"]["results"]
    assert len(res) == 2 and all(x["verdict"] in ("promising", "weak", "fails", "insufficient") for x in res)
    assert all("whole" in x and "test" in x for x in res)
    rb = res[0]["robustness"]
    assert rb["settings"] == len(swing.GRIDS["breakout"]) and 0 <= rb["profitable"] <= rb["settings"]
