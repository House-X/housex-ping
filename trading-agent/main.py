"""Interactive trading desk in the terminal.

    python main.py            # chat with the agent
    python main.py --check    # just check SL/TP on open paper positions (for cron)
    python main.py --scan bist|crypto|new|trending   # print an opportunity scan, no AI cost
    python main.py --verify   # check the Binance connection and API-key permissions
    python main.py --watch    # keep syncing SL/TP exits every 30s (run in a second terminal)
"""
import json
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from agent.config import settings
from agent.core import TradingAgent
from agent import scanner
from agent.paper_broker import PaperBroker
from agent.sharia import universe

console = Console()

HELP = """[bold]أوامر سريعة[/]
  /account   ملخص الحساب والصفقات المفتوحة
  /check     فحص وقف الخسارة وجني الأرباح على الصفقات المفتوحة
  /halal     عرض قائمة الأصول المعتمدة شرعياً
  /scan bist | crypto | new | trending   مسح سريع للفرص بدون تكلفة ذكاء اصطناعي
  /new       محادثة جديدة
  /exit      خروج

[bold]أمثلة[/]
  ابحث لي عن أفضل 3 فرص في البورصة التركية هذا الأسبوع
  ما أفضل العملات المدرجة حديثاً على بينانس؟ وادرس أقواها
  حلّل BTC/USDT واعطني أفضل سيناريو للدخول اليوم
  ما أهم الأخبار المؤثرة على الذهب هذا الأسبوع؟ وما البديل الحلال للتداول عليه؟
  هل عملة ADA/USDT متوافقة مع الشريعة؟
  افتح الصفقة حسب الخطة السابقة
  راجع آخر 10 صفقات وقل لي أين أخطئ
"""


def confirm(action: str, details: dict) -> bool:
    console.print()
    console.print(Panel(json.dumps(details, indent=2, ensure_ascii=False, default=str),
                        title=f"[bold yellow]{action}[/] — mode: "
                              f"{'paper' if settings.trading_mode == 'paper' else 'binance-' + settings.binance_env}",
                        border_style="yellow"))
    return Confirm.ask("[bold]Approve?[/]", default=False)


def on_tool(name: str, args: dict) -> None:
    console.print(f"\n[dim]→ {name} {json.dumps(args, ensure_ascii=False)}[/]")


def run_scan(kind: str) -> None:
    with console.status("جارٍ المسح..."):
        if kind == "bist":
            r = scanner.scan_bist(top_n=15)
        else:
            mode = {"crypto": "established", "new": "new_listings"}.get(kind, kind)
            r = scanner.scan_crypto(mode=mode, top_n=15)
    t = Table(title=f"{r.get('market') or r.get('exchange')} — {kind}", show_lines=False)
    for col in ("symbol", "score", "setup", "rsi14", "return_3m_pct", "rel_strength_3m_pct", "sharia"):
        t.add_column(col)
    for o in r["opportunities"]:
        t.add_row(o["symbol"], str(o["score"]), o["setup"], str(o["rsi14"]),
                  str(o.get("return_3m_pct")), str(o.get("rel_strength_3m_pct")), o["sharia"]["status"])
    console.print(t)
    for e in r.get("excluded_by_sharia_screen", []):
        console.print(f"[dim]✗ {e['symbol']}: {e['status']} — {'; '.join(e['reasons'])}[/]")


def build_broker():
    if settings.trading_mode == "paper":
        return PaperBroker()
    if settings.trading_mode != "live":
        console.print("[red]TRADING_MODE must be paper or live[/]")
        sys.exit(1)

    import ccxt

    from agent.binance_broker import BinanceBroker, SecurityError
    try:
        broker = BinanceBroker()
        report = broker.verify_account()
    except (SecurityError, ValueError) as e:
        console.print(Panel(str(e), title="[bold red]Refusing to start[/]", border_style="red"))
        sys.exit(1)
    except (ccxt.AuthenticationError, ccxt.PermissionDenied) as e:
        console.print(Panel(f"Binance rejected the API key ({e}).\nDemo keys only work with "
                            "BINANCE_ENV=demo, real keys only with BINANCE_ENV=live.",
                            title="[bold red]Authentication failed[/]", border_style="red"))
        sys.exit(1)
    except ccxt.NetworkError as e:
        console.print(f"[red]Cannot reach Binance: {e}[/]")
        sys.exit(1)
    color = "red" if settings.binance_env == "live" else "green"
    console.print(Panel("\n".join(f"✓ {c}" for c in report["checks"])
                        + f"\nUSDT free: {report['usdt_free']:.2f} · max per order: {report['max_order_usd']} USD",
                        title=f"[bold {color}]Binance {settings.binance_env.upper()}[/]", border_style=color))
    if settings.binance_env == "live":
        console.print("[bold red]REAL MONEY.[/] Type LIVE to continue: ", end="")
        if input().strip() != "LIVE":
            sys.exit(0)
    return broker


def watch(broker) -> None:
    console.print("[dim]Watching positions every 30s. Ctrl+C to stop.[/]")
    while True:
        try:
            for e in broker.check_stops():
                console.print(f"[bold]{e['closed_at']}[/] {e['symbol']} — {e['close_reason']} — "
                              f"P&L {e['pnl']} ({e['r_multiple']}R)")
        except KeyboardInterrupt:
            break
        except Exception as e:  # network blips: log and keep watching
            console.print(f"[red]{type(e).__name__}: {e}[/]")
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            break


def main() -> None:
    if "--scan" in sys.argv:
        idx = sys.argv.index("--scan")
        run_scan(sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "bist")
        return

    broker = build_broker()
    if "--verify" in sys.argv:
        console.print_json(json.dumps(broker.account(), ensure_ascii=False, default=str))
        return
    if "--watch" in sys.argv:
        watch(broker)
        return

    if "--check" in sys.argv:
        for c in broker.check_stops():
            console.print(f"Closed {c['symbol']} {c['side']} — {c['close_reason']} — P&L {c['pnl']}")
        return

    agent = TradingAgent(broker, confirm=confirm, on_tool=on_tool)
    console.print(Panel(HELP, title="[bold]HOUSE X · Trading Desk Agent[/]", border_style="cyan"))

    while True:
        try:
            text = console.input("\n[bold cyan]أنت ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/new":
            agent.reset()
            console.print("[dim]محادثة جديدة.[/]")
            continue
        if text == "/account":
            console.print_json(json.dumps(broker.account(), ensure_ascii=False, default=str))
            continue
        if text == "/halal":
            console.print_json(json.dumps(universe(), ensure_ascii=False))
            continue
        if text.startswith("/scan"):
            parts = text.split()
            try:
                run_scan(parts[1] if len(parts) > 1 else "bist")
            except Exception as e:
                console.print(f"[red]Scan failed: {e}[/]")
            continue
        if text == "/check":
            closed = broker.check_stops()
            console.print(closed or "[dim]لا توجد صفقات وصلت للوقف أو الهدف.[/]")
            continue

        console.print("\n[bold green]الوكيل ›[/] ", end="")
        try:
            tail = agent.ask(text)
            if tail.startswith("\n["):
                console.print(f"[red]{tail}[/]")
        except KeyboardInterrupt:
            console.print("\n[dim]أُلغي.[/]")
        console.print()


if __name__ == "__main__":
    main()
