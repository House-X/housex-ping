"""Price alerts and pre-approved automatic buys.

An alert watches one condition on one symbol:
    price_above / price_below   - live price crosses a level
    close_above / close_below   - the last COMPLETED daily close is beyond a level

Actions:
    notify    - Telegram message when the condition is met (armed immediately, no money moves)
    auto_buy  - a full plan (stop + target) that the trader approves ONCE in the browser; when the
                condition is met it is executed without asking again. Created as "proposed" and
                never executes before approval.

Safety at trigger time (auto_buy): the broker re-checks Sharia, risk %, reward/risk, cash, caps
and daily loss; the price must not have run more than AUTO_BUY_MAX_CHASE_PCT past the trigger;
plans expire; real-money auto-buys stay disabled unless AUTO_BUY_LIVE=true.

Statuses: proposed -> armed -> triggered (notify) | executed | failed;
          also rejected, cancelled, expired.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from . import market_data, notify
from .config import ROOT, settings

STORE = ROOT / "data" / "alerts.json"
CONDITIONS = ("price_above", "price_below", "close_above", "close_below")
ACTIVE = ("proposed", "armed")
COND_AR = {"price_above": "السعر فوق", "price_below": "السعر تحت",
           "close_above": "إغلاق يومي فوق", "close_below": "إغلاق يومي تحت"}
STOCK_RECHECK_S = 300  # Yahoo data: don't poll stocks more than every 5 minutes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-300:], indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE)  # atomic: the browser and the watcher both write this file


def describe(a: dict) -> str:
    return f"{a['symbol']} — {COND_AR.get(a['condition'], a['condition'])} {a['level']:g}"


def plan_text(a: dict) -> str:
    """Phone-friendly card for an auto-buy plan: the decision, the numbers that matter, the max loss."""
    lvl, sl, tp = a["level"], a["stop_loss"], a["take_profit"]
    rr = (tp - lvl) / (lvl - sl) if lvl > sl else 0
    risk = a.get("risk_pct") or settings.risk_per_trade_pct
    return (f"🤖 خطة شراء تلقائي بانتظار موافقتك\n{describe(a)}\n"
            f"الوقف: {sl:g} ({(sl / lvl - 1) * 100:+.1f}%) · الهدف: {tp:g} ({(tp / lvl - 1) * 100:+.1f}%)\n"
            f"العائد/المخاطرة: {rr:.1f} · أقصى خسارة: {risk:g}% من رأس المال\n"
            f"{a.get('note') or ''}\n"
            f"عند موافقتك أراقب الشرط وأشتري تلقائياً دون الرجوع إليك.").replace("\n\n", "\n")


def create(symbol: str, condition: str, level: float, action: str = "notify",
           stop_loss: float | None = None, take_profit: float | None = None,
           note: str = "", source: str = "chat", expires_days: int | None = None,
           risk_pct: float | None = None, send: bool = True) -> dict:
    symbol = symbol.upper().strip()
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}")
    if action not in ("notify", "auto_buy"):
        raise ValueError("action must be notify or auto_buy")
    if action == "auto_buy":
        if stop_loss is None or take_profit is None:
            raise ValueError("auto_buy needs stop_loss and take_profit")
        if not stop_loss < level < take_profit:
            raise ValueError("auto_buy plan needs stop_loss < trigger level < take_profit")

    items = _load()
    for a in items:  # no duplicates of an active alert
        if (a["status"] in ACTIVE and a["symbol"] == symbol and a["condition"] == condition
                and abs(a["level"] - level) <= 1e-9 * max(1, level) and a["action"] == action):
            return {**a, "duplicate": True}

    days = expires_days or settings.alert_expiry_days
    alert = {
        "id": uuid.uuid4().hex[:6], "symbol": symbol, "condition": condition, "level": float(level),
        "action": action, "stop_loss": stop_loss, "take_profit": take_profit, "risk_pct": risk_pct,
        "note": note, "source": source,
        "status": "proposed" if action == "auto_buy" else "armed",
        "created_at": _now(), "expires_ts": time.time() + days * 86400, "last_checked_ts": 0,
        "result": None,
    }
    items.append(alert)
    _save(items)
    if send:
        if action == "auto_buy":
            notify.send(plan_text(alert), buttons=[[("✅ وافق", f"al:ok:{alert['id']}"),
                                                    ("❌ ارفض", f"al:no:{alert['id']}")]])
        else:
            notify.send(f"🔔 تنبيه جديد قيد المراقبة\n{describe(alert)}\n{note}".strip(),
                        buttons=[[("🗑 إلغاء التنبيه", f"al:cx:{alert['id']}")]])
    return alert


def _update(alert_id: str, **changes) -> dict:
    items = _load()
    for a in items:
        if a["id"] == alert_id:
            a.update(changes)
            _save(items)
            return a
    raise ValueError(f"No alert {alert_id}")


def approve(alert_id: str) -> dict:
    a = next((x for x in _load() if x["id"] == alert_id), None)
    if not a or a["status"] != "proposed":
        raise ValueError("Only a proposed plan can be approved")
    return _update(alert_id, status="armed", approved_at=_now())


def reject(alert_id: str) -> dict:
    return _update(alert_id, status="rejected")


def cancel(alert_id: str) -> dict:
    a = next((x for x in _load() if x["id"] == alert_id), None)
    if not a or a["status"] not in ACTIVE:
        raise ValueError("Only an active alert can be cancelled")
    return _update(alert_id, status="cancelled")


def active() -> list[dict]:
    return [a for a in _load() if a["status"] in ACTIVE]


def history(limit: int = 30) -> list[dict]:
    return [a for a in reversed(_load()) if a["status"] not in ACTIVE][:limit]


def _met(a: dict, value: float) -> bool:
    return value >= a["level"] if a["condition"].endswith("above") else value <= a["level"]


def check(broker, price_fn: Callable[[str], float] = market_data.last_price,
          close_fn: Callable[[str], float] = market_data.last_completed_daily_close,
          now: float | None = None) -> list[dict]:
    """Evaluate armed alerts once. Call from the watcher loop. Returns what happened."""
    now = now or time.time()
    items, events, changed = _load(), [], False
    live_money = getattr(broker, "env", None) == "live"

    for a in items:
        if a["status"] not in ACTIVE:
            continue
        if now > a["expires_ts"]:
            a.update(status="expired", result="انتهت الصلاحية دون تحقق الشرط")
            changed = True
            notify.send(f"⌛ انتهت صلاحية: {describe(a)}")
            events.append(a)
            continue
        if a["status"] != "armed":
            continue
        is_stock = market_data.asset_class(a["symbol"]) != "crypto"
        if is_stock and now - a.get("last_checked_ts", 0) < STOCK_RECHECK_S:
            continue
        try:
            value = close_fn(a["symbol"]) if a["condition"].startswith("close") else price_fn(a["symbol"])
        except Exception as e:  # data hiccup: try again next loop
            print(f"[alerts] {a['symbol']}: {e}")
            continue
        a["last_checked_ts"], a["last_value"] = now, value
        changed = True
        if not _met(a, value):
            continue

        a["triggered_at"] = _now()
        if a["action"] == "notify":
            a.update(status="triggered", result=f"تحقق الشرط عند {value:g}")
            notify.send(f"🔔 تحقق الشرط!\n{describe(a)} (الآن {value:g})\n{a.get('note') or ''}\n"
                        "افتح الواجهة وادرسها مع الوكيل.".strip())
        else:
            a.update(**_auto_buy(a, broker, price_fn, live_money))
        events.append(a)

    if changed:
        _save(items)
    return events


def _auto_buy(a: dict, broker, price_fn, live_money: bool) -> dict:
    def fail(reason: str) -> dict:
        notify.send(f"⚠️ لم يُنفَّذ الشراء التلقائي\n{describe(a)}\nالسبب: {reason}")
        return {"status": "failed", "result": reason}

    if live_money and not settings.auto_buy_live:
        return fail("الشراء التلقائي بأموال حقيقية معطّل (AUTO_BUY_LIVE=false)")
    try:
        price = price_fn(a["symbol"])
    except Exception as e:
        return fail(f"تعذّر جلب السعر: {e}")
    chase = settings.auto_buy_max_chase_pct / 100
    if a["condition"].endswith("above") and price > a["level"] * (1 + chase):
        return fail(f"السعر قفز إلى {price:g}، أبعد من {settings.auto_buy_max_chase_pct}% فوق الشرط")
    try:
        pos = broker.open_position(a["symbol"], a["stop_loss"], a["take_profit"],
                                   rationale=f"شراء تلقائي بموافقة مسبقة: {describe(a)}. {a.get('note') or ''}",
                                   risk_pct=a.get("risk_pct"))
    except Exception as e:  # rules (Sharia, risk, R:R, cash, caps) are enforced by the broker
        return fail(str(e))
    # the broker already sends the "auto-buy executed" Telegram message (see notify.trade_opened)
    return {"status": "executed", "result": f"فُتحت الصفقة {pos['id']} عند {pos['entry']:g}",
            "position_id": pos["id"]}
