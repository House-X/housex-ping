"""Offline tests: synthetic prices, no network, no API key."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agent import tools as tools_mod
from agent.indicators import snapshot
from agent.paper_broker import PaperBroker
from agent.risk import position_size, validate_trade
from agent.tools import ToolExecutor, validate_input


def synthetic_ohlcv(n=300, start=100.0, drift=0.001, seed=1):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": rng.uniform(100, 200, n)}, index=idx)


@pytest.fixture
def broker(tmp_path):
    prices = {"BTC/USDT": 100.0}
    b = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: prices[s.upper()])
    b._prices = prices
    return b


def test_snapshot_uptrend():
    snap = snapshot(synthetic_ohlcv(drift=0.003))
    assert snap["trend"] == "uptrend"
    assert 0 <= snap["rsi14"] <= 100
    assert snap["atr14"] > 0


def test_position_size_risks_exactly_one_pct():
    s = position_size(10_000, 10_000, entry=100, stop_loss=90)
    assert s["risk_amount"] == pytest.approx(100)
    assert s["units"] == pytest.approx(10)
    assert s["leverage"] == 1.0


def test_never_spends_more_than_cash_or_allocation_cap():
    # Tight stop would need 50x leverage; must be capped at 30% of equity
    s = position_size(10_000, 10_000, entry=100, stop_loss=99.98)
    assert s["cost"] <= 3_000 and s["capped_by"].startswith("max position")
    # Only 500 free cash left
    s = position_size(10_000, 500, entry=100, stop_loss=90)
    assert s["cost"] == pytest.approx(500) and s["capped_by"] == "available cash"


def test_validation_rules():
    assert validate_trade(100, 98, 101, 0, 0, 10_000, 10_000)  # RR 0.5 rejected
    assert validate_trade(100, 102, 90, 0, 0, 10_000, 10_000)  # short-style levels rejected
    assert not validate_trade(100, 98, 104, 0, 0, 10_000, 10_000)
    assert validate_trade(100, 98, 104, 0, -400, 10_000, 10_000)  # daily loss hit
    assert validate_trade(100, 98, 104, 0, 0, 10_000, 0)  # no free cash


@pytest.mark.parametrize("symbol,status", [
    ("BTC/USDT", "compliant"), ("ETH/USDT", "compliant"), ("SPUS", "compliant"),
    ("BTC/USDT:USDT", "not_compliant"), ("DOGE/USDT", "not_compliant"),
    ("EURUSD", "not_compliant"), ("XAUUSD", "not_compliant"), ("NAS100", "not_compliant"),
    ("GC=F", "not_compliant"), ("WLD/USDT", "review_required"), ("AAPL", "review_required"),
])
def test_sharia_screen(symbol, status):
    from agent.sharia import check
    r = check(symbol, auto_screen=False)
    assert r["status"] == status
    assert r["tradable"] == (status == "compliant")


def test_non_compliant_symbol_is_rejected(broker):
    broker._prices["EURUSD"] = 1.10
    preview = broker.preview_order("EURUSD", 1.09, 1.13)
    assert not preview["allowed"] and "Sharia" in preview["violations"][0]


def test_paper_round_trip(broker):
    broker.open_position("BTC/USDT", 90, 130, "test")
    assert broker.free_cash() == pytest.approx(9_000)
    broker._prices["BTC/USDT"] = 131
    closed = broker.check_stops()
    assert closed[0]["close_reason"] == "take_profit hit"
    assert closed[0]["r_multiple"] == pytest.approx(3.0)
    assert broker.account()["balance"] == pytest.approx(10_300)
    assert broker.free_cash() == pytest.approx(10_300)
    assert broker.history()["win_rate_pct"] == 100


def test_input_validation():
    assert validate_input("open_trade", {"symbol": "X"})
    assert validate_input("preview_trade", {"symbol": "X", "side": "sell", "stop_loss": 1, "take_profit": 2})
    assert validate_input("get_account", {}) is None


ORDER = {"symbol": "BTC/USDT", "stop_loss": 90, "take_profit": 130, "rationale": "x"}


def test_trade_refused_without_prior_analysis(broker):
    ex = ToolExecutor(broker, confirm=lambda a, d: True)
    with pytest.raises(PermissionError, match="analysis"):
        ex.run("open_trade", ORDER)
    assert broker.state["positions"] == []


def test_trade_allowed_after_analysis(broker, monkeypatch):
    monkeypatch.setattr(tools_mod.market_data, "fetch_ohlcv", lambda *a: synthetic_ohlcv())
    ex = ToolExecutor(broker, confirm=lambda a, d: True)
    ex.run("analyze_market", {"symbol": "BTC/USDT"})
    assert json.loads(ex.run("open_trade", ORDER))["opened"]["side"] == "buy"


def test_declined_order_is_not_opened(broker, monkeypatch):
    monkeypatch.setattr(tools_mod.market_data, "fetch_ohlcv", lambda *a: synthetic_ohlcv())
    ex = ToolExecutor(broker, confirm=lambda a, d: False)
    ex.run("analyze_market", {"symbol": "BTC/USDT"})
    with pytest.raises(PermissionError, match="declined"):
        ex.run("open_trade", ORDER)
    assert broker.state["positions"] == []


def test_agent_loop_with_fake_client(broker, monkeypatch):
    """Simulate Claude: call preview_trade, then answer."""
    from agent import core

    def msg(stop, content):
        return SimpleNamespace(stop_reason=stop, content=content)

    replies = iter([
        msg("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="preview_trade",
                                         input={"symbol": "BTC/USDT",
                                                "stop_loss": 98, "take_profit": 106})]),
        msg("end_turn", [SimpleNamespace(type="text", text="done")]),
    ])
    monkeypatch.setattr(core.anthropic, "Anthropic", lambda: None)
    agent = core.TradingAgent(broker, confirm=lambda a, d: True, on_text=lambda t: None)
    monkeypatch.setattr(agent, "_request", lambda: next(replies))
    assert agent.ask("preview") == "done"
    result = json.loads(agent.messages[2]["content"][0]["content"])
    assert result["allowed"] and result["reward_risk"] == 3.0


# ── Phase 2: stock screen, multi-currency, scanners, crypto research ──

from agent import scanner, stock_screen
from agent.crypto_research import sharia_prescreen, tokenomics_flags

CLEAN = {"name": "Clean Co", "sector": "Industrials", "industry": "Airlines", "market_cap": 1000,
         "total_debt": 200, "cash_and_investments": 100, "interest_income": 2, "revenue": 500}


@pytest.mark.parametrize("override,status,reason", [
    ({}, "compliant", "Passes"),
    ({"industry": "Banks - Regional"}, "not_compliant", "Prohibited core business"),
    ({"industry": "Beverages - Brewers"}, "not_compliant", "Prohibited core business"),
    ({"total_debt": 400}, "not_compliant", "Interest-bearing debt"),
    ({"cash_and_investments": 350}, "not_compliant", "Cash/interest-bearing"),
    ({"interest_income": 40}, "not_compliant", "Interest income"),
    ({"total_debt": None}, "review_required", "Missing balance-sheet"),
    ({"industry": "Aerospace & Defense"}, "review_required", "Mixed-activity"),
])
def test_stock_screen_rules(override, status, reason):
    r = stock_screen.evaluate("TEST.IS", {**CLEAN, **override})
    assert r["status"] == status
    assert any(reason in x for x in r["reasons"])


def test_stock_screen_purification():
    r = stock_screen.evaluate("TEST.IS", CLEAN)
    assert r["purification_pct"] == pytest.approx(0.4)


def test_sharia_check_uses_auto_screen_for_stocks(monkeypatch):
    from agent import sharia
    monkeypatch.setattr(stock_screen, "screen", lambda s: stock_screen.evaluate(s, CLEAN))
    r = sharia.check("THYAO.IS")
    assert r["tradable"] and r["ratios"]["debt_to_market_cap"] == 0.2


def test_try_position_pnl_in_usd(tmp_path, monkeypatch):
    """Buy a BIST stock in TRY: P&L must be in USD and include the lira's move."""
    from agent import sharia
    monkeypatch.setattr(sharia, "check", lambda s, **k: {"status": "compliant", "tradable": True, "reasons": []})
    prices, fx = {"THYAO.IS": 300.0}, {"TRY": 1 / 40, "USD": 1.0}
    b = PaperBroker(state_file=tmp_path / "s.json", price_fn=lambda s: prices[s], fx_fn=lambda c: fx[c])
    pos = b.open_position("THYAO.IS", 280, 360, "test")
    assert pos["currency"] == "TRY"
    assert pos["risk_amount"] == pytest.approx(100, rel=1e-3)  # 1% of $10k, in USD
    assert pos["cost"] <= 3000
    prices["THYAO.IS"] = 330.0   # +10% in TRY
    fx["TRY"] = 1 / 44           # ...but lira lost ~9%
    usd_pnl = b.account()["unrealized_pnl"]
    expected = pos["units"] * (330 / 44 - 300 / 40)
    assert usd_pnl == pytest.approx(expected, abs=0.01)
    assert usd_pnl < pos["units"] * (330 - 300) / 40  # smaller than the naive TRY gain


