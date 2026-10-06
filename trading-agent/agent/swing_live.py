"""Live trend-riding ("the wave") for the symbols that passed the swing lab (default ETH/USDT, daily).

Once a day, after the daily candle closes (00:00 UTC = 03:00 Istanbul):
  1. Settings are re-chosen weekly exactly as the lab does (best on the first 70% of 9 years) and
     locked into a trade when it opens, so a later change never moves an open trade's stop.
  2. No open swing trade + a breakout on the last completed candle -> a proposal is sent to
     Telegram with ✅/❌. It stays valid 24h and is skipped if the price already ran more than
     SWING_MAX_CHASE_PCT above the breakout close (buying late ruins the reward/risk).
  3. Open swing trade -> the trailing stop is raised to (highest close since entry - k x ATR) and
     moved on the exchange; it is never lowered. The exit itself is the exchange stop order.

Orders go through the broker, so Sharia, no-leverage, 1% risk per trade, max position size and
LIVE_MAX_ORDER_USD are all enforced exactly as for any other trade. The take-profit leg of the
exchange OCO is parked far away (2x the entry): this strategy has no target, the trailing stop exits.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from . import market_data, notify, swing
from .config import ROOT, settings
from .indicators import atr

STORE = ROOT / "data" / "swing.json"
PARAMS_MAX_AGE_S = 7 * 86_400
PROPOSAL_TTL_S = 24 * 3600
FAR_TARGET = 2.0
RUN_AFTER_MIN = 10  # minutes after 00:00 UTC, so the exchange has finalised the daily candle


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"params": {}, "last_run_day": None, "proposals": {}, "positions": {}}


def _save(state: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE)


def symbols() -> list[str]:
    return [s.strip().upper() for s in settings.swing_symbols.split(",") if s.strip()]


def due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.hour == 0 and now.minute < RUN_AFTER_MIN:
        return False
    return _load().get("last_run_day") != now.date().isoformat()


def _completed(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop today's still-forming daily candle."""
    return df[df.index.normalize() < pd.Timestamp(now.date(), tz="UTC")]


def _params(state: dict, symbol: str, df: pd.DataFrame, now: float) -> dict:
    cached = state["params"].get(symbol)
    if cached and now - cached["ts"] < PARAMS_MAX_AGE_S:
        return cached
    r = swing.evaluate(df, "breakout")
    cached = {**r["params"], "ts": now, "verdict": r["verdict"], "robust": r["robustness"]}
    state["params"][symbol] = cached
    return cached


def _sync_closed(state: dict, broker) -> None:
    open_ids = {p["id"] for p in broker.state["positions"]}
    for pid in [pid for pid in state["positions"] if pid not in open_ids]:
        state["positions"].pop(pid)


