"""Hard risk rules. These run in code, so the model cannot talk its way past them.

Spot, long-only, cash-funded: leverage is fixed at 1x and is NOT configurable.
"""
from __future__ import annotations

from .config import settings

MAX_LEVERAGE = 1.0  # by design: never borrow, never trade on margin


def position_size(equity: float, free_cash: float, entry: float, stop_loss: float,
                  risk_pct: float | None = None) -> dict:
    """Units to buy so that hitting the stop loses at most risk_pct of equity,
    capped by the cash actually available and the per-position allocation limit."""
    risk_pct = min(risk_pct or settings.risk_per_trade_pct, settings.risk_per_trade_pct)
    stop_distance = entry - stop_loss
    if stop_distance <= 0:
        raise ValueError("Stop loss must be below entry")

    units = equity * risk_pct / 100 / stop_distance
    cap_notional = min(free_cash, equity * settings.max_position_pct / 100)
    capped_by = None
    if units * entry > cap_notional:
        units = cap_notional / entry
        capped_by = "available cash" if free_cash <= equity * settings.max_position_pct / 100 \
            else f"max position size {settings.max_position_pct}% of equity"

    risk_amount = units * stop_distance
    return {
        "units": units,
        "cost": round(units * entry, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_pct_of_equity": round(risk_amount / equity * 100, 3) if equity else None,
        "allocation_pct_of_equity": round(units * entry / equity * 100, 2) if equity else None,
        "leverage": MAX_LEVERAGE,
        "capped_by": capped_by,
        "stop_distance_pct": round(stop_distance / entry * 100, 3),
    }


def validate_trade(entry: float, stop_loss: float, take_profit: float, open_positions: int,
                   today_pnl: float, equity: float, free_cash: float) -> list[str]:
    """Return a list of rule violations; empty list means the trade is allowed."""
    errors = []
    if not (stop_loss < entry < take_profit):
        errors.append("Spot buy requires stop_loss < entry < take_profit")
    else:
        rr = (take_profit - entry) / (entry - stop_loss)
        if rr < settings.min_reward_risk:
            errors.append(f"Reward/risk {rr:.2f} is below the minimum {settings.min_reward_risk}")
    if free_cash <= 0:
        errors.append("No free cash: all capital is already invested (no borrowing allowed)")
    if open_positions >= settings.max_open_positions:
        errors.append(f"Max open positions reached ({settings.max_open_positions})")
    if equity > 0 and today_pnl <= -equity * settings.max_daily_loss_pct / 100:
        errors.append(f"Daily loss limit hit ({settings.max_daily_loss_pct}%). Trading paused until tomorrow.")
    return errors


def rules_summary() -> dict:
    return {
        "leverage": "none (1x, spot only, cash-funded)",
        "short_selling": "not allowed",
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_position_pct_of_equity": settings.max_position_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_open_positions": settings.max_open_positions,
        "min_reward_risk": settings.min_reward_risk,
        "analysis_required_within_min": settings.analysis_max_age_min,
    }
