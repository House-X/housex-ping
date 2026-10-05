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
    assert len(app.tabs) == 4


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