def run(broker, now: datetime | None = None, fetch: Callable | None = None, send: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or (lambda s: market_data.fetch_daily_history(s, 9))
    expire_old(now.timestamp())
    state = _load()
    _sync_closed(state, broker)
    report = {"proposals": [], "raised": [], "errors": []}
    for sym in symbols():
        try:
            df = _completed(fetch(sym), now)
            p = _params(state, sym, df, now.timestamp())
            a = float(atr(df, 14).iloc[-1])
            close = float(df["close"].iloc[-1])
            mine = [(pid, t) for pid, t in state["positions"].items() if t["symbol"] == sym]
            if mine:
                pid, t = mine[0]
                t["peak"] = max(t["peak"], close)
                new_stop = t["peak"] - t["trail_atr"] * a
                pos = next(x for x in broker.state["positions"] if x["id"] == pid)
                if new_stop > pos["stop_loss"] * 1.002:  # ignore sub-0.2% nudges
                    old = pos["stop_loss"]
                    broker.modify_position(pid, stop_loss=round(new_stop, 2))
                    report["raised"].append({"symbol": sym, "from": old, "to": round(new_stop, 2)})
                    if send:
                        notify.send(f"🌊 رفعت الوقف المتحرك على {sym}\n{old:,.2f} ← {new_stop:,.2f}\n"
                                    f"أعلى إغلاق منذ الدخول: {t['peak']:,.2f}\n"
                                    f"إذا نزل السعر إلى الوقف تُغلق الصفقة تلقائياً.")
                continue
            if any(x["symbol"] == sym for x in state["proposals"].values()):
                continue
            if swing.signals(df, "breakout", p).iloc[-1]:
                level = float(df["high"].iloc[-p["lookback"] - 1:-1].max())
                prop = {"id": uuid.uuid4().hex[:6], "symbol": sym, "signal_close": close, "atr": a,
                        "trail_atr": p["trail_atr"], "lookback": p["lookback"], "level": level,
                        "created_ts": now.timestamp(), "day": str(df.index[-1])[:10]}
                state["proposals"][prop["id"]] = prop
                report["proposals"].append(prop)
                if send:
                    notify.send(proposal_text(prop),
                                buttons=[[("✅ اشترِ", f"sw:ok:{prop['id']}"), ("❌ تجاهل", f"sw:no:{prop['id']}")]])
        except Exception as e:
            report["errors"].append(f"{sym}: {type(e).__name__}: {e}")
    state["last_run_day"] = now.date().isoformat()
    _save(state)
    return report


def proposal_text(p: dict) -> str:
    stop = p["signal_close"] - p["trail_atr"] * p["atr"]
    return (f"🌊 إشارة ركوب موجة: {p['symbol']}\n"
            f"أغلق السعر أمس عند {p['signal_close']:,.2f} فوق أعلى قمة لآخر {p['lookback']} يوماً "
            f"({p['level']:,.2f}) وفوق متوسط 200 يوم.\n"
            f"الوقف المبدئي: {stop:,.2f} ({(stop / p['signal_close'] - 1) * 100:.1f}%)، ثم يرتفع مع السعر "
            f"ولا ينزل. لا يوجد هدف ثابت: نترك الربح يكبر.\n"
            f"الحجم: بحيث لا تتجاوز الخسارة عند الوقف {settings.risk_per_trade_pct:g}% من رأس المال.\n"
            f"الإشارة صالحة 24 ساعة، ولا أشتري إذا قفز السعر أكثر من {settings.swing_max_chase_pct:g}%.")


def approve(broker, prop_id: str, price_fn: Callable[[str], float] | None = None,
            now: float | None = None) -> dict:
    price_fn = price_fn or market_data.last_price
    now = now or time.time()
    state = _load()
    p = state["proposals"].pop(prop_id, None)
    _save(state)
    if not p:
        raise ValueError("الإشارة لم تعد موجودة (نُفّذت أو رُفضت سابقاً)")
    if now - p["created_ts"] > PROPOSAL_TTL_S:
        raise ValueError("انتهت صلاحية الإشارة (أكثر من 24 ساعة)")
    px = price_fn(p["symbol"])
    if px > p["signal_close"] * (1 + settings.swing_max_chase_pct / 100):
        raise ValueError(f"السعر قفز إلى {px:,.2f}، أبعد من {settings.swing_max_chase_pct:g}% فوق الإشارة؛ "
                         "لن أطارده")
    stop = round(px - p["trail_atr"] * p["atr"], 2)
    pos = broker.open_position(p["symbol"], stop_loss=stop, take_profit=round(px * FAR_TARGET, 2),
                               rationale=f"ركوب موجة: اختراق قمة {p['lookback']} يوماً، وقف متحرك "
                                         f"{p['trail_atr']}×ATR")
    state = _load()
    state["positions"][pos["id"]] = {"symbol": p["symbol"], "trail_atr": p["trail_atr"],
                                     "peak": float(pos["entry"]), "opened": p["day"]}
    _save(state)
    return pos


def reject(prop_id: str) -> None:
    state = _load()
    state["proposals"].pop(prop_id, None)
    _save(state)


def expire_old(now: float | None = None) -> None:
    now = now or time.time()
    state = _load()
    old = [k for k, v in state["proposals"].items() if now - v["created_ts"] > PROPOSAL_TTL_S]
    if old:
        for k in old:
            state["proposals"].pop(k)
        _save(state)


def status_text(broker) -> str:
    state = _load()
    _sync_closed(state, broker)
    lines = ["🌊 ركوب الموجة (يومي)"]
    for sym in symbols():
        p = state["params"].get(sym)
        lines.append(f"\n• {sym}")
        if p:
            lines.append(f"   الإعداد: اختراق {p['lookback']} يوماً · وقف {p['trail_atr']}×ATR")
            rb = p.get("robust") or {}
            if rb:
                lines.append(f"   الثبات التاريخي: {rb.get('profitable')}/{rb.get('settings')} إعدادات رابحة")
        t = next((t for t in state["positions"].values() if t["symbol"] == sym), None)
        if t:
            pos = next((x for x in broker.state["positions"] if x["symbol"] == sym), None)
            if pos:
                lines.append(f"   صفقة مفتوحة منذ {t['opened']} · الدخول {pos['entry']:,.2f}")
                lines.append(f"   الوقف المتحرك الآن: {pos['stop_loss']:,.2f}")
        elif any(x["symbol"] == sym for x in state["proposals"].values()):
            lines.append("   ⏳ إشارة بانتظار موافقتك: ابحث عن رسالة 🌊 في المحادثة")
        else:
            lines.append("   لا توجد إشارة: ننتظر اختراقاً جديداً. الصبر جزء من الاستراتيجية.")
    lines.append("\nالفحص التالي بعد إغلاق الشمعة اليومية (03:10 بتوقيت إسطنبول).")
    return "\n".join(lines)