def test_technical_score_ranks_uptrend_above_downtrend():
    up = scanner.technical_score(synthetic_ohlcv(drift=0.004))
    down = scanner.technical_score(synthetic_ohlcv(drift=-0.004))
    assert up["score"] > down["score"]
    assert up["setup"] != "none" and down["setup"] == "none"


def test_scan_bist_filters_by_sharia(monkeypatch):
    monkeypatch.setattr(scanner, "bist_universe", lambda: ["GOOD.IS", "BANK.IS"])

    def fetch_many(tickers):
        out = {t: synthetic_ohlcv(drift=0.004, seed=i) * 1 for i, t in enumerate(tickers)}
        for t in out:
            out[t]["volume"] = 1e6  # liquid: close ~ hundreds * 1e6 > 50M TRY
        return out

    def screen(sym):
        ok = sym == "GOOD.IS"
        return {"status": "compliant" if ok else "not_compliant", "tradable": ok,
                "reasons": [] if ok else ["Prohibited core business: Banks"], "purification_pct": 0}

    r = scanner.scan_bist(fetch_many=fetch_many, screen=screen, min_score=0)
    assert [o["symbol"] for o in r["opportunities"]] == ["GOOD.IS"]
    assert r["excluded_by_sharia_screen"][0]["symbol"] == "BANK.IS"
    assert "return_3m_usd_pct" in r["opportunities"][0]


