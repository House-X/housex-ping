"""Interactive trading desk in the terminal.

    python main.py            # chat with the agent
    python main.py --check    # just check SL/TP on open paper positions (for cron)
"""
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from agent.config import settings
from agent.core import TradingAgent
from agent.paper_broker import PaperBroker
from agent.sharia import universe

console = Console()

HELP = """[bold]أوامر سريعة[/]
  /account   ملخص الحساب والصفقات المفتوحة
  /check     فحص وقف الخسارة وجني الأرباح على الصفقات المفتوحة
  /halal     عرض قائمة الأصول المعتمدة شرعياً
  /new       محادثة جديدة
  /exit      خروج

[bold]أمثلة[/]
  حلّل BTC/USDT واعطني أفضل سيناريو للدخول اليوم
  ما أهم الأخبار المؤثرة على الذهب هذا الأسبوع؟ وما البديل الحلال للتداول عليه؟
  هل عملة ADA/USDT متوافقة مع الشريعة؟
  افتح الصفقة حسب الخطة السابقة
  راجع آخر 10 صفقات وقل لي أين أخطئ
"""


def confirm(action: str, details: dict) -> bool:
    console.print()
    console.print(Panel(json.dumps(details, indent=2, ensure_ascii=False, default=str),
                        title=f"[bold yellow]{action}[/] — mode: {settings.trading_mode}",
                        border_style="yellow"))
    return Confirm.ask("[bold]Approve?[/]", default=False)


def on_tool(name: str, args: dict) -> None:
    console.print(f"\n[dim]→ {name} {json.dumps(args, ensure_ascii=False)}[/]")


def main() -> None:
    if settings.trading_mode != "paper":
        console.print("[red]Live mode is not wired up yet (step 3). Set TRADING_MODE=paper.[/]")
        sys.exit(1)
    broker = PaperBroker()

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
