"""Telegram alerts, backtester and proactive explorer (offline)."""
import dataclasses

import pandas as pd
import pytest

from agent import backtest, explorer, notify
from agent.paper_broker import PaperBroker
from agent.scanner import technical_score
from test_offline import synthetic_ohlcv  # noqa: E402


# ── Telegram ───────────────────────────────────────────────────
@pytest.fixture
def telegram(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify, "_call", lambda method, **p: sent.append(p["text"]) or {"ok": True})
    return sent


def test_notify_is_silent_when_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert notify.send("hi") is False


def test_long_messages_are_split(telegram):
    assert notify.send("x" * 9000)
    assert [len(m) for m in telegram] == [4000, 4000, 1000]


def test_notify_never_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify, "_call", lambda *a, **k: 1 / 0)
    assert notify.send("hi") is False


def test_paper_broker_alerts_on_open_and_close(telegram, tmp_path):
    prices = {"BTC/USDT": 100.0}
    b = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: prices[s])
    b.open_position("BTC/USDT", 90, 130, "x")
    prices["BTC/USDT"] = 131
    b.check_stops()
    assert "صفقة جديدة" in telegram[0] and "BTC/USDT" in telegram[0]
    assert "إغلاق صفقة" in telegram[1] and "+300.00$" in telegram[1]


# ── Backtest ───────────────────────────────────────────────────
@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("drift", [0.004, -0.003, 0.0005])
def test_vectorised_score_matches_scanner(seed, drift):
    df = synthetic_ohlcv(n=400, drift=drift, seed=seed)
    live, hist = technical_score(df), backtest.score_frame(df).iloc[-1]
    assert (live["score"], live["setup"]) == (int(hist["score"]), hist["setup"])


