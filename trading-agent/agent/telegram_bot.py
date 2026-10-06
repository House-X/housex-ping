"""Run the desk from the phone: Telegram buttons and commands, handled by the watcher (main.py --watch).

Buttons (callback_data, max 64 bytes):
  al:ok:<id> / al:no:<id>   approve / reject a proposed auto-buy plan
  al:cx:<id>                cancel an active alert or plan
  ps:ask:<id>               ask to close a position -> ps:yes:<id> / ps:keep:<id>
  ag:ok:<id> / ag:no:<id>   approve / reject an order the agent queued during a phone chat

Security: only updates from TELEGRAM_CHAT_ID are handled; everything else is ignored. Every order
still goes through the broker, so Sharia, no-leverage and risk rules are re-checked at execution.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from . import alerts, notify
from .config import ROOT, settings

OFFSET_FILE = ROOT / "data" / "telegram_offset.json"

COMMANDS = [
    ("status", "الرصيد والصفقات المفتوحة"),
    ("plans", "الخطط والتنبيهات الفعّالة"),
    ("positions", "الصفقات المفتوحة مع زر إغلاق"),
    ("core", "النواة: الشراء الدوري والاستثمار الطويل"),
    ("swing", "ركوب الموجة: الإشارات والوقف المتحرك"),
    ("explore", "ابحث عن فرص الآن"),
    ("new", "محادثة جديدة مع الوكيل"),
    ("help", "طريقة الاستخدام"),
]

HELP = """📱 مكتبك على الهاتف

الأوامر:
/status — الرصيد والصفقات
/plans — الخطط والتنبيهات (موافقة / إلغاء)
/positions — الصفقات المفتوحة (إغلاق)
/core — النواة: الشراء الدوري الأسبوعي
/swing — ركوب الموجة: الإشارات والوقف المتحرك
/explore — ابحث عن فرص الآن
/new — ابدأ محادثة جديدة

أو اكتب أي سؤال مباشرة، مثل:
حلّل BTC وهل أشتري اليوم؟

