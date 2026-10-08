"""Browser UI smoke test with a fake agent: chat, deferred approval, approve/reject buttons."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tools import ToolExecutor  # noqa: E402


class FakeAgent:
    def __init__(self):
        self.executor = ToolExecutor(broker=None, approval_mode="deferred")
        self.notes, self.executed = [], []
        self.on_text = self.on_tool = None

    def ask(self, text):
        self.on_tool("analyze_market", {"symbol": "ETH/USDT"})
        self.on_text("تحليل **ETH/USDT**: الاتجاه صاعد.")
        details = {"symbol": "ETH/USDT", "entry": 100, "stop_loss": 90, "take_profit": 130,
                   "reward_risk": 3.0, "sizing": {"units": 1, "cost": 100, "risk_amount": 10},
                   "sharia": {"status": "compliant"}, "rationale": "اختبار"}
        self.executor._gate("OPEN TRADE", details, lambda: self.executed.append(1) or {"opened": "ok"})
        return ""


@pytest.fixture
def app():
    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["agent"] = FakeAgent()
    return at.run()


def test_app_renders_without_errors(app):
    assert not app.exception
    assert len(app.tabs) == 7


def test_chat_then_approve(app):
    agent = app.session_state["agent"]
    app.chat_input[0].set_value("حلل ETH").run()
    assert not app.exception
    assert "الاتجاه صاعد" in app.session_state["chat"][-1]["content"]
    assert len(agent.executor.pending) == 1                       # waiting for the trader
    ok = next(b for b in app.button if b.key and b.key.startswith("ok_"))
    ok.click().run()
    assert agent.executed == [1] and not agent.executor.pending
    assert "APPROVED" in agent.notes[0]


def test_chat_then_reject(app):
    agent = app.session_state["agent"]
    app.chat_input[0].set_value("حلل ETH").run()
    no = next(b for b in app.button if b.key and b.key.startswith("no_"))
    no.click().run()
    assert agent.executed == [] and "REJECTED" in agent.notes[0]


def test_study_button_sends_candidate_to_chat(tmp_path, monkeypatch):
    import json

    from agent import explorer
    store = tmp_path / "opp.json"
    store.write_text(json.dumps({"runs": [{
        "ts": "2026-10-05T12:00:00+00:00", "ai": True, "report": "## 📌 الخلاصة\nفرصة واحدة", "telegram": "",
        "tradable": [{"market": "crypto", "symbol": "ETH/USDT", "score": 85,
                      "setup": "breakout / new highs", "sharia": "compliant"}],
        "research": []}], "seen": {}, "ai_runs": {}, "last_run_ts": 0}), encoding="utf-8")
    monkeypatch.setattr(explorer, "STORE", store)

    at = AppTest.from_file("../app.py", default_timeout=30)
    agent = FakeAgent()
    agent.prompts = []
    original = agent.ask
    agent.ask = lambda text: agent.prompts.append(text) or original(text)
    at.session_state["agent"] = agent
    at.run()
    study = next(b for b in at.button if b.key and b.key.startswith("study_"))
    study.click().run()
    assert not at.exception
    assert agent.prompts and "ETH/USDT" in agent.prompts[0] and "اختراق قمة جديدة" in agent.prompts[0]


def test_alerts_tab_approves_auto_buy_plan(tmp_path, monkeypatch):
    from agent import alerts, notify
    monkeypatch.setattr(alerts, "STORE", tmp_path / "alerts.json")
    monkeypatch.setattr(notify, "send", lambda t, **kw: True)
    plan = alerts.create("ETH/USDT", "close_above", 2807, "auto_buy", stop_loss=2650, take_profit=3100)
    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["agent"] = FakeAgent()
    at.run()
    assert any("التنبيهات" in t.label and "🔴" in t.label for t in at.tabs)
    next(b for b in at.button if b.key == f"al_ok_{plan['id']}").click().run()
    assert not at.exception
    assert alerts.active()[0]["status"] == "armed"


def test_every_tool_has_an_arabic_label():
    from agent.tools import CLIENT_TOOLS
    src = (__import__("pathlib").Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert [t["name"] for t in CLIENT_TOOLS if f'"{t["name"]}":' not in src] == []


def test_shortterm_section_runs_and_shows_verdicts(monkeypatch):
    from agent import market_data
    from test_offline import synthetic_ohlcv
    monkeypatch.setattr(market_data, "fetch_intraday_history",
                        lambda s, tf, days: synthetic_ohlcv(n=2500, drift=0.0002, seed=5))
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.session_state["agent"] = FakeAgent()
    at.run()
    next(b for b in at.button if "المضاربة السريعة" in b.label).click().run()
    assert not at.exception
    res = at.session_state["st_res"]
    assert len(res["per_symbol"]["BTC/USDT"]["results"]) == 3
    assert any("الحكم" in df.value.columns for df in at.dataframe)


def test_swing_section_runs(monkeypatch):
    from agent import market_data
    from test_offline import synthetic_ohlcv
    monkeypatch.setattr(market_data, "fetch_daily_history",
                        lambda s, years: synthetic_ohlcv(n=2000, drift=0.0008, seed=6))
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.session_state["agent"] = FakeAgent()
    at.run()
    next(b for b in at.button if "ركوب الموجة" in b.label).click().run()
    assert not at.exception
    assert len(at.session_state["sw_res"]["per_symbol"]["BTC/USDT"]["results"]) == 2


def test_robustness_lab_section(monkeypatch, tmp_path):
    from agent import market_data, swing_live
    from test_offline import synthetic_ohlcv
    monkeypatch.setattr(swing_live, "STORE", tmp_path / "swing.json")
    monkeypatch.setattr(market_data, "fetch_daily_history",
                        lambda s, years: synthetic_ohlcv(n=1500, drift=0.0008, seed=len(s)))
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.session_state["agent"] = FakeAgent()
    at.run()
    next(b for b in at.button if "افحص كل العملات" in b.label).click().run()
    assert not at.exception
    lab = at.session_state["lab"]
    assert len(lab["rows"]) + len(lab["skipped"]) >= 20
    pick = [r["symbol"] for r in lab["rows"]][:2]
    at.multiselect(key="lab_pick").set_value(pick).run()
    next(b for b in at.button if "اعتمد المختارة" in b.label).click().run()
    assert not at.exception and swing_live.symbols() == pick
