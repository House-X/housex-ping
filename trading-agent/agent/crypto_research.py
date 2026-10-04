"""Crypto fundamentals from CoinGecko (free public API) + a category-based Sharia pre-screen.

The pre-screen flags obvious problems (memes, gambling, interest-based lending, derivatives).
Passing it does NOT make a coin tradable: only coins the trader approves in
sharia_universe.json can be bought.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

API = "https://api.coingecko.com/api/v3"

BLOCK_CATEGORIES = (  # substring match on CoinGecko category names, lower-case
    "meme", "gambling", "lending/borrowing", "yield aggregator", "yield farming", "derivatives",
    "perpetuals", "options", "prediction market", "nsfw", "adult",
)
REVIEW_CATEGORIES = (
    "decentralized finance", "decentralized exchange", "staking", "restaking", "liquid staking",
    "exchange-based tokens", "stablecoin", "real world assets", "privacy", "launchpad",
)


def _get(path: str, **params) -> dict | list:
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    headers = {"accept": "application/json", "user-agent": "housex-trading-agent"}
    if key := os.getenv("COINGECKO_API_KEY"):
        headers["x-cg-demo-api-key"] = key
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        return json.loads(r.read())


def coin_id(symbol: str) -> str:
    """Map a ticker (BTC, or BTC/USDT) to the CoinGecko id with the best market-cap rank."""
    base = symbol.upper().split("/")[0]
    coins = [c for c in _get("/search", query=base).get("coins", []) if c["symbol"].upper() == base]
    if not coins:
        raise ValueError(f"{base} not found on CoinGecko")
    return min(coins, key=lambda c: c.get("market_cap_rank") or 10**9)["id"]


def sharia_prescreen(categories: list[str]) -> dict:
    cats = [c.lower() for c in categories if c]
    blocked = sorted({c for c in cats for k in BLOCK_CATEGORIES if k in c})
    review = sorted({c for c in cats for k in REVIEW_CATEGORIES if k in c})
    if blocked:
        return {"prescreen": "likely_not_compliant", "flags": blocked,
                "note": "Category indicates gambling/speculation (maysir, gharar) or interest-based activity."}
    if review:
        return {"prescreen": "needs_scholar_review", "flags": review,
                "note": "Mixed-activity category: check whether the protocol earns or pays interest."}
    return {"prescreen": "no_red_flags_found", "flags": [],
            "note": "No prohibited category found. Still requires approval in sharia_universe.json."}


def tokenomics_flags(md: dict) -> list[str]:
    flags = []
    mcap, fdv = md.get("market_cap"), md.get("fdv")
    if mcap and fdv and fdv / mcap > 2:
        flags.append(f"FDV is {fdv / mcap:.1f}x market cap: large future token unlocks (dilution risk)")
    if md.get("circulating_supply") and md.get("total_supply") and \
            md["circulating_supply"] / md["total_supply"] < 0.5:
        flags.append("Less than 50% of supply is circulating")
    if mcap and mcap < 50e6:
        flags.append("Market cap under $50M: thin liquidity, manipulation risk")
    if mcap and md.get("volume_24h") and md["volume_24h"] / mcap > 0.5:
        flags.append("24h volume above 50% of market cap: speculative churn")
    if md.get("ath_drawdown_pct") is not None and md["ath_drawdown_pct"] < -85:
        flags.append(f"{md['ath_drawdown_pct']:.0f}% below all-time high")
    return flags


def research(symbol: str) -> dict:
    cid = coin_id(symbol)
    d = _get(f"/coins/{cid}", localization="false", tickers="false", market_data="true",
             community_data="false", developer_data="true", sparkline="false")
    m = d.get("market_data", {})
    usd = lambda k: (m.get(k) or {}).get("usd")  # noqa: E731
    md = {
        "price": usd("current_price"), "market_cap": usd("market_cap"),
        "fdv": usd("fully_diluted_valuation"), "volume_24h": usd("total_volume"),
        "market_cap_rank": d.get("market_cap_rank"),
        "circulating_supply": m.get("circulating_supply"), "total_supply": m.get("total_supply"),
        "max_supply": m.get("max_supply"),
        "ath": usd("ath"), "ath_drawdown_pct": usd("ath_change_percentage"),
        "change_30d_pct": m.get("price_change_percentage_30d"),
        "change_1y_pct": m.get("price_change_percentage_1y"),
    }
    dev = d.get("developer_data") or {}
    categories = d.get("categories") or []
    return {
        "id": cid, "symbol": d.get("symbol", "").upper(), "name": d.get("name"),
        "categories": categories,
        "description": ((d.get("description") or {}).get("en") or "")[:700],
        "genesis_date": d.get("genesis_date"),
        "homepage": next(iter((d.get("links") or {}).get("homepage") or []), None),
        "market": md,
        "developer_activity": {"commits_4w": dev.get("commit_count_4_weeks"), "github_stars": dev.get("stars")},
        "tokenomics_flags": tokenomics_flags(md),
        "sharia_prescreen": sharia_prescreen(categories),
    }


def trending() -> list[dict]:
    items = _get("/search/trending").get("coins", [])
    return [{"symbol": i["item"]["symbol"].upper(), "name": i["item"]["name"],
             "market_cap_rank": i["item"].get("market_cap_rank")} for i in items]