def bars(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_entry_is_next_open_and_stop_wins_ties(monkeypatch):
    # signal on bar 1 only; bar 2 opens at 100 and touches both 90 and 130 -> stop assumed
    rows = [[100, 101, 99, 100, 1]] * 3
    rows[2] = [100, 140, 80, 100, 1]
    df = bars(rows)
    sig = pd.DataFrame({"score": [0, 90, 0], "setup": ["none", backtest.SETUP_BREAKOUT, "none"],
                        "atr": [5.0] * 3}, index=df.index)
    monkeypatch.setattr(backtest, "score_frame", lambda d: sig)
    r = backtest.run(df, warmup=0, fee=0)
    t = r["trades"][0]
    assert t["entry"] == 100 and t["exit"] == 90 and t["reason"] == "stop"
    assert t["r"] == pytest.approx(-1.0)


def test_target_hit_and_position_size_has_no_leverage(monkeypatch):
    rows = [[100, 101, 99, 100, 1]] * 2 + [[100, 101, 99.5, 100, 1], [101, 125, 100, 120, 1]]
    df = bars(rows)
    sig = pd.DataFrame({"score": [0, 90, 0, 0], "setup": ["none", backtest.SETUP_PULLBACK, "none", "none"],
                        "atr": [0.5] * 4}, index=df.index)
    monkeypatch.setattr(backtest, "score_frame", lambda d: sig)
    r = backtest.run(df, warmup=0, fee=0, reward_risk=2.0)
    t = r["trades"][0]
    assert t["reason"] == "target" and t["r"] == pytest.approx(2.0)
    # 1% risk with a 1-point stop would be 100 units = 10,000$ -> capped at 30% of equity
    assert t["pnl"] == pytest.approx(30 * 2, rel=1e-6)


def test_backtest_runs_on_realistic_data():
    r = backtest.run(synthetic_ohlcv(n=900, drift=0.001, seed=7), min_score=60)
    s = r["stats"]
    assert s["trades"] > 0 and -100 < s["max_drawdown_pct"] <= 0
    assert all(isinstance(v, (int, float, type(None))) for v in s.values())


def test_backtest_many_reports_errors_per_symbol():
    def fetch(sym, years):
        if sym == "BAD":
            raise ValueError("no data")
        return synthetic_ohlcv(n=600, seed=1)
    r = backtest.backtest(["GOOD", "BAD"], fetch=fetch, min_score=60)
    assert "stats" in r["per_symbol"]["GOOD"] and "error" in r["per_symbol"]["BAD"]


# ── Explorer ───────────────────────────────────────────────────
def opp(sym, score, setup="breakout / new highs", sharia="compliant"):
    return {"symbol": sym, "score": score, "setup": setup, "rsi14": 60, "sharia": {"status": sharia}}


SCANS = {
    "crypto": lambda: {"opportunities": [opp("ETH/USDT", 85), opp("SOL/USDT", 90, sharia="review_required"),
                                         opp("XRP/USDT", 50), opp("DOGE/USDT", 95, sharia="not_compliant")]},
    "bist": lambda: {"opportunities": [opp("BIMAS.IS", 80, "pullback to EMA20 in uptrend"),
                                       opp("ASELS.IS", 90, sharia="review_required")]},
}


class FakeAgent:
    def __init__(self):
        from agent.tools import ToolExecutor
        self.executor = ToolExecutor(None, approval_mode="deferred")
        self.prompts = []

    def ask(self, text):
        self.prompts.append(text)
        self.executor.pending.append({"id": "x"})  # must be discarded by the explorer
        return "## تقرير\nتفاصيل\n<<TELEGRAM>>\nETH: فكرة شراء\n<<END>>"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(explorer, "STORE", tmp_path / "opp.json")
    monkeypatch.setattr(explorer, "settings", dataclasses.replace(
        explorer.settings, explore_min_score=75, explore_ai_max_per_day=1, explore_every_hours=4))
    return tmp_path


def test_candidates_filter_score_sharia_and_markets(store):
    tradable, research = explorer.collect_candidates(SCANS)
    assert [c["symbol"] for c in tradable] == ["ETH/USDT", "BIMAS.IS"]
    assert [c["symbol"] for c in research] == ["SOL/USDT"]   # BIST review / blocked coins excluded


def test_explore_with_ai_parses_telegram_and_never_queues_orders(store, telegram):
    agent = FakeAgent()
    run = explorer.explore(scans=SCANS, agent_factory=lambda: agent)
    assert run["ai"] and run["telegram"] == "ETH: فكرة شراء"
    assert run["report"].startswith("## تقرير")
    assert not agent.executor.pending
    assert "مستكشف الفرص" in telegram[0]
    assert "Do NOT open" in agent.prompts[0]


def test_explore_dedupes_and_respects_ai_budget(store, telegram):
    explorer.explore(scans=SCANS, agent_factory=FakeAgent)
    second = explorer.explore(scans=SCANS, agent_factory=FakeAgent)   # same candidates -> nothing new
    assert not second["tradable"] and not second["ai"]
    assert len(telegram) == 1                                          # no repeat alert
    new = {"crypto": lambda: {"opportunities": [opp("BTC/USDT", 88)]}}
    third = explorer.explore(scans=new, agent_factory=FakeAgent)       # budget (1/day) used up
    assert third["tradable"] and not third["ai"] and "BTC/USDT" in third["telegram"]


def test_explore_due_schedule(store):
    assert explorer.due()
    explorer.explore(scans={}, send=False)
    assert not explorer.due()


def test_turkish_stocks_are_backtested_in_usd():
    df = bars([[100, 110, 90, 100, 1], [200, 220, 180, 200, 1]])          # price doubles in TRY
    fx = pd.Series([10.0, 20.0], index=df.index)                          # ...and so does USDTRY
    usd = backtest.in_usd(df, fx)
    assert list(usd["close"]) == [10.0, 10.0]                             # flat in USD


def test_backtest_reports_buy_and_hold_drawdown():
    s = backtest.run(synthetic_ohlcv(n=600, drift=0.001, seed=2), min_score=60)["stats"]
    assert s["buy_and_hold_max_drawdown_pct"] <= 0
