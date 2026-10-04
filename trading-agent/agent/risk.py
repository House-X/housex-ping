"""Hard risk rules. These run in code, so the model cannot talk its way past them."""
from __future__ import annotations

from .config import settings


def position_size(equity: float, entry: float, stop_loss: float,
                  risk_pct: float | None = None) -> dict:
    """Units to trade so that hitting the stop loses exactly risk_pct of equity."""
    risk_pct = min(risk_pct or settings.risk_per_trade_pct, settings.risk_per_trade_pct)
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        raise ValueError("Stop loss must differ from entry")
    risk_amount = equity * risk_pct / 100
    units = risk_amount / stop_distance
    notional = units * entry
    leverage = notional / equity if equity else 0
    if leverage > settings.max_leverage:
        units = settings.max_leverage * equity / entry
        notional = units * entry
        risk_amount = units * stop_distance
        leverage = settings.max_leverage
    return {
        "units": units,
        "notional": round(notional, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_pct_of_equity": round(risk_amount / equity * 100, 3),
        "effective_leverage": round(leverage, 2),
        "stop_distance_pct": round(stop_distance / entry * 100, 3),
    }


def validate_trade(side: str, entry: float, stop_loss: float, take_profit: float,
                   open_positions: int, today_pnl: float, equity: float) -> list[str]:
    """Return a list of rule violations; empty list means the trade is allowed."""
    errors = []
    if side not in ("buy", "sell"):
        errors.append("side must be 'buy' or 'sell'")
        return errors
    if side == "buy" and not (stop_loss < entry < take_profit):
        errors.append("For a BUY: stop_loss < entry < take_profit")
    if side == "sell" and not (take_profit < entry < stop_loss):
        errors.append("For a SELL: take_profit < entry < stop_loss")
    if not errors:
        rr = abs(take_profit - entry) / abs(entry - stop_loss)
        if rr < settings.min_reward_risk:
            errors.append(f"Reward/risk {rr:.2f} is below the minimum {settings.min_reward_risk}")
    if open_positions >= settings.max_open_positions:
        errors.append(f"Max open positions reached ({settings.max_open_positions})")
    if equity > 0 and today_pnl <= -equity * settings.max_daily_loss_pct / 100:
        errors.append(f"Daily loss limit hit ({settings.max_daily_loss_pct}%). Trading paused until tomorrow.")
    return errors


def rules_summary() -> dict:
    return {
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_open_positions": settings.max_open_positions,
        "min_reward_risk": settings.min_reward_risk,
        "max_leverage": settings.max_leverage,
    }
