"""Proactive opportunity explorer: the agent looks for ideas on its own and reports them.

One cycle:
  1. Free scans: Binance established coins, Binance new listings, Borsa Istanbul.
  2. Keep strong long setups (score >= EXPLORE_MIN_SCORE). Tradable = Sharia-compliant; strong
     coins that are not yet approved become "research candidates" (never traded automatically).
  3. Drop anything already reported in the last 24h for the same setup (no spam).
  4. If the daily AI budget allows, the agent researches the best few (technicals, news, Sharia)
     and writes a short brief. Otherwise a free scanner-only summary is sent.
  5. Saved to data/opportunities.json (shown in the browser) and sent to Telegram.

The explorer never places orders: trades still need the trader's explicit approval.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Callable

from . import notify, scanner
from .config import ROOT, settings

STORE = ROOT / "data" / "opportunities.json"
COOLDOWN_S = 24 * 3600
TELEGRAM_START, TELEGRAM_END = "<<TELEGRAM>>", "<<END>>"

EXPLORE_PROMPT = """\
[Proactive exploration - the trader did not ask; you are scouting for them]
The free scanners flagged these candidates (JSON below). Do NOT open, modify or close any trade.

Research the best tradable candidates (max 3) and the most interesting research candidates (max 2):
run analyze_market, check the news with web_search, and for coins use research_crypto.
Be selective: "nothing worth buying today" is a perfectly good answer.
For every WATCH item on a TRADABLE (Sharia-compliant) candidate with a clear price trigger, call
create_alert (action "notify") so the trader is told when it happens. Never create alerts for
research candidates: they cannot be bought, so an alert on them is only noise. For a strong TRADE IDEA on an approved coin with a clear trigger, you may
also create an "auto_buy" proposal with stop and target - it waits for the trader's approval.

Write the WHOLE report in simple Arabic for a non-specialist, using exactly this layout:

## 📌 الخلاصة
Two sentences: is there anything worth doing today, and the one market factor that matters most.

## 🟢 فرص شراء
One card per TRADE IDEA (omit the section if none):
### <symbol> — <what the company/coin is, in a few words>
- **القرار:** شراء الآن أو شراء بشرط (the condition in plain words)
- **لماذا:** two or three short reasons in everyday language
- **الخطة:** الدخول · الوقف · الهدف · أقصى خسارة بالدولار
- **الحالة الشرعية:** ...
- **مستوى الثقة:** منخفض / متوسط / مرتفع

## 🟡 للمراقبة
One line each: symbol, what it is, and the exact trigger in plain words ("نشتري إذا ...").

## ⚪ تجاهلناها
One line each with the reason.

## 🔬 عملات تحتاج بحثاً شرعياً قبل اعتمادها
For research candidates: what the coin does, the specific Sharia question to settle, and whether
it is even technically attractive. Remind that it cannot be traded until the trader approves it.

Finish with a short phone-friendly Arabic summary for Telegram (max ~700 characters, plain text,
emoji bullets, no markdown tables or headings) between the markers {start} and {end}.

