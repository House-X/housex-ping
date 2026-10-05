"""Personal Trading Desk - browser interface with proper Arabic (right-to-left) rendering.

    streamlit run app.py
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent import scanner
from agent.config import settings
from agent.sharia import universe

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
}
ACTION_LABELS = {"OPEN TRADE": "فتح صفقة", "MODIFY TRADE": "تعديل صفقة", "CLOSE TRADE": "إغلاق صفقة"}


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

    if st.button("🔄 فحص الوقف والأهداف", use_container_width=True):
        closed = broker.check_stops()
        st.success(f"أُغلقت {len(closed)} صفقة" if closed else "لا توجد صفقات وصلت للوقف أو الهدف")
    if st.button("🆕 محادثة جديدة", use_container_width=True):
        ss.chat = []
        ss.pop("agent", None)
        st.rerun()
    st.caption("لا رافعة · لا بيع على المكشوف · حلال فقط · التحليل قبل التنفيذ")

tab_chat, tab_scan, tab_acc, tab_halal = st.tabs(["💬 المحادثة", "🔎 الفرص", "📊 الحساب", "☪️ القائمة الشرعية"])


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
        if c1.button("✅ موافق، نفّذ", key=f"ok_{item['id']}", type="primary", use_container_width=True):
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
        if c2.button("✖️ رفض", key=f"no_{item['id']}", use_container_width=True):
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

    prompt = st.chat_input("اكتب سؤالك هنا...")
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
            "الرمز": o["symbol"], "الدرجة": o["score"], "نوع الفرصة": o["setup"], "RSI": o["rsi14"],
            "أداء 3 أشهر %": o.get("return_3m_pct"), "بالدولار %": o.get("return_3m_usd_pct"),
            "القوة النسبية %": o.get("rel_strength_3m_pct"), "الحالة الشرعية": o["sharia"]["status"],
        } for o in r["opportunities"]]).dropna(axis=1, how="all")
        st.dataframe(df, hide_index=True, use_container_width=True)
        excluded = r.get("excluded_by_sharia_screen") or []
        if excluded:
            with st.expander(f"مستبعد شرعياً ({len(excluded)})"):
                for e in excluded:
                    st.markdown(f"- **{e['symbol']}** · {e['status']}: <span class='ltr'>{'; '.join(e['reasons'])}</span>",
                                unsafe_allow_html=True)


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
            } for p in acc["open_positions"]]), hide_index=True, use_container_width=True)
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
            } for t in reversed(h["recent"])]), hide_index=True, use_container_width=True)
    except Exception as e:
        st.error(f"تعذّر قراءة الحساب: {e}")


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
