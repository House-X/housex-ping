"""Short-term strategy engine: no look-ahead, costs charged, honest train/test split."""
import numpy as np
import pandas as pd
import pytest

from agent import shortterm
from test_offline import synthetic_ohlcv


def flat(n=260, px=100.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": px, "high": px * 1.001, "low": px * 0.999, "close": px, "volume": 1.0},
                        index=idx)


def test_entry_is_next_bar_open_and_time_stop_exits():
    df = flat()
    sig = pd.Series(False, index=df.index)
    sig.iloc[220] = True
    t = shortterm.simulate(df, sig, reward_risk=50, time_stop=5, fee=0, slippage=0)["trades"][0]
    assert t["entry_date"] == df.index[221]                       # the bar AFTER the signal
    assert t["reason"] == "time" and t["bars"] == 5


def test_costs_are_charged_on_both_sides():
    df = flat()
    sig = pd.Series(False, index=df.index)
    sig.iloc[220] = True
    r = shortterm.simulate(df, sig, reward_risk=50, time_stop=3)
    t = r["trades"][0]
    assert t["pnl"] < 0                                           # flat market: fees + slippage lose
    assert r["stats"]["fees_usd"] > 0


def test_stop_wins_when_bar_touches_both():
    df = flat()
    sig = pd.Series(False, index=df.index)
    sig.iloc[220] = True
    df.iloc[222, df.columns.get_loc("high")] = 200
    df.iloc[222, df.columns.get_loc("low")] = 1
    t = shortterm.simulate(df, sig, time_stop=50, fee=0, slippage=0)["trades"][0]
    assert t["reason"] == "stop"


@pytest.mark.parametrize("strategy", list(shortterm.STRATEGIES_AR))
def test_signals_never_use_future_bars(strategy):
    df = synthetic_ohlcv(n=900, drift=0.0005, seed=4)
    full = shortterm.signals(df, strategy)
    cut = shortterm.signals(df.iloc[:600], strategy)
    assert full.iloc[:600].equals(cut)                            # adding later bars changes nothing


def test_evaluate_reports_untouched_test_period_and_a_verdict():
    df = synthetic_ohlcv(n=3000, drift=0.0002, seed=7)
    r = shortterm.evaluate(df, "pullback")
    assert r["params"]["reward_risk"] in (1.0, 1.5, 2.0)
    assert r["test_from"] == str(df.index[2100])[:10]
    assert r["verdict"] in ("promising", "weak", "fails", "insufficient") and r["verdict_ar"]


def test_research_handles_errors_and_short_history():
    def fetch(s, tf, days):
        if s == "BAD/USDT":
            raise ValueError("boom")
        return synthetic_ohlcv(n=3000 if s == "BTC/USDT" else 100, seed=3)
    r = shortterm.research(["BTC/USDT", "NEW/USDT", "BAD/USDT"], fetch=fetch)
    assert len(r["per_symbol"]["BTC/USDT"]["results"]) == 3
    assert "need at least" in r["per_symbol"]["NEW/USDT"]["error"]
    assert "boom" in r["per_symbol"]["BAD/USDT"]["error"]


def test_random_walk_is_not_called_promising():
    # a driftless random walk has no edge: after costs, no strategy should look "promising" on most seeds
    verdicts = [shortterm.evaluate(synthetic_ohlcv(n=4000, drift=0.0, seed=s), st)["verdict"]
                for s in range(4) for st in shortterm.STRATEGIES_AR]
    assert verdicts.count("promising") <= 2