Candidates:
{candidates}
"""


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"runs": [], "seen": {}, "ai_runs": {}, "last_run_ts": 0}


def _save(state: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    state["runs"] = state["runs"][-50:]
    STORE.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def history(limit: int = 20) -> list[dict]:
    return list(reversed(_load()["runs"][-limit:]))


def due() -> bool:
    return time.time() - _load().get("last_run_ts", 0) >= settings.explore_every_hours * 3600


def _slim(o: dict, market: str) -> dict:
    keys = ("symbol", "score", "setup", "rsi14", "return_1m_pct", "return_3m_pct",
            "return_3m_usd_pct", "rel_strength_3m_pct", "from_55d_high_pct", "atr_pct",
            "listed_days_ago", "volume_24h_usd_m")
    return {"market": market, **{k: o[k] for k in keys if k in o},
            "sharia": (o.get("sharia") or {}).get("status")}


def collect_candidates(scans: dict[str, Callable[[], dict]] | None = None) -> tuple[list, list]:
    """Return (tradable, research) candidates from the free scanners."""
    scans = scans or {
        "crypto": lambda: scanner.scan_crypto("established", top_n=15),
        "new_listings": lambda: scanner.scan_crypto("new_listings", top_n=8),
        "bist": lambda: scanner.scan_bist(top_n=10),
    }
    tradable, research = [], []
    for market, fn in scans.items():
        try:
            result = fn()
        except Exception as e:
            print(f"[explorer] {market} scan failed: {e}")
            continue
        for o in result.get("opportunities", []):
            if o["score"] < settings.explore_min_score or o["setup"] == "none":
                continue
            status = (o.get("sharia") or {}).get("status")
            if status == "compliant":
                tradable.append(_slim(o, market))
            elif status == "review_required" and market != "bist":
                research.append(_slim(o, market))
    key = lambda x: x["score"]  # noqa: E731
    return sorted(tradable, key=key, reverse=True), sorted(research, key=key, reverse=True)


def _scanner_summary(tradable: list, research: list) -> str:
    lines = ["🔎 فرص جديدة من الماسح (بدون تحليل الذكاء الاصطناعي):"]
    for c in tradable[:6]:
        lines.append(f"• {c['symbol']} — {c['score']}/100 — {scanner.SETUP_AR.get(c['setup'], c['setup'])}")
    if research:
        lines.append("\n🧪 مرشحون للبحث (غير معتمدين شرعياً بعد):")
        lines += [f"• {c['symbol']} — {c['score']}/100" for c in research[:4]]
    lines.append("\nافتح الواجهة واطلب من الوكيل دراسة أي منها.")
    return "\n".join(lines)


def explore(broker=None, use_ai: bool = True, send: bool = True, force: bool = False,
            scans: dict | None = None, agent_factory: Callable | None = None) -> dict:
    """force=True (a manual "explore now") re-reports candidates already seen in the last 24h;
    the scheduled cycle keeps the de-duplication so Telegram isn't spammed."""
    state = _load()
    now = time.time()
    state["last_run_ts"] = now
    tradable, research = collect_candidates(scans)

    seen = {k: ts for k, ts in state["seen"].items() if now - ts < COOLDOWN_S}
    fresh = lambda c: force or f"{c['symbol']}|{c['setup']}" not in seen  # noqa: E731
    tradable, research = [c for c in tradable if fresh(c)], [c for c in research if fresh(c)]

    tradable, research = tradable[:6], research[:4]
    run = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tradable": tradable, "research": research, "ai": False, "report": "", "telegram": ""}
    if not tradable and not research:
        run["report"] = "لا توجد فرص جديدة قوية في هذه الجولة."
        state["seen"] = seen
        state["runs"].append(run)
        _save(state)
        return run

    today = datetime.now(timezone.utc).date().isoformat()
    ai_used_today = state["ai_runs"].get(today, 0)
    if use_ai and ai_used_today < settings.explore_ai_max_per_day:
        try:
            if agent_factory:
                agent = agent_factory()
            else:
                from .core import TradingAgent
                agent = TradingAgent(broker, approval_mode="deferred", effort="medium",
                                     on_text=lambda t: None)
                agent.executor.alert_source = "explorer"
            payload = json.dumps({"tradable": tradable, "research_candidates": research},
                                 ensure_ascii=False)
            report = agent.ask(EXPLORE_PROMPT.format(start=TELEGRAM_START, end=TELEGRAM_END,
                                                     candidates=payload))
            agent.executor.pending.clear()  # the explorer never queues orders
            run["ai"] = True
            state["ai_runs"] = {today: ai_used_today + 1}
            if TELEGRAM_START in report:
                body, _, tail = report.partition(TELEGRAM_START)
                run["telegram"] = tail.split(TELEGRAM_END)[0].strip()
                run["report"] = body.strip()
            else:
                run["report"] = report.strip()
                run["telegram"] = report.strip()[:900]
        except Exception as e:
            run["report"] = f"تعذّر تحليل الذكاء الاصطناعي ({type(e).__name__}: {e})."
    if not run["telegram"]:
        run["telegram"] = _scanner_summary(tradable, research)
        run["report"] = (run["report"] + "\n\n" + run["telegram"]).strip()

    for c in tradable + research:
        seen[f"{c['symbol']}|{c['setup']}"] = now
    state["seen"] = seen
    state["runs"].append(run)
    _save(state)
    # A scanner-only round with nothing tradable is kept in the browser but not pushed to the phone.
    if send and (run["ai"] or tradable):
        run["sent"] = notify.send(f"💡 مستكشف الفرص\n\n{run['telegram']}")
    return run
