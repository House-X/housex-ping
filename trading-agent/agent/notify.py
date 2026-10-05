"""Telegram notifications. Silent no-op when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set,
and never raises: a failed alert must not break trading."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .config import ROOT

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram limit is 4096 characters per message


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def configured() -> bool:
    return bool(_token() and _chat_id())


def _call(method: str, **params) -> dict:
    url = API.format(token=_token(), method=method)
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
        return json.loads(r.read())


def send(text: str) -> bool:
    if not configured():
        return False
    try:
        for i in range(0, len(text), MAX_LEN):
            _call("sendMessage", chat_id=_chat_id(), text=text[i:i + MAX_LEN],
                  disable_web_page_preview="true")
        return True
    except Exception as e:  # network, bad token, blocked bot...
        print(f"[telegram] send failed: {e}")
        return False


# ── trade events (called by the brokers) ──────────────────────
def trade_opened(pos: dict, mode: str) -> None:
    auto = str(pos.get("rationale", "")).startswith("شراء تلقائي")
    send(f"{'🤖✅ شراء تلقائي نُفّذ' if auto else '🟢 صفقة جديدة'} ({mode})\n"
         f"{pos['symbol']}\n"
         f"الدخول: {pos['entry']:.6g}\nالوقف: {pos['stop_loss']:.6g}\nالهدف: {pos['take_profit']:.6g}\n"
         f"التكلفة: {pos.get('cost', 0):.2f}$ · أقصى خسارة: {pos.get('risk_amount', 0):.2f}$\n"
         f"الحماية: {pos.get('protection', 'local')}")


def trade_closed(closed: dict, mode: str) -> None:
    win = closed.get("pnl", 0) > 0
    send(f"{'✅' if win else '🔴'} إغلاق صفقة ({mode})\n"
         f"{closed['symbol']}\n"
         f"السبب: {closed.get('close_reason')}\n"
         f"الدخول: {closed['entry']:.6g} ← الخروج: {closed.get('exit', 0):.6g}\n"
         f"الربح/الخسارة: {closed.get('pnl', 0):+.2f}$ ({closed.get('r_multiple')}R)")


# ── first-time setup helper ───────────────────────────────────
def setup_chat_id() -> str:
    """Find the chat that messaged the bot, save TELEGRAM_CHAT_ID to .env and send a test message."""
    if not _token():
        return "TELEGRAM_BOT_TOKEN is missing in .env"
    updates = _call("getUpdates").get("result", [])
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
             for u in updates if "message" in u}
    if not chats:
        return "No messages found. Open your bot in Telegram, press Start / send any message, then retry."
    if len(chats) > 1:
        return "Several chats found, add the right one to .env manually: " + json.dumps(chats, ensure_ascii=False)
    chat_id = str(next(iter(chats)))
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    if "TELEGRAM_CHAT_ID=" not in text:
        env.write_text(text.rstrip("\n") + f"\nTELEGRAM_CHAT_ID={chat_id}\n", encoding="utf-8")
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    ok = send("✅ تم ربط وكيل التداول بتيليجرام. ستصلك هنا التنبيهات والفرص.")
    return f"Chat ID {chat_id} saved to .env. Test message {'sent' if ok else 'FAILED'}."