class FakeExchange:
    id = "binance"
    markets = {
        "BTC/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "BTC"},
        "ETH/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "ETH"},
        "NEW/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "NEW"},
        "DOGE/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "DOGE"},
        "USDC/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "USDC"},
        "BTCUP/USDT": {"spot": True, "active": True, "quote": "USDT", "base": "BTCUP"},
        "BTC/USDT:USDT": {"spot": False, "active": True, "quote": "USDT", "base": "BTC"},
    }

    def load_markets(self):
        return self.markets

    def fetch_tickers(self, syms=None):
        assert syms is None, "must fetch all tickers in one call (long symbol lists break the URL)"
        return {s: {"quoteVolume": 1e8, "percentage": 1.0} for s in self.markets}


def _fake_ohlcv(sym, n):
    days = 60 if sym == "NEW/USDT" else n
    df = synthetic_ohlcv(n=days, drift=0.003)
    ts = (df.index.astype("int64") // 10**6).tolist()
    return [[t, *row] for t, row in zip(ts, df[["open", "high", "low", "close", "volume"]].values.tolist())]


def test_scan_crypto_excludes_stables_leveraged_meme_and_perps():
    r = scanner.scan_crypto(exchange=FakeExchange(), fetch_ohlcv=_fake_ohlcv)
    syms = {o["symbol"] for o in r["opportunities"]}
    assert syms == {"BTC/USDT", "ETH/USDT", "NEW/USDT"}
    eth = next(o for o in r["opportunities"] if o["symbol"] == "ETH/USDT")
    assert eth["sharia"]["tradable"] is True


def test_scan_crypto_new_listings():
    r = scanner.scan_crypto(mode="new_listings", exchange=FakeExchange(), fetch_ohlcv=_fake_ohlcv)
    assert [o["symbol"] for o in r["opportunities"]] == ["NEW/USDT"]
    assert r["opportunities"][0]["listed_days_ago"] == 60
    assert r["opportunities"][0]["sharia"]["tradable"] is False


def test_crypto_prescreen_and_tokenomics():
    assert sharia_prescreen(["Meme", "Solana Ecosystem"])["prescreen"] == "likely_not_compliant"
    assert sharia_prescreen(["Lending/Borrowing Protocols"])["prescreen"] == "likely_not_compliant"
    assert sharia_prescreen(["Decentralized Exchange (DEX)"])["prescreen"] == "needs_scholar_review"
    assert sharia_prescreen(["Layer 1 (L1)"])["prescreen"] == "no_red_flags_found"
    flags = tokenomics_flags({"market_cap": 40e6, "fdv": 200e6})
    assert any("FDV" in f for f in flags) and any("under $50M" in f for f in flags)


def test_screen_cache_roundtrip_with_turkish_and_arabic(tmp_path, monkeypatch):
    """Windows with an Arabic locale defaults to cp1256, which cannot write 'Ç'. Files must be UTF-8."""
    monkeypatch.setattr(stock_screen, "CACHE_FILE", tmp_path / "cache.json")
    f = {**CLEAN, "name": "Türk Hava Yolları Çelebi — شركة"}
    r1 = stock_screen.screen("THYAO.IS", fetch=lambda s: f)
    r2 = stock_screen.screen("THYAO.IS", fetch=lambda s: 1 / 0)  # must come from cache
    assert r1 == r2 and r2["name"].startswith("Türk")


def test_ifg_adoption_in_the_universe():
    from agent import sharia
    sharia.universe.cache_clear()
    for ok in ("SOL/USDT", "ADA/USDT", "LINK/USDT", "AVAX/USDT"):
        r = sharia.check(ok)
        assert r["status"] == "compliant" and "Islamic Finance Guru" in r["reasons"][0]
    for meme in ("DOGE/USDT", "PEPE/USDT", "TRUMP/USDT"):
        assert sharia.check(meme)["status"] == "not_compliant"
    assert "riba" in sharia.check("AAVE/USDT")["reasons"][0]
    assert "taqwa" in sharia.check("ZEC/USDT")["reasons"][0]
    assert sharia.check("OM/USDT")["status"] == "not_compliant"
