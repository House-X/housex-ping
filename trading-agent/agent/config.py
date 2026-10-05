"""Runtime configuration loaded from environment / .env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True: the project .env wins over stale variables set elsewhere in Windows/the shell
load_dotenv(ROOT / ".env", override=True)


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    effort: str = os.getenv("CLAUDE_EFFORT", "high")

    trading_mode: str = os.getenv("TRADING_MODE", "paper").lower()
    paper_starting_balance: float = _f("PAPER_STARTING_BALANCE", 10_000)

    risk_per_trade_pct: float = _f("RISK_PER_TRADE_PCT", 1.0)
    max_daily_loss_pct: float = _f("MAX_DAILY_LOSS_PCT", 3.0)
    max_open_positions: int = int(_f("MAX_OPEN_POSITIONS", 3))
    min_reward_risk: float = _f("MIN_REWARD_RISK", 1.5)
    max_position_pct: float = _f("MAX_POSITION_PCT", 30.0)
    analysis_max_age_min: int = int(_f("ANALYSIS_MAX_AGE_MIN", 60))

    crypto_exchange: str = os.getenv("CRYPTO_EXCHANGE", "binance")
    exchange_api_key: str = os.getenv("EXCHANGE_API_KEY", "")
    exchange_api_secret: str = os.getenv("EXCHANGE_API_SECRET", "")
    # demo = Binance Demo Trading (real prices, fake funds) | testnet = spot testnet | live = real money
    binance_env: str = os.getenv("BINANCE_ENV", "demo").lower()
    live_max_order_usd: float = _f("LIVE_MAX_ORDER_USD", 50)  # hard cap per order while you build trust
    require_ip_whitelist: bool = os.getenv("REQUIRE_IP_WHITELIST", "true").lower() == "true"

    # Proactive explorer: scans run every N hours (free); the AI deep-dive is capped per day (paid)
    explore_enabled: bool = os.getenv("EXPLORE_ENABLED", "true").lower() == "true"
    explore_every_hours: float = _f("EXPLORE_EVERY_HOURS", 4)
    explore_ai_max_per_day: int = int(_f("EXPLORE_AI_MAX_PER_DAY", 2))
    explore_min_score: int = int(_f("EXPLORE_MIN_SCORE", 75))

    # Alerts & pre-approved automatic buys
    alert_expiry_days: int = int(_f("ALERT_EXPIRY_DAYS", 7))
    auto_buy_max_chase_pct: float = _f("AUTO_BUY_MAX_CHASE_PCT", 1.5)  # skip if price ran this far past the trigger
    auto_buy_live: bool = os.getenv("AUTO_BUY_LIVE", "false").lower() == "true"  # real-money auto-buys off by default

    state_file: Path = ROOT / "data" / "paper_state.json"


settings = Settings()
