"""Phone desk: Telegram buttons and commands."""
import json

import pytest

from agent import alerts, notify, telegram_bot
from agent.paper_broker import PaperBroker


class FakeTelegram:
    def __init__(self):
        self.calls, self.updates = [], []

    def __call__(self, method, _timeout=15, **p):
        self.calls.append((method, p))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return {"ok": True, "result": out}
        return {"ok": True}

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]

    def edits(self):
        return [p["text"] for m, p in self.calls if m == "editMessageText"]


@pytest.fixture
def tg(tmp_path, monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notify, "_call", fake)
    monkeypatch.setattr(alerts, "STORE", tmp_path / "alerts.json")
    monkeypatch.setattr(telegram_bot, "OFFSET_FILE", tmp_path / "offset.json")
    return fake


@pytest.fixture
def desk(tmp_path, tg):
    broker = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: 100.0)
    return telegram_bot.PhoneDesk(broker)


def button(data, chat=42, uid=1):
    return {"update_id": uid, "callback_query": {
        "id": "cb", "data": data, "message": {"chat": {"id": chat}, "message_id": 7, "text": "خطة"}}}


def text(t, chat=42, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": t}}


def test_plan_message_has_approve_and_reject_buttons(tg):
    a = alerts.create("ETH/USDT", "price_above", 110, "auto_buy", stop_loss=100, take_profit=130)
    msg = tg.sent()[-1]
    kb = json.loads(msg["reply_markup"])["inline_keyboard"][0]
    assert [b["callback_data"] for b in kb] == [f"al:ok:{a['id']}", f"al:no:{a['id']}"]
    assert "العائد/المخاطرة: 2.0" in msg["text"]


def test_tapping_approve_arms_the_plan_and_removes_buttons(desk, tg):
    a = alerts.create("ETH/USDT", "price_above", 110, "auto_buy", stop_loss=100, take_profit=130)
    tg.updates = [button(f"al:ok:{a['id']}")]
    desk.poll(0)
    assert alerts.active()[0]["status"] == "armed"
    assert "وافقت" in tg.edits()[-1]
    tg.updates = [button(f"al:ok:{a['id']}", uid=2)]                    # a second tap can't re-approve
    desk.poll(0)
    assert "لم يُنفَّذ" in tg.edits()[-1]


def test_strangers_are_ignored(desk, tg):
    a = alerts.create("ETH/USDT", "price_above", 110, "auto_buy", stop_loss=100, take_profit=130)
    tg.updates = [button(f"al:ok:{a['id']}", chat=999), text("/status", chat=999, uid=2)]
    n_sent = len(tg.sent())
    desk.poll(0)
    assert alerts.active()[0]["status"] == "proposed"
    assert len(tg.sent()) == n_sent and not tg.edits()


def test_offset_advances_so_updates_are_never_replayed(desk, tg):
    tg.updates = [text("/help", uid=5)]
    desk.poll(0)
    assert desk.offset == 6
    assert telegram_bot.PhoneDesk(desk.broker).offset == 6               # survives a restart


def test_first_start_skips_old_messages(desk, tg):
    tg.updates = [text("/start", uid=3)]
    desk.start()
    assert desk.offset == 4 and not tg.sent()


def test_status_and_close_position_need_confirmation(desk, tg):
    pos = desk.broker.open_position("BTC/USDT", stop_loss=90, take_profit=120, rationale="t")
    tg.updates = [text("/status")]
    desk.poll(0)
    assert "BTC/USDT" in tg.sent()[-1]["text"]
    tg.updates = [button(f"ps:ask:{pos['id']}", uid=2)]
    desk.poll(0)
    assert desk.broker.state["positions"]                                # asking doesn't close
    tg.updates = [button(f"ps:yes:{pos['id']}", uid=3)]
    desk.poll(0)
    assert not desk.broker.state["positions"] and "أُغلقت" in tg.edits()[-1]


class FakeExecutor:
    def __init__(self):
        self.pending, self.done = [], []

    def approve(self, i):
        self.pending = [p for p in self.pending if p["id"] != i]
        self.done.append(i)
        return {"action": "OPEN TRADE", "result": {"ok": True}}

    def reject(self, i):
        self.pending = [p for p in self.pending if p["id"] != i]
        return {"action": "OPEN TRADE", "result": "rejected"}


class FakeAgent:
    def __init__(self):
        self.executor, self.notes, self.asked = FakeExecutor(), [], []

    def ask(self, t):
        self.asked.append(t)
        self.executor.pending.append({"id": "q1", "action": "OPEN TRADE", "details": {
            "symbol": "ETH/USDT", "entry": 100, "stop_loss": 95, "take_profit": 110,
            "sizing": {"cost": 500, "risk_amount": 25}}})
        return "أنصح بالشراء"


def test_free_text_goes_to_agent_and_orders_come_back_with_buttons(desk, tg):
    agent = FakeAgent()
    desk.agent_factory = lambda: agent
    tg.updates = [text("حلل ETH")]
    desk.poll(0)
    assert "Telegram" in agent.asked[0] and agent.asked[0].endswith("حلل ETH")
    order = tg.sent()[-1]
    assert "بانتظار موافقتك" in order["text"] and "ag:ok:q1" in order["reply_markup"]
    tg.updates = [button("ag:ok:q1", uid=2)]
    desk.poll(0)
    assert agent.executor.done == ["q1"] and "APPROVED" in agent.notes[0]


def test_poll_never_raises_on_network_errors(desk, monkeypatch):
    monkeypatch.setattr(notify, "get_updates", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda s: None)
    assert desk.poll(0) == 0


def test_core_command_and_buy_now_needs_confirmation(desk, tg, tmp_path, monkeypatch):
    from agent import dca
    monkeypatch.setattr(dca, "STORE", tmp_path / "dca.json")
    monkeypatch.setattr(dca, "trend_note", lambda symbol="BTC/USDT": "")
    tg.updates = [text("/core")]
    desk.poll(0)
    assert "dc:ask" in tg.sent()[-1]["reply_markup"]
    tg.updates = [button("dc:ask", uid=2)]
    desk.poll(0)
    assert not dca.holdings(price_fn=lambda s: 100.0)["positions"]    # asking buys nothing
    tg.updates = [button("dc:yes", uid=3)]
    desk.poll(0)
    assert dca.holdings(price_fn=lambda s: 100.0)["invested"] > 0


def test_swing_buttons_and_status(desk, tg, tmp_path, monkeypatch):
    from agent import swing_live
    monkeypatch.setattr(swing_live, "STORE", tmp_path / "swing.json")
    opened = []
    monkeypatch.setattr(swing_live, "approve",
                        lambda broker, i: opened.append(i) or {"symbol": "ETH/USDT", "entry": 100.0, "stop_loss": 90.0})
    tg.updates = [button("sw:ok:abc")]
    desk.poll(0)
    assert opened == ["abc"] and "اشتريت" in tg.edits()[-1]
    tg.updates = [text("/swing", uid=2)]
    desk.poll(0)
    assert "ركوب الموجة" in tg.sent()[-1]["text"]


def test_second_watcher_conflict_warns_once(desk, tg, monkeypatch):
    def conflict(*a, **k):
        raise RuntimeError("HTTP Error 409: Conflict")
    monkeypatch.setattr(notify, "get_updates", conflict)
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda s: None)
    desk.poll(0)
    desk.poll(0)
    warnings = [m for m in tg.sent() if "مراقب آخر" in m["text"]]
    assert len(warnings) == 1


def test_status_includes_core_holdings(desk, tg, tmp_path, monkeypatch):
    from agent import dca
    monkeypatch.setattr(dca, "STORE", tmp_path / "dca.json")
    monkeypatch.setattr(dca, "holdings", lambda price_fn=None: {
        "positions": [{"symbol": "BTC/USDT"}], "value": 100.0, "invested": 99.0, "pnl": 1.0, "pnl_pct": 1.0})
    text = desk.status_text()
    assert "الإجمالي مع النواة" in text and "النواة: 100.00$" in text


def test_setswing_command_filters_unapproved(desk, tg, tmp_path, monkeypatch):
    from agent import swing_live
    monkeypatch.setattr(swing_live, "STORE", tmp_path / "swing.json")
    tg.updates = [text("/setswing eth near doge")]
    desk.poll(0)
    msg = tg.sent()[-1]["text"]
    assert "ETH · NEAR" in msg and "DOGE/USDT" in msg
    assert swing_live.symbols() == ["ETH/USDT", "NEAR/USDT"]
