"""Runtime configuration loaded from environment / .env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


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
    exchange_testnet: bool = os.getenv("EXCHANGE_TESTNET", "true").lower() == "true"

    state_file: Path = ROOT / "data" / "paper_state.json"


settings = Settings()
