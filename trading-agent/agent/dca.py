"""The core: a fixed amount every week into approved assets, held for the long term.

Why: the backtests showed patience beat fast trading - buy-and-hold made +27% (BTC) and +34% (ETH)
in the same test window where every short-term strategy lost or barely broke even after fees.
Buying a fixed amount on a fixed day removes the need to time the market (more coins when the price
is low, fewer when it is high).

Rules:
  - only assets that pass the Sharia check right now; spot, cash-funded, no leverage
  - the bot never sells the core; the trader decides
  - one batch per week (DCA_WEEKDAY at DCA_HOUR_UTC); a week missed while the laptop was off is
    caught up at the next start, never doubled
  - real-money purchases stay off unless DCA_LIVE=true; LIVE_MAX_ORDER_USD caps each order
Everything is logged to data/dca.json and reported on Telegram.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import market_data, notify, sharia
from .config import ROOT, settings
from .indicators import ema

STORE = ROOT / "data" / "dca.json"


def allocation() -> dict[str, float]:
    """'BTC/USDT:60,ETH/USDT:40' -> {'BTC/USDT': 0.6, 'ETH/USDT': 0.4}"""
    parts = {}
    for item in settings.dca_allocation.split(","):
        if ":" in item:
            sym, w = item.split(":")
            parts[sym.strip().upper()] = float(w)
    total = sum(parts.values()) or 1
    return {s: w / total for s, w in parts.items()}


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"purchases": [], "last_run": None}


def _save(state: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE)


def _this_weeks_slot(now: datetime) -> datetime:
    start = (now - timedelta(days=(now.weekday() - settings.dca_weekday) % 7)).replace(
        hour=settings.dca_hour_utc, minute=0, second=0, microsecond=0)
    return start if start <= now else start - timedelta(days=7)


def due(now: datetime | None = None) -> bool:
    """True once the latest weekly slot has passed and no batch ran since it."""
    now = now or datetime.now(timezone.utc)
    last = _load().get("last_run")
    if not last:
        return _this_weeks_slot(now).date() == now.date()  # first ever batch: on the day, after the hour
    return datetime.fromisoformat(last) < _this_weeks_slot(now)


def next_run(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (_this_weeks_slot(now) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M UTC")


def trend_note(symbol: str = "BTC/USDT") -> str:
    try:
        d = market_data.fetch_ohlcv(symbol, "1d", 260)
        above = d["close"].iloc[-2] > ema(d["close"], 200).iloc[-2]
    except Exception:
        return ""
    return ("📈 الاتجاه الكبير صاعد (فوق متوسط 200 يوم)." if above else
            "📉 السوق تحت متوسط 200 يوم: الشراء الدوري مستمر، فهذه أوقات التجميع بأسعار أقل، "
            "لكن لا تضف مبالغ إضافية دون دراسة.")


def run(broker, now: datetime | None = None, reason: str = "scheduled", send: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    state = _load()
    live = getattr(broker, "env", None) == "live"
    batch = {"ts": now.isoformat(timespec="seconds"), "reason": reason, "buys": [], "skipped": []}
    if live and not settings.dca_live:
        batch["skipped"].append({"symbol": "*", "why": "الشراء الدوري بأموال حقيقية معطّل (DCA_LIVE=false)"})
    else:
        for sym, w in allocation().items():
            usd = round(settings.dca_weekly_usd * w, 2)
            sh = sharia.check(sym)
            if sh["status"] != "compliant":
                batch["skipped"].append({"symbol": sym, "why": f"غير معتمد شرعياً ({sh['status']})"})
                continue
            try:
                fill = broker.buy_core(sym, usd)
                batch["buys"].append(fill)
                state["purchases"].append({**fill, "ts": batch["ts"]})
            except Exception as e:
                batch["skipped"].append({"symbol": sym, "why": f"{type(e).__name__}: {e}"})
    state["last_run"] = now.isoformat(timespec="seconds")
    _save(state)
    if send:
        lines = ["🟢 الشراء الدوري الأسبوعي (النواة)"]
        lines += [f"• {b['symbol']}: {b['cost']:.2f}$ بسعر {b['price']:,.6g}" for b in batch["buys"]]
        lines += [f"⚠️ {s['symbol']}: {s['why']}" for s in batch["skipped"]]
        h = holdings()
        if h["positions"]:
            lines.append(f"\nإجمالي المستثمر: {h['invested']:,.2f}$ · القيمة الآن: {h['value']:,.2f}$ "
                         f"({h['pnl_pct']:+.1f}%)")
        note = trend_note()
        if note:
            lines.append(note)
        lines.append(f"الدفعة القادمة: {next_run(now)}")
        notify.send("\n".join(lines))
    return batch


def holdings(price_fn: Callable[[str], float] | None = None) -> dict:
    price_fn = price_fn or market_data.last_price
    agg: dict[str, dict] = {}
    for p in _load()["purchases"]:
        a = agg.setdefault(p["symbol"], {"symbol": p["symbol"], "units": 0.0, "invested": 0.0, "buys": 0})
        a["units"] += p["units"]
        a["invested"] += p["cost"]
        a["buys"] += 1
    rows, invested, value = [], 0.0, 0.0
    for a in agg.values():
        try:
            px = price_fn(a["symbol"])
        except Exception:
            px = None
        val = a["units"] * px if px else a["invested"]
        rows.append({**a, "avg_price": a["invested"] / a["units"] if a["units"] else None, "price": px,
                     "value": round(val, 2), "pnl": round(val - a["invested"], 2),
                     "pnl_pct": round((val / a["invested"] - 1) * 100, 1) if a["invested"] else 0.0})
        invested += a["invested"]
        value += val
    return {"positions": rows, "invested": round(invested, 2), "value": round(value, 2),
            "pnl": round(value - invested, 2),
            "pnl_pct": round((value / invested - 1) * 100, 1) if invested else 0.0}


def holdings_text() -> str:
    h = holdings()
    if not h["positions"]:
        return f"🟢 النواة فارغة بعد. أول شراء دوري: {next_run()}\nالخطة: " + " · ".join(
            f"{s} {w * settings.dca_weekly_usd:.0f}$" for s, w in allocation().items()) + " أسبوعياً"
    lines = ["🟢 النواة (استثمار طويل المدى)"]
    for r in h["positions"]:
        lines += [f"• {r['symbol']} — {r['buys']} دفعات", f"   المستثمر: {r['invested']:,.2f}$",
                  f"   متوسط السعر: {r['avg_price']:,.6g}", f"   القيمة الآن: {r['value']:,.2f}$ ({r['pnl_pct']:+.1f}%)"]
    lines.append(f"\nالإجمالي: {h['invested']:,.2f}$ ← {h['value']:,.2f}$ ({h['pnl_pct']:+.1f}%)")
    lines.append(f"الدفعة القادمة: {next_run()}")
    return "\n".join(lines)
