"""Personal Trading Desk - browser interface with proper Arabic (right-to-left) rendering.

    streamlit run app.py
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from agent import market_data, scanner
from agent.config import settings
from agent.sharia import universe
from agent.scanner import SETUP_AR

st.set_page_config(page_title="مكتب التداول الشخصي", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

BG, ACCENT, RED = "#0f172a", "#10b981", "#ef4444"
st.markdown(f"""
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;900&display=swap" rel="stylesheet">
<style>
html, body, [class*="st-"], .stMarkdown, .stChatMessage, button, input, textarea {{
  font-family: 'Cairo', 'Poppins', sans-serif !important;
}}
.main .block-container, section[data-testid="stSidebar"] {{ direction: rtl; text-align: right; }}
.stChatMessage, [data-testid="stChatMessageContent"], .stMarkdown {{ direction: rtl; text-align: right; }}
pre, code, [data-testid="stDataFrame"], [data-testid="stJson"], .ltr {{ direction: ltr; text-align: left; }}
[data-testid="stChatInput"] textarea {{ direction: rtl; text-align: right; }}
[data-testid="stIconMaterial"], [class*="material-symbols"] {{
  font-family: 'Material Symbols Rounded' !important;
}}
h1, h2, h3 {{ color: {ACCENT}; font-weight: 700; }}
.hx-badge {{ display:inline-block; padding:2px 12px; border-radius:999px; font-weight:700; font-size:.85rem; }}
.hx-paper {{ background:{ACCENT}; color:{BG}; }}
.hx-live {{ background:{RED}; color:#fff; }}
.hx-card {{ border:2px solid {ACCENT}; border-radius:12px; padding:14px 18px; margin:8px 0;
           background:rgba(255,255,255,.04); }}
.hx-tool {{ opacity:.7; font-size:.85rem; }}
[data-testid="stMetricValue"] {{ color:{ACCENT}; }}
.stMarkdown ul, .stMarkdown ol {{ padding-right: 1.4rem; padding-left: 0; margin-right: 0; }}
.stMarkdown li {{ text-align: right; margin-left: 0; }}
[data-baseweb="tab-list"] {{ direction: rtl; }}
[data-testid="stTable"] table {{ direction: rtl; }}
</style>
""", unsafe_allow_html=True)

TOOL_LABELS = {
    "analyze_market": "تحليل فني", "check_sharia": "فحص شرعي", "scan_turkish_stocks": "مسح البورصة التركية",
    "scan_crypto": "مسح العملات الرقمية", "research_crypto": "بحث أساسي عن العملة",
    "get_account": "قراءة الحساب", "preview_trade": "معاينة الصفقة", "open_trade": "طلب فتح صفقة",
    "modify_trade": "طلب تعديل صفقة", "close_trade": "طلب إغلاق صفقة", "trade_history": "سجل الصفقات",
    "backtest_strategy": "اختبار تاريخي", "create_alert": "إنشاء تنبيه", "list_alerts": "قراءة التنبيهات",
    "cancel_alert": "إلغاء تنبيه",
}
ACTION_LABELS = {"OPEN TRADE": "فتح صفقة", "MODIFY TRADE": "تعديل صفقة", "CLOSE TRADE": "إغلاق صفقة"}
SHARIA_AR = {"compliant": "✅ متوافق", "not_compliant": "❌ غير متوافق", "review_required": "⏸ يحتاج مراجعة"}
MARKET_AR = {"crypto": "عملات رقمية", "new_listings": "عملات حديثة الإدراج", "bist": "البورصة التركية"}


# ── broker / agent (kept for the whole browser session) ────────
def mode_label() -> str:
    return "paper" if settings.trading_mode == "paper" else f"binance-{settings.binance_env}"


@st.cache_resource(show_spinner="جارٍ الاتصال بالحساب...")
def get_broker():
    if settings.trading_mode == "paper":
        from agent.paper_broker import PaperBroker
        return PaperBroker()
    from agent.binance_broker import BinanceBroker
    broker = BinanceBroker()
    broker.verify_account()
    return broker


def get_agent():
    if "agent" not in st.session_state:
        from agent.core import TradingAgent
        st.session_state.agent = TradingAgent(get_broker(), approval_mode="deferred")
    return st.session_state.agent


ss = st.session_state
ss.setdefault("chat", [])  # [{"role", "content", "tools": [...]}]

try:
    broker = get_broker()
except Exception as e:  # bad key, refused permissions, network
    st.error(f"تعذّر تشغيل الحساب ({mode_label()}):\n\n{e}")
    st.stop()


# ── sidebar ────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 مكتب التداول")
    live = settings.trading_mode == "live"
    st.markdown(f'<span class="hx-badge {"hx-live" if live else "hx-paper"}">{mode_label()}</span>',
                unsafe_allow_html=True)
    if live and settings.binance_env == "live" and not ss.get("live_ok"):
        st.warning("⚠️ أموال حقيقية. اكتب LIVE للمتابعة")
        if st.text_input("تأكيد", key="live_word") == "LIVE":
            ss.live_ok = True
            st.rerun()
        st.stop()

    try:
        acc = broker.account()
        st.metric("القيمة الإجمالية", f"${acc['equity']:,.2f}")
        st.metric("نقد متاح", f"${acc['free_cash']:,.2f}")
        st.metric("ربح/خسارة اليوم", f"${acc['today_realized_pnl']:,.2f}")
        st.caption(f"صفقات مفتوحة: {len(acc['open_positions'])}")
    except Exception as e:
        st.warning(f"تعذّر قراءة الحساب: {e}")

    if st.button("🔄 فحص الوقف والأهداف", width="stretch"):
        closed = broker.check_stops()
        st.success(f"أُغلقت {len(closed)} صفقة" if closed else "لا توجد صفقات وصلت للوقف أو الهدف")
    if st.button("🆕 محادثة جديدة", width="stretch"):
        ss.chat = []
        ss.pop("agent", None)
        st.rerun()
    st.caption("لا رافعة · لا بيع على المكشوف · حلال فقط · التحليل قبل التنفيذ")

from agent import alerts  # noqa: E402

n_proposed = sum(a["status"] == "proposed" for a in alerts.active())
tab_chat, tab_ideas, tab_alerts, tab_scan, tab_bt, tab_acc, tab_halal = st.tabs(
    ["💬 المحادثة", "💡 فرص الوكيل", f"⏰ التنبيهات{f' ({n_proposed}🔴)' if n_proposed else ''}",
     "🔎 المسح", "🧪 اختبار تاريخي", "📊 الحساب", "☪️ القائمة الشرعية"])


# ── chat ───────────────────────────────────────────────────────
def render_tools(tools: list[dict]) -> None:
    for t in tools:
        st.markdown(f'<div class="hx-tool">⚙️ {TOOL_LABELS.get(t["name"], t["name"])} · '
                    f'<span class="ltr">{t["args"].get("symbol", "")}</span></div>', unsafe_allow_html=True)


def render_approval(item: dict, agent) -> None:
    d = item["details"]
    s = d.get("sizing") or {}
    rows = {
        "الرمز": d.get("symbol") or d.get("position_id"), "سعر الدخول": d.get("entry"),
        "وقف الخسارة": d.get("stop_loss"), "جني الأرباح": d.get("take_profit"),
        "العائد/المخاطرة": d.get("reward_risk"), "الكمية": s.get("units"),
        "التكلفة ($)": s.get("cost"), "أقصى خسارة ($)": s.get("risk_amount"),
        "الحالة الشرعية": (d.get("sharia") or {}).get("status"), "السبب": d.get("reason"),
    }
    with st.container(border=True):
        st.markdown(f"### ✋ بانتظار موافقتك: {ACTION_LABELS.get(item['action'], item['action'])} "
                    f"<span class='hx-badge {'hx-live' if live else 'hx-paper'}'>{mode_label()}</span>",
                    unsafe_allow_html=True)
        st.table(pd.DataFrame({"القيمة": {k: str(v) for k, v in rows.items() if v is not None}}))
        if d.get("rationale"):
            st.markdown(f"**المبرر:** {d['rationale']}")
        c1, c2 = st.columns(2)
        if c1.button("✅ موافق، نفّذ", key=f"ok_{item['id']}", type="primary", width="stretch"):
            try:
                res = agent.executor.approve(item["id"])
                msg = f"✅ تم التنفيذ: {ACTION_LABELS.get(res['action'], res['action'])}"
                agent.notes.append(f"Trader APPROVED {item['action']} {item['id']}. Result: "
                                   + json.dumps(res["result"], ensure_ascii=False, default=str)[:1500])
            except Exception as e:
                msg = f"❌ رُفض التنفيذ: {e}"
                agent.notes.append(f"Trader approved {item['action']} {item['id']} but it failed: {e}")
            ss.chat.append({"role": "assistant", "content": msg, "tools": []})
            st.rerun()
        if c2.button("✖️ رفض", key=f"no_{item['id']}", width="stretch"):
            agent.executor.reject(item["id"])
            agent.notes.append(f"Trader REJECTED {item['action']} {item['id']}.")
            ss.chat.append({"role": "assistant", "content": "تم رفض الطلب، ولن يُنفَّذ.", "tools": []})
            st.rerun()


with tab_chat:
    if not ss.chat:
        st.markdown("## أهلاً بك في مكتب التداول")
        st.markdown("اسأل عن أي سهم أو عملة، أو اطلب البحث عن فرص. أمثلة:\n"
                    "- ابحث لي عن أفضل 3 فرص في البورصة التركية هذا الأسبوع\n"
                    "- حلّل سهم BIMAS.IS وهل هو فرصة شراء الآن؟\n"
                    "- ما أفضل العملات المدرجة حديثاً على بينانس؟ وادرس أقواها\n"
                    "- هل عملة SOL متوافقة مع الشريعة؟")
    for m in ss.chat:
        with st.chat_message(m["role"], avatar="🧑‍💼" if m["role"] == "user" else "🤖"):
            render_tools(m.get("tools", []))
            st.markdown(m["content"])

    agent = ss.get("agent")
    if agent:
        for item in list(agent.executor.pending):
            render_approval(item, agent)

    prompt = st.chat_input("اكتب سؤالك هنا...") or ss.pop("queued_prompt", None)
    if prompt:
        ss.chat.append({"role": "user", "content": prompt, "tools": []})
        with st.chat_message("user", avatar="🧑‍💼"):
            st.markdown(prompt)
        with st.chat_message("assistant", avatar="🤖"):
            tools_box, text_box = st.container(), st.empty()
            buf, tools = [], []

            def on_text(t: str) -> None:
                buf.append(t)
                text_box.markdown("".join(buf) + " ▌")

            def on_tool(name: str, args: dict) -> None:
                tools.append({"name": name, "args": args})
                with tools_box:
                    render_tools([tools[-1]])

            try:
                agent = get_agent()
                agent.on_text, agent.on_tool = on_text, on_tool
                with st.spinner("الوكيل يعمل..."):
                    tail = agent.ask(prompt)
                answer = "".join(buf) or tail
                if tail.startswith("\n["):
                    answer += f"\n\n> {tail.strip()}"
            except Exception as e:
                answer = f"❌ حدث خطأ: {type(e).__name__}: {e}"
            text_box.markdown(answer)
        ss.chat.append({"role": "assistant", "content": answer, "tools": tools})
        st.rerun()


# ── scans ──────────────────────────────────────────────────────
with tab_scan:
    st.markdown("## مسح الفرص")
    st.caption("مجاني: لا يستخدم الذكاء الاصطناعي. اطلب من الوكيل في المحادثة دراسة أي نتيجة بعمق.")
    kinds = {"البورصة التركية": "bist", "العملات الراسخة": "established",
             "العملات المدرجة حديثاً": "new_listings", "العملات الرائجة": "trending"}
    choice = st.radio("السوق", list(kinds), horizontal=True)
    if st.button("ابدأ المسح", type="primary"):
        with st.spinner("جارٍ المسح... قد يستغرق دقيقة أو أكثر"):
            try:
                kind = kinds[choice]
                ss.scan = scanner.scan_bist(top_n=20) if kind == "bist" else \
                    scanner.scan_crypto(mode=kind, top_n=20)
            except Exception as e:
                st.error(f"فشل المسح: {e}")
    r = ss.get("scan")
    if r:
        df = pd.DataFrame([{
            "الرمز": o["symbol"], "الدرجة": o["score"], "نوع الفرصة": SETUP_AR.get(o["setup"], o["setup"]),
            "RSI": o["rsi14"],
            "أداء 3 أشهر %": o.get("return_3m_pct"), "بالدولار %": o.get("return_3m_usd_pct"),
            "القوة النسبية %": o.get("rel_strength_3m_pct"),
            "الحالة الشرعية": SHARIA_AR.get(o["sharia"]["status"], o["sharia"]["status"]),
        } for o in r["opportunities"]]).dropna(axis=1, how="all")
        st.dataframe(df, hide_index=True, width="stretch")
        excluded = r.get("excluded_by_sharia_screen") or []
        if excluded:
            with st.expander(f"مستبعد شرعياً ({len(excluded)})"):
                for e in excluded:
                    st.markdown(f"- **{e['symbol']}** · {e['status']}: <span class='ltr'>{'; '.join(e['reasons'])}</span>",
                                unsafe_allow_html=True)


# ── proactive ideas ────────────────────────────────────────────
with tab_ideas:
    from agent import explorer, notify
    st.markdown("## فرص اكتشفها الوكيل بنفسه")
    st.caption(f"يمسح الأسواق تلقائياً كل {settings.explore_every_hours:g} ساعات أثناء تشغيل نافذة المراقب، "
               f"ويدرس أفضل المرشحين بالذكاء الاصطناعي (الحد اليومي: {settings.explore_ai_max_per_day}). "
               f"تيليجرام: {'مفعّل ✅' if notify.configured() else 'غير مفعّل'}. "
               "لا يفتح أي صفقة: التنفيذ يبقى بموافقتك.")
    c1, c2 = st.columns(2)
    if c1.button("🔍 استكشف الآن (مع الذكاء الاصطناعي)", type="primary", width="stretch"):
        with st.spinner("الوكيل يمسح الأسواق ويدرس المرشحين... قد يستغرق دقيقتين إلى أربع"):
            explorer.explore(broker, force=True)
        st.rerun()
    if c2.button("⚡ مسح سريع مجاني", width="stretch"):
        with st.spinner("جارٍ المسح..."):
            explorer.explore(broker, use_ai=False, force=True)
        st.rerun()
    runs = explorer.history(15)
    if not runs:
        st.info("لا توجد جولات استكشاف بعد. اضغط «استكشف الآن» أو اترك نافذة المراقب تعمل.")
    for i, run in enumerate(runs):
        title = (f"{run['ts'][:16].replace('T', ' ')} UTC · {len(run['tradable'])} قابلة للتداول · "
                 f"{len(run['research'])} للبحث · {'🤖 تحليل ذكي' if run['ai'] else '⚡ مسح فقط'}")
        with st.expander(title, expanded=(i == 0)):
            st.markdown(run.get("report") or run.get("telegram") or "-")
            cands = run["tradable"][:6] + run["research"][:4]
            if cands:
                st.markdown("#### المرشحون في هذه الجولة")
                for j, c in enumerate(cands):
                    col1, col2 = st.columns([4, 1])
                    col1.markdown(
                        f"**{c['symbol']}** · {MARKET_AR.get(c.get('market'), c.get('market'))} · "
                        f"الدرجة {c['score']}/100 · {SETUP_AR.get(c['setup'], c['setup'])} · "
                        f"{SHARIA_AR.get(c.get('sharia'), c.get('sharia'))}")
                    if col2.button("ادرسها معي 💬", key=f"study_{i}_{j}", width="stretch"):
                        ss.queued_prompt = (f"اقترح المستكشف {c['symbol']} ({SETUP_AR.get(c['setup'], c['setup'])}). "
                                            "ادرسها بالتفصيل الآن، واشرح لي بلغة بسيطة هل تستحق الشراء، "
                                            "وإن كانت مناسبة حسب قواعدي جهّز الصفقة لأوافق عليها.")
                        st.toast("أُرسلت للوكيل. افتح تبويب «المحادثة» لترى التحليل.", icon="💬")
                        st.rerun()


# ── alerts & auto-buy plans ────────────────────────────────────
STATUS_AR = {"proposed": "🟠 بانتظار موافقتك", "armed": "🟢 قيد المراقبة", "triggered": "🔔 تحقق",
             "executed": "🤖✅ نُفّذ", "failed": "⚠️ لم يُنفّذ", "expired": "⌛ انتهت صلاحيته",
             "cancelled": "✖️ أُلغي", "rejected": "✖️ مرفوض"}
COND_OPTIONS = {"إغلاق يومي فوق": "close_above", "السعر فوق": "price_above",
                "السعر تحت": "price_below", "إغلاق يومي تحت": "close_below"}

with tab_alerts:
    st.markdown("## التنبيهات والشراء التلقائي")
    st.caption("🔔 تنبيه فقط: رسالة على تيليجرام عند تحقق الشرط. "
               "🤖 شراء تلقائي: توافق مرة واحدة على الخطة، فيشتري عند تحقق الشرط بدون الرجوع إليك، "
               "مع إعادة فحص القواعد الشرعية وقواعد المخاطرة لحظة التنفيذ. "
               "يعمل فقط أثناء تشغيل نافذة المراقب.")
    act = alerts.active()

    proposed = [a for a in act if a["status"] == "proposed"]
    if proposed:
        st.markdown("### 🟠 خطط شراء تلقائي بانتظار موافقتك")
    for a in proposed:
        with st.container(border=True):
            st.markdown(f"**{alerts.describe(a)}** · مصدرها: "
                        f"{'المستكشف' if a['source'] == 'explorer' else 'المحادثة' if a['source'] == 'chat' else 'يدوي'}")
            rr = (a["take_profit"] - a["level"]) / (a["level"] - a["stop_loss"])
            st.markdown(f"عند تحقق الشرط **يشتري تلقائياً** · الوقف **{a['stop_loss']:g}** · "
                        f"الهدف **{a['take_profit']:g}** · العائد/المخاطرة من مستوى الشرط **{rr:.1f}**")
            if a.get("note"):
                st.caption(a["note"])
            c1, c2 = st.columns(2)
            if c1.button("✅ أوافق: نفّذ تلقائياً عند تحقق الشرط", key=f"al_ok_{a['id']}", type="primary",
                         width="stretch"):
                alerts.approve(a["id"])
                st.rerun()
            if c2.button("✖️ رفض", key=f"al_no_{a['id']}", width="stretch"):
                alerts.reject(a["id"])
                st.rerun()

    armed = [a for a in act if a["status"] == "armed"]
    st.markdown("### 🟢 قيد المراقبة")
    if not armed:
        st.caption("لا توجد تنبيهات نشطة.")
    for a in armed:
        c1, c2 = st.columns([5, 1])
        kind = "🤖 شراء تلقائي" if a["action"] == "auto_buy" else "🔔 تنبيه"
        last = f" · آخر قيمة {a['last_value']:g}" if a.get("last_value") is not None else ""
        c1.markdown(f"{kind} · **{alerts.describe(a)}**{last}" + (f" · {a['note']}" if a.get("note") else ""))
        if c2.button("إلغاء", key=f"al_cancel_{a['id']}", width="stretch"):
            alerts.cancel(a["id"])
            st.rerun()

    with st.expander("➕ إضافة تنبيه يدوياً"):
        f1, f2, f3 = st.columns(3)
        sym = f1.text_input("الرمز", "BTC/USDT", key="al_sym")
        cond = f2.selectbox("الشرط", list(COND_OPTIONS), key="al_cond")
        level = f3.number_input("المستوى", min_value=0.0, value=0.0, format="%.6g", key="al_level")
        kind = st.radio("النوع", ["🔔 تنبيه فقط", "🤖 شراء تلقائي"], horizontal=True, key="al_kind")
        stop = target = None
        if kind.startswith("🤖"):
            g1, g2 = st.columns(2)
            stop = g1.number_input("وقف الخسارة", min_value=0.0, value=0.0, format="%.6g", key="al_stop")
            target = g2.number_input("الهدف", min_value=0.0, value=0.0, format="%.6g", key="al_tp")
        note = st.text_input("ملاحظة (اختياري)", key="al_note")
        days = st.slider("الصلاحية بالأيام", 1, 30, settings.alert_expiry_days, key="al_days")
        if st.button("حفظ التنبيه", type="primary"):
            try:
                created = alerts.create(sym, COND_OPTIONS[cond], level,
                                        "auto_buy" if kind.startswith("🤖") else "notify",
                                        stop or None, target or None, note, source="manual", expires_days=days)
                st.success("تم الحفظ" + (" — وافق عليه بالأعلى ليصبح نشطاً" if created["status"] == "proposed" else ""))
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    hist = alerts.history(20)
    if hist:
        with st.expander(f"السجل ({len(hist)})"):
            for a in hist:
                st.markdown(f"- {STATUS_AR.get(a['status'], a['status'])} · **{alerts.describe(a)}** · "
                            f"{a.get('result') or ''}")


# ── backtest ───────────────────────────────────────────────────
with tab_bt:
    from agent import backtest
    st.markdown("## اختبار تاريخي لقواعد الوكيل")
    st.caption("يطبّق نفس قواعد الماسح على بيانات السنوات الماضية: دخول عند افتتاح اليوم التالي، وقف بمسافة "
               "ضعفي ATR، هدف ثابت، مخاطرة 1%، بلا رافعة، مع العمولات. لا يشمل الأخبار ولا حكم الذكاء "
               "الاصطناعي. النتائج الماضية ليست ضماناً للمستقبل.")
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    syms = c1.text_input("الرموز (افصل بفاصلة)", "BTC/USDT, ETH/USDT, BIMAS.IS")
    years = c2.number_input("السنوات", 1, 8, 4)
    min_score = c3.number_input("أقل درجة", 40, 100, 70, step=5)
    rr = c4.number_input("العائد/المخاطرة", 1.0, 5.0, 2.0, step=0.5)
    if st.button("▶️ شغّل الاختبار", type="primary"):
        symbols = [x.strip().upper() for x in syms.split(",") if x.strip()][:6]
        results = {}
        with st.spinner("جارٍ تنزيل البيانات والاختبار..."):
            for sym in symbols:
                try:
                    df = market_data.fetch_daily_history(sym, int(years))
                    if sym.endswith(".IS"):  # judge Turkish stocks in USD, not inflating lira
                        df = backtest.in_usd(df, market_data.fetch_daily_history("USDTRY", int(years))["close"])
                    results[sym] = backtest.run(df, min_score=int(min_score), reward_risk=float(rr))
                except Exception as e:
                    results[sym] = {"error": f"{type(e).__name__}: {e}"}
        ss.bt = results
    for sym, res in (ss.get("bt") or {}).items():
        st.markdown(f"### {sym}")
        if "error" in res:
            st.error(res["error"])
            continue
        stt = res["stats"]
        if sym.endswith(".IS"):
            st.caption("💵 محسوب بالدولار، لأن الليرة التركية تضخّم العوائد الاسمية")
        m = st.columns(4)
        m[0].metric("عدد الصفقات", stt["trades"])
        m[1].metric("نسبة الربح", f"{stt['win_rate_pct']}%" if stt["win_rate_pct"] is not None else "-")
        m[2].metric("متوسط R", stt["avg_r"] if stt["avg_r"] is not None else "-")
        m[3].metric("وقت داخل السوق", f"{stt['time_in_market_pct']}%")
        st.table(pd.DataFrame({
            "استراتيجية الوكيل": [f"{stt['total_return_pct']}%", f"{stt['max_drawdown_pct']}%"],
            "الشراء والاحتفاظ": [f"{stt['buy_and_hold_pct']}%", f"{stt.get('buy_and_hold_max_drawdown_pct')}%"],
        }, index=["العائد الإجمالي", "أقصى تراجع"]))
        if stt["trades"] < 30:
            st.warning("عدد الصفقات أقل من 30: النتيجة غير موثوقة إحصائياً.")
        st.caption("منحنى رأس المال (يبدأ من 10,000$)")
        eq = res["equity_curve"].rename("equity").rename_axis("date").reset_index()
        st.altair_chart(
            alt.Chart(eq).mark_line(color=ACCENT, strokeWidth=2).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("equity:Q", title=None, scale=alt.Scale(zero=False), axis=alt.Axis(format="$,.0f")),
                tooltip=[alt.Tooltip("date:T", title="التاريخ"),
                         alt.Tooltip("equity:Q", title="رأس المال", format="$,.0f")],
            ).properties(height=240),
            width="stretch")
        if res["trades"]:
            with st.expander("آخر الصفقات"):
                st.dataframe(pd.DataFrame(backtest._fmt(res["trades"][-20:])), hide_index=True,
                             width="stretch")


    st.divider()
    st.markdown("## ⚡ المضاربة قصيرة المدى: هل تربح فعلاً؟")
    st.caption("ثلاث استراتيجيات شراء سريعة (دخول وخروج خلال ساعات) تعمل فقط مع الاتجاه الصاعد. تُختار "
               "إعداداتها على أول 70% من التاريخ، ثم تُحكم على آخر 30% لم ترها أبداً، بعد خصم الرسوم "
               "(0.1% لكل جهة) والانزلاق السعري. الحكم يعتمد على فترة الاختبار فقط.")
    from agent import shortterm
    d1, d2, d3 = st.columns([3, 1, 1])
    st_syms = d1.text_input("العملات", "BTC/USDT, ETH/USDT", key="st_syms")
    tf = d2.selectbox("الإطار الزمني", ["1h", "15m"], key="st_tf",
                      format_func=lambda x: {"1h": "ساعة", "15m": "15 دقيقة"}[x])
    days = d3.selectbox("المدة", [730, 365, 180], key="st_days", format_func=lambda x: f"{x} يوماً")
    if st.button("⚡ اختبر المضاربة السريعة", type="primary"):
        symbols = [x.strip().upper() for x in st_syms.split(",") if x.strip()][:4]
        with st.spinner("جارٍ تنزيل آلاف الشموع واختبار 27 تركيبة لكل عملة... قد يستغرق دقيقة"):
            ss.st_res = shortterm.research(symbols, timeframe=tf, days=int(days))
    res = ss.get("st_res")
    if res:
        icon = {"promising": "🟢", "weak": "🟡", "fails": "🔴", "insufficient": "⚪"}
        if res["promising"]:
            st.success("استراتيجيات واعدة: " + " · ".join(res["promising"]))
        else:
            st.info("لا توجد استراتيجية واعدة بعد الرسوم في هذه الفترة. الاحتفاظ بالنقد أو الاستثمار "
                    "طويل المدى أفضل من مضاربة خاسرة.")
        for sym, v in res["per_symbol"].items():
            st.markdown(f"### {sym}")
            if "error" in v:
                st.error(v["error"])
                continue
            rows = []
            for r in v["results"]:
                t = r["test"]
                rows.append({
                    "الاستراتيجية": r["strategy_ar"], "الحكم": f"{icon[r['verdict']]} {r['verdict_ar'].split(':')[0].split('—')[0].strip()}",
                    "صفقات الاختبار": t["trades"], "صفقات/أسبوع": t["trades_per_week"],
                    "نسبة الربح %": t["win_rate_pct"], "معامل الربح": t["profit_factor"],
                    "العائد %": t["total_return_pct"], "أقصى تراجع %": t["max_drawdown_pct"],
                    "الشراء والاحتفاظ %": t["buy_and_hold_pct"],
                    "الرسوم من الأرباح %": t["fees_vs_gross_profit_pct"],
                    "الإعدادات": f"هدف {r['params']['reward_risk']}R · خروج بعد {r['params']['time_stop_bars']} شمعة",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            r0 = v["results"][0]
            st.caption(f"فترة الاختبار: {r0['test_from']} ← {r0['test_to']} · عدد الشموع: {v['bars']:,}. "
                       "معامل الربح = الأرباح ÷ الخسائر (أعلى من 1.3 جيد). ")

    st.divider()
    st.markdown("## 🌊 ركوب الموجة: الاتجاهات الطويلة مع وقف متحرك")
    st.caption("صفقات قليلة تدوم أسابيع: دخول عند بداية الاتجاه الصاعد، ووقف يرتفع مع السعر ولا ينزل أبداً، "
               "بلا هدف ثابت حتى يكبر الربح. السؤال هنا: هل تحتفظ بأغلب الصعود وتتجنب الانهيارات الكبيرة؟ "
               "الحكم على آخر 30% من التاريخ، ومعه النتيجة على كامل التاريخ (تشمل انهيارَي 2018 و2022).")
    from agent import swing
    w1, w2, w3 = st.columns([3, 1, 1])
    sw_syms = w1.text_input("العملات", "BTC/USDT, ETH/USDT", key="sw_syms")
    sw_tf = w2.selectbox("الإطار الزمني", ["1d", "4h"], key="sw_tf",
                         format_func=lambda x: {"1d": "يومي", "4h": "4 ساعات"}[x])
    sw_years = w3.selectbox("السنوات", [9, 5, 3], key="sw_years")
    if st.button("🌊 اختبر ركوب الموجة", type="primary"):
        symbols = [x.strip().upper() for x in sw_syms.split(",") if x.strip()][:4]
        with st.spinner("جارٍ تنزيل سنوات من البيانات واختبار 12 تركيبة لكل عملة..."):
            ss.sw_res = swing.research(symbols, timeframe=sw_tf, years=int(sw_years))
    res = ss.get("sw_res")
    if res:
        icon = {"promising": "🟢", "weak": "🟡", "fails": "🔴", "insufficient": "⚪"}
        if res["promising"]:
            st.success("واعدة: " + " · ".join(res["promising"]))
        for sym, v in res["per_symbol"].items():
            st.markdown(f"### {sym}")
            if "error" in v:
                st.error(v["error"])
                continue
            rows = []
            for r in v["results"]:
                t, w = r["test"], r["whole"]
                rows.append({
                    "الاستراتيجية": r["strategy_ar"],
                    "الحكم": f"{icon[r['verdict']]} {r['verdict_ar'].split(':')[0].split('—')[0].strip()}",
                    "صفقات الاختبار": t["trades"], "عائد الاختبار %": t["total_return_pct"],
                    "تراجع الاختبار %": t["max_drawdown_pct"], "احتفاظ: عائد %": t["buy_and_hold_pct"],
                    "احتفاظ: تراجع %": t["buy_and_hold_max_drawdown_pct"],
                    "كامل التاريخ: عائد %": w["total_return_pct"], "كامل التاريخ: تراجع %": w["max_drawdown_pct"],
                    "احتفاظ كامل: عائد %": w["buy_and_hold_pct"],
                    "احتفاظ كامل: تراجع %": w["buy_and_hold_max_drawdown_pct"],
                    "داخل السوق %": w["time_in_market_pct"],
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            for r in v["results"]:
                rb = r["robustness"]
                st.caption(f"🧱 ثبات «{r['strategy_ar']}»: من {rb['settings']} إعدادات مختلفة، ربحت "
                           f"{rb['profitable']}، وكان هبوطها أقل من الاحتفاظ في {rb['smaller_drawdown']}، "
                           f"وتفوقت على عائد الاحتفاظ في {rb['beat_hold_return']} · الوسيط: عائد "
                           f"{rb['median_return_pct']}% وتراجع {rb['median_drawdown_pct']}%")
            r0 = v["results"][0]
            st.caption(f"الاختبار: {r0['test_from']} ← {r0['test_to']} · كامل التاريخ من {r0['whole_from']}. "
                       "التراجع = أكبر هبوط من قمة رأس المال، وكلما صغر كان أفضل.")
            best = max(v["results"], key=lambda r: r["whole"]["total_return_pct"])
            eq = best["equity_whole"].rename("equity").rename_axis("date").reset_index()
            st.caption(f"منحنى رأس المال على كامل التاريخ: {best['strategy_ar']} (يبدأ من 10,000$)")
            st.altair_chart(alt.Chart(eq).mark_line(color=ACCENT, strokeWidth=2).encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("equity:Q", title=None, scale=alt.Scale(type="log"), axis=alt.Axis(format="$,.0f")),
            ).properties(height=220), width="stretch")

# ── account ────────────────────────────────────────────────────
with tab_acc:
    st.markdown("## الحساب")
    try:
        acc = broker.account()
        c = st.columns(4)
        c[0].metric("القيمة الإجمالية", f"${acc['equity']:,.2f}")
        c[1].metric("نقد متاح", f"${acc['free_cash']:,.2f}")
        c[2].metric("ربح غير محقق", f"${acc['unrealized_pnl']:,.2f}")
        c[3].metric("ربح اليوم المحقق", f"${acc['today_realized_pnl']:,.2f}")
        st.markdown("### الصفقات المفتوحة")
        if acc["open_positions"]:
            st.dataframe(pd.DataFrame([{
                "id": p["id"], "الرمز": p["symbol"], "الدخول": p["entry"], "السعر الحالي": p.get("current_price"),
                "الوقف": p["stop_loss"], "الهدف": p["take_profit"], "ربح/خسارة $": p.get("unrealized_pnl"),
                "R": p.get("r_multiple"), "الحماية": p.get("protection", "-"),
            } for p in acc["open_positions"]]), hide_index=True, width="stretch")
        else:
            st.caption("لا توجد صفقات مفتوحة.")
        h = broker.history(50)
        st.markdown("### السجل")
        c = st.columns(4)
        c[0].metric("عدد الصفقات", h["total_trades"])
        c[1].metric("نسبة الربح", f"{h['win_rate_pct']}%" if h["win_rate_pct"] is not None else "-")
        c[2].metric("متوسط R", h["avg_r_multiple"] if h["avg_r_multiple"] is not None else "-")
        c[3].metric("صافي الربح", f"${h['net_pnl']:,.2f}")
        if h["recent"]:
            st.dataframe(pd.DataFrame([{
                "الرمز": t["symbol"], "الدخول": t["entry"], "الخروج": t["exit"], "ربح $": t["pnl"],
                "R": t.get("r_multiple"), "السبب": t["close_reason"], "التاريخ": t["closed_at"],
            } for t in reversed(h["recent"])]), hide_index=True, width="stretch")
    except Exception as e:
        st.error(f"تعذّر قراءة الحساب: {e}")

    from agent import dca
    st.markdown("### 🟢 النواة: الشراء الدوري الأسبوعي")
    plan = " · ".join(f"{sym} {w * settings.dca_weekly_usd:.0f}$" for sym, w in dca.allocation().items())
    st.caption(f"{'مفعّل' if settings.dca_enabled else 'متوقف'} · {plan} كل أسبوع · الدفعة القادمة: "
               f"{dca.next_run()} · الوكيل لا يبيع النواة أبداً، القرار لك.")
    try:
        core = dca.holdings()
        if core["positions"]:
            c = st.columns(3)
            c[0].metric("المستثمر", f"${core['invested']:,.2f}")
            c[1].metric("القيمة الآن", f"${core['value']:,.2f}")
            c[2].metric("الربح/الخسارة", f"${core['pnl']:,.2f}", f"{core['pnl_pct']:+.1f}%")
            st.dataframe(pd.DataFrame([{
                "الرمز": r["symbol"], "عدد الدفعات": r["buys"], "الكمية": round(r["units"], 6),
                "متوسط السعر": round(r["avg_price"], 2), "السعر الآن": r["price"],
                "المستثمر $": round(r["invested"], 2), "القيمة $": r["value"], "التغير %": r["pnl_pct"],
            } for r in core["positions"]]), hide_index=True, width="stretch")
        else:
            st.caption("لا توجد مشتريات بعد.")
    except Exception as e:
        st.error(f"تعذّر قراءة النواة: {e}")


# ── halal list ─────────────────────────────────────────────────
with tab_halal:
    st.markdown("## قائمتك الشرعية")
    u = universe()
    c1, c2 = st.columns(2)
    c1.markdown("**عملات معتمدة للتداول**\n\n" + " · ".join(u.get("crypto_spot_bases", [])))
    c1.markdown("**عملات محظورة**\n\n" + " · ".join(u.get("crypto_blocked_bases", [])))
    c2.markdown("**أسهم وصناديق معتمدة يدوياً**\n\n" + (" · ".join(u.get("stocks_etfs", [])) or "-"))
    c2.markdown("**أسهم محظورة يدوياً**\n\n" + (" · ".join(u.get("stocks_blocked", [])) or "-"))
    st.info("الأسهم تُفحص تلقائياً بمعيار هيئة المحاسبة والمراجعة رقم 21. لتعديل القائمة افتح الملف "
            "sharia_universe.json ثم أعد تشغيل الواجهة. هذا فحص منهجي وليس فتوى.")