كل صفقة تحتاج ضغطة ✅ منك، والقواعد الشرعية وقواعد المخاطرة تُفحص من جديد لحظة التنفيذ."""

PHONE_HINT = ("[The trader is writing from Telegram on the phone: answer briefly in plain text, no tables "
              "or headings, max ~1200 characters. Orders you queue are shown to them with ✅/❌ buttons.]\n")

ACTION_AR = {"OPEN TRADE": "فتح صفقة", "MODIFY TRADE": "تعديل صفقة", "CLOSE TRADE": "إغلاق صفقة"}


def _load_offset() -> int | None:
    try:
        return json.loads(OFFSET_FILE.read_text(encoding="utf-8"))["offset"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _n(x) -> str:
    return "-" if x is None else f"{x:,.6g}" if isinstance(x, (int, float)) else str(x)


class PhoneDesk:
    def __init__(self, broker, agent_factory: Callable | None = None, explore_fn: Callable | None = None):
        self.broker = broker
        self.agent_factory = agent_factory
        self.explore_fn = explore_fn
        self.agent = None
        self.offset = _load_offset()
        self.conflict_warned = False

    # ── polling ────────────────────────────────────────────
    def start(self) -> None:
        notify.set_commands(COMMANDS)
        if self.offset is None:  # first run: skip old messages (e.g. the setup "/start")
            old = notify.get_updates(None, timeout=0)
            self._advance(max((u["update_id"] for u in old), default=-1) + 1 if old else None)

    def _advance(self, offset: int | None) -> None:
        if offset is not None:
            self.offset = offset
            _save_offset(offset)

    def poll(self, timeout: int = 25) -> int:
        """Long-poll Telegram (returns at once when the trader taps a button). Never raises."""
        try:
            updates = notify.get_updates(self.offset, timeout=timeout)
        except Exception as e:
            print(f"[telegram] poll failed: {e}")
            if "409" in str(e) and not self.conflict_warned:
                # Telegram allows one reader per bot: another watcher (laptop + server?) is running,
                # and two watchers would also double the weekly core buys.
                self.conflict_warned = True
                notify.send("⚠️ يوجد مراقب آخر يعمل على جهاز ثانٍ بنفس البوت.\n"
                            "شغّل مراقباً واحداً فقط (السيرفر)، وأغلق نافذة المراقب على اللاب توب، "
                            "وإلا قد تتكرر عمليات الشراء.")
            time.sleep(min(timeout, 5))
            return 0
        for u in updates:
            self._advance(u["update_id"] + 1)  # first, so a crashing update is never replayed
            try:
                self.handle(u)
            except Exception as e:
                notify.send(f"⚠️ تعذّر تنفيذ الطلب: {type(e).__name__}: {e}")
        return len(updates)

    def handle(self, u: dict) -> None:
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != notify._chat_id():
                return
            self.on_button(cq)
        elif "message" in u:
            msg = u["message"]
            if str(msg.get("chat", {}).get("id")) != notify._chat_id():
                return
            self.on_text((msg.get("text") or "").strip())

    # ── buttons ────────────────────────────────────────────
    def on_button(self, cq: dict) -> None:
        kind, _, rest = cq.get("data", "").partition(":")
        verb, _, ident = rest.partition(":")
        msg, original = cq["message"], cq["message"].get("text", "")
        try:
            if kind == "al":
                result = self._alert_button(verb, ident)
            elif kind == "ps":
                result = self._position_button(verb, ident)
            elif kind == "sw":
                result = self._swing_button(verb, ident)
            elif kind == "dc":
                result = self._dca_button(verb)
            elif kind == "ag":
                result = self._agent_button(verb, ident)
            else:
                result = "زر غير معروف"
        except Exception as e:
            result = f"❌ لم يُنفَّذ: {e}"
        notify.answer_button(cq["id"], result.splitlines()[0])
        if result:
            notify.edit(msg, f"{original}\n\n{result}")

    def _alert_button(self, verb: str, ident: str) -> str:
        if verb == "ok":
            a = alerts.approve(ident)
            return f"✅ وافقت. الخطة مفعّلة الآن: {alerts.describe(a)}\nسأشتري تلقائياً عند تحقق الشرط."
        if verb == "no":
            alerts.reject(ident)
            return "❌ رفضت الخطة، ولن تُنفَّذ."
        if verb == "cx":
            alerts.cancel(ident)
            return "🗑 أُلغي."
        return "زر غير معروف"

    def _position_button(self, verb: str, ident: str) -> str:
        if verb == "ask":
            notify.send(f"⚠️ تأكيد إغلاق الصفقة {ident} بسعر السوق الآن؟",
                        buttons=[[("نعم، أغلق", f"ps:yes:{ident}"), ("لا، أبقها", f"ps:keep:{ident}")]])
            return ""
        if verb == "yes":
            c = self.broker.close_position(ident, reason="أغلقها المتداول من تيليجرام")
            return f"✅ أُغلقت. الربح/الخسارة: {c.get('pnl', 0):+.2f}$"
        if verb == "keep":
            return "👍 الصفقة باقية كما هي."
        return "زر غير معروف"

    def _swing_button(self, verb: str, ident: str) -> str:
        from . import swing_live
        if verb == "ok":
            pos = swing_live.approve(self.broker, ident)
            return (f"✅ اشتريت {pos['symbol']} بسعر {pos['entry']:,.2f}\n"
                    f"الوقف المبدئي {pos['stop_loss']:,.2f}، وسأرفعه كل يوم مع السعر.")
        if verb == "no":
            swing_live.reject(ident)
            return "❌ تجاهلت الإشارة."
        return "زر غير معروف"

    def _dca_button(self, verb: str) -> str:
        from . import dca
        if verb == "ask":
            plan = " · ".join(f"{s} {w * settings.dca_weekly_usd:.0f}$" for s, w in dca.allocation().items())
            notify.send(f"🛒 تأكيد شراء دفعة النواة الآن؟\n{plan}",
                        buttons=[[("نعم، اشترِ", "dc:yes"), ("لا", "dc:no")]])
            return ""
        if verb == "yes":
            b = dca.run(self.broker, reason="manual")
            return f"✅ نُفّذت {len(b['buys'])} عمليات شراء" + (f"، وتُخطّيت {len(b['skipped'])}" if b["skipped"] else "")
        if verb == "no":
            return "👍 لم يُشترَ شيء."
        return "زر غير معروف"

    def _agent_button(self, verb: str, ident: str) -> str:
        if not self.agent:
            return "انتهت المحادثة؛ اطلب الصفقة من جديد."
        if verb == "ok":
            try:
                res = self.agent.executor.approve(ident)
            except Exception as e:
                self.agent.notes.append(f"Trader approved {ident} but it failed: {e}")
                raise
            self.agent.notes.append(f"Trader APPROVED {res['action']} {ident}. Result: "
                                    + json.dumps(res["result"], ensure_ascii=False, default=str)[:1500])
            return f"✅ نُفّذ: {ACTION_AR.get(res['action'], res['action'])}"
        if verb == "no":
            res = self.agent.executor.reject(ident)
            self.agent.notes.append(f"Trader REJECTED {res['action']} {ident}.")
            return "❌ رُفض، ولن يُنفَّذ."
        return "زر غير معروف"

    # ── commands and free text ─────────────────────────────
    def on_text(self, text: str) -> None:
        cmd = text.split()[0].split("@")[0].lower() if text else ""
        if cmd in ("/start", "/help", "مساعدة"):
            notify.send(HELP)
        elif cmd in ("/status", "الحالة"):
            notify.send(self.status_text())
        elif cmd in ("/plans", "الخطط"):
            self.send_plans()
        elif cmd in ("/positions", "الصفقات"):
            self.send_positions()
        elif cmd in ("/core", "النواة"):
            from . import dca
            notify.send(dca.holdings_text(), buttons=[[("🛒 اشترِ دفعة الآن", "dc:ask")]])
        elif cmd in ("/swing", "الموجة"):
            from . import swing_live
            notify.send(swing_live.status_text(self.broker))
        elif cmd in ("/explore", "استكشف"):
            notify.send("🔎 أبحث عن فرص الآن... قد يستغرق ذلك بضع دقائق.")
            run = self.explore_fn() if self.explore_fn else None
            if run is not None and not run.get("sent"):
                notify.send(run.get("telegram") or run.get("report") or "لا توجد فرص جديدة قوية الآن.")
        elif cmd in ("/new", "جديد"):
            self.agent = None
            notify.send("🆕 بدأنا محادثة جديدة.")
        elif text:
            self.ask_agent(text)

    def status_text(self) -> str:
        from . import dca
        acc = self.broker.account()
        try:
            core = dca.holdings()
        except Exception:
            core = {"positions": [], "value": 0.0}
        lines = [f"📊 الحساب ({acc.get('mode')})"]
        if core["positions"]:  # core coins sit in the wallet but outside the trading account
            lines += [f"💼 الإجمالي مع النواة: {acc.get('equity', 0) + core['value']:,.2f}$",
                      f"🟢 النواة: {core['value']:,.2f}$ ({core['pnl_pct']:+.1f}%)",
                      f"📈 حساب التداول: {acc.get('equity', 0):,.2f}$"]
        else:
            lines.append(f"القيمة الإجمالية: {acc.get('equity', 0):,.2f}$")
        lines += [f"النقد المتاح: {acc.get('free_cash', 0):,.2f}$",
                 f"ربح غير محقق: {acc.get('unrealized_pnl', 0):+,.2f}$ · ربح اليوم: "
                 f"{acc.get('today_realized_pnl', 0):+,.2f}$"]
        pos = acc.get("open_positions") or []
        lines.append(f"\nالصفقات المفتوحة: {len(pos)}")
        for p in pos:
            lines += [f"• {p['symbol']}", f"   الدخول: {_n(p['entry'])}",
                      f"   الآن: {_n(p.get('current_price'))}",
                      f"   الربح/الخسارة: {p.get('unrealized_pnl', 0):+.2f}$"]
        act = alerts.active()
        lines.append(f"\nخطط بانتظار موافقتك: {sum(a['status'] == 'proposed' for a in act)} · "
                     f"قيد المراقبة: {sum(a['status'] == 'armed' for a in act)}")
        return "\n".join(lines)

    def send_plans(self) -> None:
        act = alerts.active()
        if not act:
            notify.send("لا توجد خطط أو تنبيهات فعّالة.")
            return
        for a in act:
            if a["status"] == "proposed":
                notify.send(alerts.plan_text(a), buttons=[[("✅ وافق", f"al:ok:{a['id']}"),
                                                           ("❌ ارفض", f"al:no:{a['id']}")]])
            else:
                kind = "🤖 شراء تلقائي مفعّل" if a["action"] == "auto_buy" else "🔔 تنبيه"
                notify.send(f"{kind}\n{alerts.describe(a)}\n{a.get('note') or ''}".strip(),
                            buttons=[[("🗑 إلغاء", f"al:cx:{a['id']}")]])

    def send_positions(self) -> None:
        pos = self.broker.account().get("open_positions") or []
        if not pos:
            notify.send("لا توجد صفقات مفتوحة.")
            return
        for p in pos:
            notify.send(f"📈 {p['symbol']}\nالدخول: {_n(p['entry'])} · الآن: {_n(p.get('current_price'))}\n"
                        f"الوقف: {_n(p['stop_loss'])} · الهدف: {_n(p['take_profit'])}\n"
                        f"الربح/الخسارة: {p.get('unrealized_pnl', 0):+.2f}$ ({_n(p.get('r_multiple'))}R)",
                        buttons=[[("🔴 إغلاق الصفقة", f"ps:ask:{p['id']}")]])

    def ask_agent(self, text: str) -> None:
        if self.agent is None:
            if self.agent_factory:
                self.agent = self.agent_factory()
            else:
                from .core import TradingAgent
                self.agent = TradingAgent(self.broker, approval_mode="deferred", on_text=lambda t: None,
                                          on_tool=lambda n, a: None)
        notify.send("⏳ أعمل على طلبك...")
        answer = self.agent.ask(PHONE_HINT + text).strip()
        notify.send(answer or "تم.")
        for item in list(self.agent.executor.pending):
            d, s = item["details"], item["details"].get("sizing") or {}
            notify.send(f"✋ بانتظار موافقتك: {ACTION_AR.get(item['action'], item['action'])}\n"
                        f"{d.get('symbol') or d.get('position_id')}\n"
                        f"الدخول: {_n(d.get('entry'))} · الوقف: {_n(d.get('stop_loss'))} · "
                        f"الهدف: {_n(d.get('take_profit'))}\n"
                        f"التكلفة: {_n(s.get('cost'))}$ · أقصى خسارة: {_n(s.get('risk_amount'))}$",
                        buttons=[[("✅ نفّذ", f"ag:ok:{item['id']}"), ("❌ ارفض", f"ag:no:{item['id']}")]])
