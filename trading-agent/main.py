"""Interactive trading desk in the terminal.

    python main.py            # chat with the agent
    python main.py --check    # just check SL/TP on open paper positions (for cron)
    python main.py --scan bist|crypto|new|trending   # print an opportunity scan, no AI cost
    python main.py --verify   # check the Binance connection and API-key permissions
    python main.py --watch    # sync SL/TP exits every 30s + run the opportunity explorer on schedule
    python main.py --explore  # run one exploration cycle now
    python main.py --backtest BTC/USDT ETH/USDT [--years 4]   # historical test of the scanner rules
    python main.py --shortterm BTC/USDT ETH/USDT [--tf 1h] [--days 730]   # test quick in-and-out strategies
    python main.py --swing BTC/USDT ETH/USDT [--tf 1d|4h] [--years 8]   # trend-riding with a trailing stop
    python main.py --robustness        # run the swing rule on every approved coin and rank them
    python main.py --telegram-setup   # link your Telegram bot (after messaging it once)
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
        import os
        if not sys.stdin.isatty():  # a background service can't type LIVE: it needs an explicit flag
            if os.getenv("LIVE_CONFIRMED", "").strip() != "LIVE":
                console.print("[red]Real money in a background service needs LIVE_CONFIRMED=LIVE in .env[/]")
                sys.exit(1)
            return broker
        console.print("[bold red]REAL MONEY.[/] Type LIVE to continue: ", end="")
        if input().strip() != "LIVE":
            sys.exit(0)
    return broker


def watch(broker) -> None:
    from agent import alerts, dca, explorer, notify, swing_live
    from agent.telegram_bot import PhoneDesk
    console.print(f"[dim]Watching positions and alerts every 30s. Explorer: "
                  f"{'every ' + str(settings.explore_every_hours) + 'h' if settings.explore_enabled else 'off'}"
                  f" · Telegram: {'on (buttons + commands)' if notify.configured() else 'off'}. Ctrl+C to stop.[/]")
    desk = None
    if notify.configured():
        desk = PhoneDesk(broker, explore_fn=lambda: explorer.explore(broker, force=True))
        try:
            desk.start()
            notify.send("🟢 المراقب يعمل. اكتب /help لأوامر الهاتف.")
        except Exception as e:
            console.print(f"[red]Telegram commands unavailable: {e}[/]")
            desk = None
    while True:
        try:
            for e in broker.check_stops():
                console.print(f"[bold]{e['closed_at']}[/] {e['symbol']} — {e['close_reason']} — "
                              f"P&L {e['pnl']} ({e['r_multiple']}R)")
            for a in alerts.check(broker):
                console.print(f"[magenta]Alert {a['id']} {alerts.describe(a)} → {a['status']}: "
                              f"{a.get('result') or ''}[/]")
            if settings.dca_enabled and dca.due():
                b = dca.run(broker)
                console.print(f"[green]Core DCA: {len(b['buys'])} buys, {len(b['skipped'])} skipped[/]")
            if settings.swing_enabled and swing_live.due():
                r = swing_live.run(broker)
                if swing_live.lab_due():  # quarterly re-check of which coins still suit the wave
                    swing_live.run_lab()
                console.print(f"[blue]Swing: {len(r['proposals'])} signals, {len(r['raised'])} stops raised"
                              f"{', errors: ' + '; '.join(r['errors']) if r['errors'] else ''}[/]")
            if settings.explore_enabled and explorer.due():
                console.print("[cyan]Exploring markets for opportunities...[/]")
                run = explorer.explore(broker)
                console.print(f"[cyan]Explorer: {len(run['tradable'])} tradable, "
                              f"{len(run['research'])} research candidates, AI={run['ai']}[/]")
        except KeyboardInterrupt:
            break
        except Exception as e:  # network blips: log and keep watching
            console.print(f"[red]{type(e).__name__}: {e}[/]")
        try:
            if desk:  # long-poll Telegram for ~30s: button taps are handled within a second
                deadline = time.time() + 30
                while time.time() < deadline:
                    desk.poll(timeout=max(1, int(deadline - time.time())))
            else:
                time.sleep(30)
        except KeyboardInterrupt:
            break


def run_backtest(args: list[str]) -> None:
    from agent import backtest
    years = 4
    if "--years" in args:
        years = int(args[args.index("--years") + 1])
    symbols = [a for a in args if not a.startswith("--") and not a.isdigit()] or ["BTC/USDT", "ETH/USDT"]
    with console.status(f"Backtesting {', '.join(symbols)} over {years} years..."):
        r = backtest.backtest(symbols, years=years)
    t = Table(title=f"Backtest — {years}y · min score {r['rules']['min_score']} · R:R {r['rules']['reward_risk']}")
    for col in ("symbol", "trades", "win %", "avg R", "profit factor", "return %", "max DD %",
                "buy&hold %", "B&H max DD %"):
        t.add_column(col)
    for sym, res in r["per_symbol"].items():
        if "error" in res:
            t.add_row(sym, res["error"], *[""] * 7)
            continue
        st = res["stats"]
        t.add_row(sym, str(st["trades"]), str(st["win_rate_pct"]), str(st["avg_r"]), str(st["profit_factor"]),
                  str(st["total_return_pct"]), str(st["max_drawdown_pct"]), str(st["buy_and_hold_pct"]),
                  str(st["buy_and_hold_max_drawdown_pct"]))
    console.print(t)
    console.print(f"[dim]{r['caveats']}[/]")


def run_shortterm(args: list[str]) -> None:
    from agent import shortterm
    tf = args[args.index("--tf") + 1] if "--tf" in args else "1h"
    days = int(args[args.index("--days") + 1]) if "--days" in args else 730
    symbols = [a for a in args if "/" in a or "." in a] or ["BTC/USDT", "ETH/USDT"]
    with console.status(f"Testing short-term strategies on {', '.join(symbols)} ({tf}, {days}d)..."):
        r = shortterm.research(symbols, timeframe=tf, days=days)
    t = Table(title=f"Short-term · {tf} · {days}d · judged on the last 30% only")
    for col in ("symbol", "strategy", "verdict", "trades", "per week", "win %", "PF", "return %",
                "max DD %", "B&H %", "fees/profit %", "RR · time stop"):
        t.add_column(col)
    for sym, v in r["per_symbol"].items():
        if "error" in v:
            t.add_row(sym, v["error"], *[""] * 10)
            continue
        for x in v["results"]:
            s = x["test"]
            t.add_row(sym, x["strategy"], x["verdict"], str(s["trades"]), str(s["trades_per_week"]),
                      str(s["win_rate_pct"]), str(s["profit_factor"]), str(s["total_return_pct"]),
                      str(s["max_drawdown_pct"]), str(s["buy_and_hold_pct"]),
                      str(s["fees_vs_gross_profit_pct"]),
                      f"{x['params']['reward_risk']} · {x['params']['time_stop_bars']}")
    console.print(t)
    console.print(f"[dim]{r['rules']}[/]")


def run_swing(args: list[str]) -> None:
    from agent import swing
    tf = args[args.index("--tf") + 1] if "--tf" in args else "1d"
    years = int(args[args.index("--years") + 1]) if "--years" in args else 9
    symbols = [a for a in args if "/" in a or "." in a] or ["BTC/USDT", "ETH/USDT"]
    with console.status(f"Testing swing strategies on {', '.join(symbols)} ({tf}, {years}y)..."):
        r = swing.research(symbols, timeframe=tf, years=years)
    t = Table(title=f"Swing · {tf} · {years}y · verdict on the last 30%")
    for col in ("symbol", "strategy", "verdict", "test trades", "test return %", "test max DD %",
                "B&H %", "B&H DD %", "whole return %", "whole DD %", "whole B&H %", "whole B&H DD %"):
        t.add_column(col)
    for sym, v in r["per_symbol"].items():
        if "error" in v:
            t.add_row(sym, v["error"], *[""] * 10)
            continue
        for x in v["results"]:
            a, w = x["test"], x["whole"]
            t.add_row(sym, x["strategy"], x["verdict"], str(a["trades"]), str(a["total_return_pct"]),
                      str(a["max_drawdown_pct"]), str(a["buy_and_hold_pct"]),
                      str(a["buy_and_hold_max_drawdown_pct"]), str(w["total_return_pct"]),
                      str(w["max_drawdown_pct"]), str(w["buy_and_hold_pct"]),
                      str(w["buy_and_hold_max_drawdown_pct"]))
    console.print(t)
    for sym, v in r["per_symbol"].items():
        for x in v.get("results", []):
            rb = x["robustness"]
            console.print(f"{sym} {x['strategy']}: {rb['profitable']}/{rb['settings']} settings profitable, "
                          f"{rb['smaller_drawdown']} with a smaller drawdown than holding, "
                          f"{rb['beat_hold_return']} beat holding · median {rb['median_return_pct']}% / "
                          f"DD {rb['median_drawdown_pct']}%")
    console.print(f"[dim]{r['rules']}[/]")


def run_robustness() -> None:
    from agent import swing
    with console.status("Testing the swing rule on every approved coin..."):
        r = swing.scan_universe(progress=lambda i, n, s: console.log(f"{i + 1}/{n} {s}"))
    t = Table(title="Robustness lab · breakout + trailing stop · daily")
    for col in ("symbol", "class", "years", "profitable", "smaller DD", "beat hold",
                "test %", "hold test %", "DD %", "hold DD %"):
        t.add_column(col)
    for x in r["rows"]:
        rb = x["robustness"]
        t.add_row(x["symbol"], x["class"], str(x["years"]), f"{rb['profitable']}/{rb['settings']}",
                  f"{rb['smaller_drawdown']}/{rb['settings']}", f"{rb['beat_hold_return']}/{rb['settings']}",
                  str(x["test"]["total_return_pct"]), str(x["test"]["buy_and_hold_pct"]),
                  str(x["whole"]["max_drawdown_pct"]), str(x["whole"]["buy_and_hold_max_drawdown_pct"]))
    console.print(t)
    for x in r["skipped"]:
        console.print(f"[dim]skipped {x['symbol']}: {x['why']}[/]")
    console.print(f"[green]Passed: {', '.join(r['passed']) or 'none'}[/]")


def main() -> None:
    if "--telegram-setup" in sys.argv:
        from agent import notify
        console.print(notify.setup_chat_id())
        return
    if "--robustness" in sys.argv:
        run_robustness()
        return
    if "--swing" in sys.argv:
        run_swing(sys.argv[sys.argv.index("--swing") + 1:])
        return
    if "--shortterm" in sys.argv:
        run_shortterm(sys.argv[sys.argv.index("--shortterm") + 1:])
        return
    if "--backtest" in sys.argv:
        run_backtest(sys.argv[sys.argv.index("--backtest") + 1:])
        return
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
    if "--explore" in sys.argv:
        from agent import explorer
        with console.status("Exploring markets..."):
            run = explorer.explore(broker)
        console.print(run["report"] or run["telegram"])
        return

    if "--check" in sys.argv:
        for c in broker.check_stops():
            console.print(f"Closed {c['symbol']} {c['side']} — {c['close_reason']} — P&L {c['pnl']}")
        return

    agent = TradingAgent(broker, confirm=confirm, on_tool=on_tool)
    console.print(Panel(HELP, title="[bold]Personal Trading Desk[/]", border_style="cyan"))

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
