"""Sharia compliance screen. Runs in code before any order; the model cannot override it.

Principles applied (based on commonly cited standards such as AAOIFI Sharia Standards
No. 1 on currency trading and No. 21 on financial papers):
  - Spot ownership only: no leverage/margin, no futures, perpetuals, options or CFDs.
  - No short selling (selling what you do not own).
  - No interest (riba): no swap/rollover, no lending or interest-bearing "earn" products.
  - Currency exchange requires immediate hand-to-hand settlement (taqabud); retail forex
    brokers sell CFDs with no real exchange, so forex is analysis-only here.
  - Gold/silver must be bought spot with real possession, not as futures or CFDs.
  - Stocks/ETFs must pass a Sharia screen (business activity + financial ratios).
  - Only instruments explicitly approved in sharia_universe.json are tradable.

This is a screening tool, not a fatwa. Confirm your list with a qualified scholar.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .config import ROOT
from .market_data import YF_ALIASES, asset_class

UNIVERSE_FILE = ROOT / "sharia_universe.json"

STOCK_SCREEN = ("Needs a Sharia screen before approval: core business must be permissible "
                "(no conventional banking/insurance, alcohol, gambling, pork, adult content, "
                "weapons), interest-bearing debt < 30% of market cap, interest-bearing cash "
                "< 30%, impure income < 5% (to be purified by donation).")

CRYPTO_SCREEN = ("Needs review before approval: a real use case or utility, not a meme or pure "
                 "gambling token, not an interest-based lending/yield protocol, no core "
                 "business in prohibited activities, and traded as spot with real ownership.")

ALTERNATIVES = {
    "gold": "Buy physical gold or a fully allocated, Sharia-certified gold product with real ownership.",
    "index": "Use a Sharia-screened ETF instead (e.g. SPUS or HLAL for US equities).",
    "forex": "Use currency analysis only as context (e.g. USD strength vs gold/crypto). "
             "Real currency exchange is only permissible spot, hand-to-hand.",
}


@lru_cache(maxsize=1)
def universe() -> dict:
    return json.loads(UNIVERSE_FILE.read_text())


def check(symbol: str) -> dict:
    s = symbol.upper().strip()
    u = universe()

    def result(status: str, reasons: list[str], alternative: str | None = None) -> dict:
        return {"symbol": s, "status": status, "tradable": status == "compliant",
                "reasons": reasons, "halal_alternative": alternative}

    if ":" in s or s.endswith("=F") or "PERP" in s or s.endswith(("-PERP", "_PERP")):
        return result("not_compliant", ["Derivative contract (futures/perpetual): no real ownership, "
                                        "leverage and funding payments."])

    cls = asset_class(s)
    if cls == "forex":
        return result("not_compliant", ["Retail forex is traded as CFDs: no real currency exchange or "
                                        "possession (taqabud), leverage and overnight swap interest."],
                      ALTERNATIVES["forex"])
    if cls == "index_commodity":
        kind = "gold" if YF_ALIASES.get(s) in ("GC=F", "SI=F") else "index"
        return result("not_compliant", ["Available only as CFD/futures here: no real ownership."],
                      ALTERNATIVES[kind])
    if cls == "crypto":
        base = s.split("/")[0]
        if base in u.get("crypto_blocked_bases", []):
            return result("not_compliant", [u.get("_crypto_blocked_reason", "Blocked token.")])
        if base in u.get("crypto_spot_bases", []):
            return result("compliant", ["Approved spot crypto asset in your Sharia universe."])
        return result("review_required", [CRYPTO_SCREEN])
    if s in u.get("stocks_etfs", []):
        return result("compliant", ["Approved Sharia-screened stock/ETF in your universe."])
    return result("review_required", [STOCK_SCREEN])
