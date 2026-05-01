"""Tests for src/backtest/portfolio.py — Week 2 Day 5."""

from __future__ import annotations

import pandas as pd
import pytest

from config.constants import TXO_MULTIPLIER
from src.backtest.portfolio import OptionLeg, Portfolio


@pytest.fixture
def short_call_leg() -> OptionLeg:
    """Short 1 call at strike 17000 entered at price 100."""
    return OptionLeg(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-15"),
        entry_price=100.0,
    )


@pytest.fixture
def long_call_leg() -> OptionLeg:
    """Long 1 call at strike 17200 entered at price 50."""
    return OptionLeg(
        contract="TXO20260219C17200",
        strike=17200,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=+1,
        entry_date=pd.Timestamp("2026-01-15"),
        entry_price=50.0,
    )


def _chain_row(strike: int, option_type: str, settle: float, bid: float, ask: float) -> dict:
    return {
        "date": pd.Timestamp("2026-01-20"),
        "expiry": pd.Timestamp("2026-02-19"),
        "strike": strike,
        "option_type": option_type,
        "settle": settle,
        "bid": bid,
        "ask": ask,
        "iv": 0.20,
        "delta": 0.16 if option_type == "call" else -0.16,
        "underlying": 17000.0,
    }


def test_portfolio_init_validates() -> None:
    with pytest.raises(ValueError):
        Portfolio(initial_capital=0)


def test_portfolio_open_credits_cash_on_short(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """Short collects premium → cash up. Long pays → cash down. Net = +50 * 50."""
    p = Portfolio(initial_capital=1_000_000.0)
    pos = p.open([short_call_leg, long_call_leg], strategy_name="VerticalCallTest")
    # Cash delta: short collects 100 (qty=-1 → -(-1)*100=+100), long pays 50 ((-1)*50=-50)
    # Net per contract = +50; * TXO_MULTIPLIER = +50*50 = +2500
    expected_cash = 1_000_000.0 + 50.0 * TXO_MULTIPLIER
    assert p.cash == pytest.approx(expected_cash)
    assert len(p.positions) == 1
    assert pos.legs[0].contract == short_call_leg.contract


def test_portfolio_close_books_realised_pnl(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """Close at lower mid (short profit) → positive realised."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VerticalCallTest")
    chain = pd.DataFrame(
        [
            _chain_row(17000, "call", settle=40.0, bid=39.0, ask=41.0),
            _chain_row(17200, "call", settle=10.0, bid=9.0, ask=11.0),
        ]
    )
    realised = p.close(0, chain)
    # short: qty=-1 * (40 - 100) = +60 per contract (decay profit)
    # long:  qty=+1 * (10 - 50)  = -40 per contract
    # net per contract = +20; * 50 = +1000
    assert realised == pytest.approx(20.0 * TXO_MULTIPLIER)
    assert p.realised_pnl_total == pytest.approx(20.0 * TXO_MULTIPLIER)
    assert p.positions[0].close_date == pd.Timestamp("2026-01-20")


def test_portfolio_double_close_raises(short_call_leg: OptionLeg, long_call_leg: OptionLeg) -> None:
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    chain = pd.DataFrame(
        [
            _chain_row(17000, "call", 40.0, 39.0, 41.0),
            _chain_row(17200, "call", 10.0, 9.0, 11.0),
        ]
    )
    p.close(0, chain)
    with pytest.raises(ValueError, match="already closed"):
        p.close(0, chain)


def test_portfolio_mark_to_market_open_position(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """Unrealised reflects (mid - entry) per leg, signed by qty."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    chain = pd.DataFrame(
        [
            _chain_row(17000, "call", 80.0, 79.0, 81.0),
            _chain_row(17200, "call", 30.0, 29.0, 31.0),
        ]
    )
    # short: -1 * (80 - 100) = +20
    # long:  +1 * (30 - 50)  = -20
    # net per contract = 0; * 50 = 0
    unrealised = p.mark_to_market(chain)
    assert unrealised == pytest.approx(0.0)


def test_portfolio_mark_to_market_excludes_closed(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    chain = pd.DataFrame(
        [
            _chain_row(17000, "call", 40.0, 39.0, 41.0),
            _chain_row(17200, "call", 10.0, 9.0, 11.0),
        ]
    )
    p.close(0, chain)
    # No open positions → unrealised should be 0.
    assert p.mark_to_market(chain) == pytest.approx(0.0)


def test_portfolio_aggregate_greeks_returns_4_keys(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    chain = pd.DataFrame(
        [
            _chain_row(17000, "call", 80.0, 79.0, 81.0),
            _chain_row(17200, "call", 30.0, 29.0, 31.0),
        ]
    )
    greeks = p.aggregate_greeks(chain)
    assert set(greeks.keys()) == {"delta", "gamma", "theta", "vega"}
    # All numeric
    for v in greeks.values():
        assert isinstance(v, float)


def test_portfolio_aggregate_greeks_empty_when_no_positions() -> None:
    p = Portfolio(initial_capital=1_000_000.0)
    chain = pd.DataFrame([_chain_row(17000, "call", 80.0, 79.0, 81.0)])
    greeks = p.aggregate_greeks(chain)
    assert greeks == {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}


def test_mark_to_market_strict_raises_on_missing_quote(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """R6 F1: mark_to_market(strict=True) must raise on missing leg quote
    before expiry, not silently skip. Otherwise daily PnL / risk gate
    underestimate drawdown."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    # Chain on 2026-01-20 has the long leg (17200) but missing short leg (17000).
    chain_missing_short = pd.DataFrame(
        [_chain_row(17200, "call", 30.0, 29.0, 31.0)]  # only long leg present
    )
    with pytest.raises(ValueError, match="mark_to_market.*not yet expired"):
        p.mark_to_market(chain_missing_short)


def test_mark_to_market_lenient_skips_missing_quote(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """strict=False keeps legacy behaviour for callers that pre-substitute."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    chain_missing_short = pd.DataFrame([_chain_row(17200, "call", 30.0, 29.0, 31.0)])
    # Should not raise; only long leg contributes.
    val = p.mark_to_market(chain_missing_short, strict=False)
    expected = 1 * (30.0 - 50.0) * TXO_MULTIPLIER  # long leg only
    assert val == pytest.approx(expected)


# =====================================================================
# R10.10 _mid_price NaN raise tests (Codex P1-1 + decision 1A + 4A, 2026-04-28)
# Per OptionMetrics IvyDB-US institutional standard:
#   mark = midpoint only; missing bid OR ask → raise (no fallback to settle).
# Was R5 P1 leak via _mid_price fallback path.
# =====================================================================


def _chain_row_with_nan(
    strike: int,
    option_type: str,
    settle: float,
    bid: float | None,
    ask: float | None,
) -> dict:
    """Helper: chain row with optional NaN bid/ask."""
    import numpy as np

    return {
        "date": pd.Timestamp("2026-01-20"),
        "expiry": pd.Timestamp("2026-02-19"),
        "strike": strike,
        "option_type": option_type,
        "settle": settle,
        "bid": np.nan if bid is None else bid,
        "ask": np.nan if ask is None else ask,
        "iv": 0.20,
        "delta": 0.16,
        "underlying": 17000.0,
    }


def test_mid_price_raises_on_missing_bid(short_call_leg: OptionLeg) -> None:
    """R10.10 1A: _mid_price raise on NaN bid (was: silent fallback to settle)."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 100.0, bid=None, ask=110.0)])
    with pytest.raises(ValueError, match="unmarkable.*missing bid"):
        p.mark_to_market(chain)


def test_mid_price_raises_on_missing_ask(short_call_leg: OptionLeg) -> None:
    """R10.10 1A: _mid_price raise on NaN ask."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 100.0, bid=90.0, ask=None)])
    with pytest.raises(ValueError, match="unmarkable.*missing bid"):
        p.mark_to_market(chain)


def test_mid_price_raises_on_missing_both(short_call_leg: OptionLeg) -> None:
    """R10.10 1A: NaN both bid and ask → raise (no settle fallback even though settle exists)."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 100.0, bid=None, ask=None)])
    with pytest.raises(ValueError, match="unmarkable"):
        p.mark_to_market(chain)


def test_mid_price_works_with_both_bid_ask(short_call_leg: OptionLeg) -> None:
    """R10.10 1A: 既有 bid AND ask → mid; backwards compatible."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 123.0, bid=90.0, ask=110.0)])
    val = p.mark_to_market(chain)
    # mid = 100; entry = 100; qty=-1 → unrealised = -1 * (100 - 100) * 50 = 0
    assert val == pytest.approx(0.0)
    # 確認用 mid 不是 settle (123): 若 settle 會是 -1 * (123 - 100) * 50 = -1150
    assert val != pytest.approx(-1150.0)


# =====================================================================
# R10.11 hybrid mark_policy tests (Codex 抓 60% TXO unmarkable, 2026-04-28)
# Pro 量化 institutional 1B/3 standard: scripts 端 explicit 選 fallback
# 並標 audit flag; default 仍 strict (R10.10 1A) 守 unit test + synthetic.
# =====================================================================


def test_mid_price_with_fallback_returns_settle_when_strict_false() -> None:
    """R10.11: _mid_price(row, strict=False) → fallback settle (no raise)."""
    import numpy as np

    from src.backtest.portfolio import _mid_price

    row = pd.Series(
        {
            "strike": 17000,
            "option_type": "call",
            "settle": 123.0,
            "bid": np.nan,
            "ask": 110.0,
            "expiry": pd.Timestamp("2026-02-19"),
            "date": pd.Timestamp("2026-01-20"),
        }
    )
    val = _mid_price(row, strict=False)
    assert val == pytest.approx(123.0)


def test_mid_price_with_basis_returns_tuple() -> None:
    """R10.11 audit: _mid_price_with_basis returns (price, basis_flag)."""
    import numpy as np

    from src.backtest.portfolio import _mid_price_with_basis

    row_mid = pd.Series(
        {
            "strike": 17000,
            "option_type": "call",
            "settle": 123.0,
            "bid": 90.0,
            "ask": 110.0,
            "expiry": pd.Timestamp("2026-02-19"),
            "date": pd.Timestamp("2026-01-20"),
        }
    )
    price, basis = _mid_price_with_basis(row_mid)
    assert price == pytest.approx(100.0) and basis == "mid"

    row_nan = pd.Series(
        {
            "strike": 17000,
            "option_type": "call",
            "settle": 123.0,
            "bid": np.nan,
            "ask": np.nan,
            "expiry": pd.Timestamp("2026-02-19"),
            "date": pd.Timestamp("2026-01-20"),
        }
    )
    price2, basis2 = _mid_price_with_basis(row_nan, fallback_mode="settle")
    assert price2 == pytest.approx(123.0) and basis2 == "settle_fallback"


def test_mark_to_market_strict_mid_default_raises_on_missing_bid(
    short_call_leg: OptionLeg,
) -> None:
    """R10.11: default mark_policy='strict_mid' = Phase A R10.10 行為."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 123.0, bid=None, ask=110.0)])
    with pytest.raises(ValueError, match="unmarkable"):
        p.mark_to_market(chain)  # default mark_policy='strict_mid'


def test_mark_to_market_fallback_uses_settle_with_audit_metric(
    short_call_leg: OptionLeg,
) -> None:
    """R10.11 institutional 1B/3: mark_policy='mid_with_settle_fallback'
    → fallback settle + audit metric records fallback rate."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    # Row with NaN bid → fallback to settle 123
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 123.0, bid=None, ask=110.0)])
    val = p.mark_to_market(chain, mark_policy="mid_with_settle_fallback")
    # Used settle 123: -1 * (123 - 100) * 50 = -1150 (vs entry 100)
    assert val == pytest.approx(-1150.0)
    # Audit metric recorded
    assert p.last_mark_fallback_rate == pytest.approx(1.0)  # 1/1 leg fell back
    assert p.last_mark_n_fallback_settle == 1
    assert p.last_mark_n_legs_marked == 1


def test_mark_to_market_fallback_uses_mid_when_bid_ask_present(
    short_call_leg: OptionLeg,
) -> None:
    """R10.11: mark_policy='mid_with_settle_fallback' 仍 prefer mid 當 OK."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 123.0, bid=90.0, ask=110.0)])
    val = p.mark_to_market(chain, mark_policy="mid_with_settle_fallback")
    # mid = 100, fall back path NOT taken
    assert val == pytest.approx(0.0)
    assert p.last_mark_fallback_rate == pytest.approx(0.0)
    assert p.last_mark_n_fallback_settle == 0


def test_mark_to_market_invalid_policy_raises(short_call_leg: OptionLeg) -> None:
    """R10.11: invalid mark_policy raises with clear message."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame([_chain_row_with_nan(17000, "call", 123.0, bid=90.0, ask=110.0)])
    with pytest.raises(ValueError, match="mark_policy must be"):
        p.mark_to_market(chain, mark_policy="forward_fill")


# =====================================================================
# R10.13 C2 (Codex caveat): R10.12 b/c regression tests
# 守 settle finite guard + aggregate_greeks NaN guard 不回歸
# =====================================================================


def test_mid_price_settle_nan_fallback_raises(short_call_leg: OptionLeg) -> None:
    """R10.12 修法 b: bid/ask AND settle 全 NaN → fallback path 必 raise (不 silent NaN)."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 17000,
                "option_type": "call",
                "settle": np.nan,
                "bid": np.nan,
                "ask": np.nan,
                "iv": 0.20,
                "delta": 0.16,
                "underlying": 17000.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="unmarkable\\+unfallbackable|all missing"):
        p.mark_to_market(chain, mark_policy="mid_with_settle_fallback")


def test_mid_price_settle_inf_raises(short_call_leg: OptionLeg) -> None:
    """R10.12 修法 b: settle is inf → fallback raise (finite guard)."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 17000,
                "option_type": "call",
                "settle": np.inf,
                "bid": np.nan,
                "ask": np.nan,
                "iv": 0.20,
                "delta": 0.16,
                "underlying": 17000.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="non-finite"):
        p.mark_to_market(chain, mark_policy="mid_with_settle_fallback")


def test_aggregate_greeks_iv_nan_raises(short_call_leg: OptionLeg) -> None:
    """R10.12 修法 c: iv NaN → aggregate_greeks raise in strict mode (Pattern 5 sibling sweep)."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 17000,
                "option_type": "call",
                "settle": 100.0,
                "bid": 90.0,
                "ask": 110.0,
                "iv": np.nan,
                "delta": 0.16,
                "underlying": 17000.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="aggregate_greeks.*iv is NaN"):
        p.aggregate_greeks(chain)  # default strict=True


def test_aggregate_greeks_delta_nan_raises(short_call_leg: OptionLeg) -> None:
    """R10.12 修法 c: delta NaN → aggregate_greeks raise in strict mode."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 17000,
                "option_type": "call",
                "settle": 100.0,
                "bid": 90.0,
                "ask": 110.0,
                "iv": 0.20,
                "delta": np.nan,
                "underlying": 17000.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="aggregate_greeks.*delta is NaN"):
        p.aggregate_greeks(chain)


def test_aggregate_greeks_iv_nan_lenient_skips(short_call_leg: OptionLeg) -> None:
    """R10.12 修法 c: strict=False 對 iv NaN 應 skip 不 raise (legacy behaviour)."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="naked_short")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 17000,
                "option_type": "call",
                "settle": 100.0,
                "bid": 90.0,
                "ask": 110.0,
                "iv": np.nan,
                "delta": 0.16,
                "underlying": 17000.0,
            }
        ]
    )
    result = p.aggregate_greeks(chain, strict=False)
    # Skipped → all greeks 0
    assert result == {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}


def test_mark_to_market_expired_leg_uses_payoff(
    short_call_leg: OptionLeg, long_call_leg: OptionLeg
) -> None:
    """Leg with today >= expiry uses intrinsic payoff regardless of strict mode."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg, long_call_leg], strategy_name="VC")
    # Chain on 2026-02-19 (= short_call_leg.expiry) with no leg rows; spot 17050.
    expired_chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-02-19"),
                "expiry": pd.Timestamp("2026-02-19"),
                "strike": 99999,  # unrelated dummy row, just to expose underlying
                "option_type": "call",
                "settle": 0.0,
                "bid": 0.0,
                "ask": 0.0,
                "iv": 0.20,
                "delta": 0.0,
                "underlying": 17050.0,
            }
        ]
    )
    val = p.mark_to_market(expired_chain)  # strict=True, but payoff path
    # short call (K=17000): payoff=max(17050-17000,0)=50; qty=-1; -1*(50-100)=+50 per
    # long  call (K=17200): payoff=max(17050-17200,0)=0;  qty=+1; +1*(0-50)=-50 per
    # net per contract = 0; total = 0
    assert val == pytest.approx(0.0)


# ===========================================================================
# Week 5 Day 5.2 — mark_policy='mid_with_surface_fallback' (vol surface mark)
# ===========================================================================


def _chain_row_with_model_price(
    strike: float = 17000,
    *,
    bid: float | None = None,
    ask: float | None = None,
    settle: float | None = 123.0,
    model_price: float | None = None,
) -> pd.Series:
    """Helper: build chain row with optional bid/ask/settle/model_price."""
    return pd.Series(
        {
            "strike": strike,
            "option_type": "call",
            "settle": settle,
            "bid": bid,
            "ask": ask,
            "model_price": model_price,
            "expiry": pd.Timestamp("2026-02-19"),
            "date": pd.Timestamp("2026-01-20"),
        }
    )


def test_mid_price_with_basis_surface_mode_uses_model_price() -> None:
    """fallback_mode='surface': bid/ask 缺 + model_price 存 → 用 model_price (basis='surface_fallback')."""
    from src.backtest.portfolio import _mid_price_with_basis

    row = _chain_row_with_model_price(model_price=99.5)
    price, basis = _mid_price_with_basis(row, fallback_mode="surface")
    assert price == pytest.approx(99.5)
    assert basis == "surface_fallback"


def test_mid_price_with_basis_surface_mode_3rd_layer_settle_r12_4() -> None:
    """R12.4 P fix (Codex audit): fallback_mode='surface' now does 3-layer fallback
    mid → surface → settle → raise. NaN model_price + valid settle → settle_3rd_fallback.
    """
    import numpy as np

    from src.backtest.portfolio import _mid_price_with_basis

    # NaN model_price + valid settle → 3rd layer settle
    row = _chain_row_with_model_price(model_price=np.nan)
    price, basis = _mid_price_with_basis(row, fallback_mode="surface")
    assert basis == "settle_3rd_fallback"
    # Settle from _chain_row_with_model_price helper
    assert price == pytest.approx(row["settle"])


def test_mid_price_with_basis_surface_mode_truly_unmarkable_raises_r12_4() -> None:
    """R12.4 P fix: only raise when bid/ask AND model_price AND settle ALL missing."""
    import numpy as np

    from src.backtest.portfolio import _mid_price_with_basis

    row = _chain_row_with_model_price(model_price=np.nan)
    row["settle"] = np.nan  # destroy 3rd layer too
    with pytest.raises(ValueError, match="truly_unmarkable"):
        _mid_price_with_basis(row, fallback_mode="surface")


def test_mark_to_market_surface_fallback_happy_path(
    short_call_leg: OptionLeg,
) -> None:
    """mark_policy='mid_with_surface_fallback': bid/ask 缺，model_price 填 → 用 surface mark."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="day5.2_test")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": float("nan"),
                "ask": float("nan"),
                "settle": 88.0,  # would be used by settle policy; 不該被 surface policy 讀
                "model_price": 95.0,
                "underlying": 17050.0,
            }
        ]
    )
    val = p.mark_to_market(chain, mark_policy="mid_with_surface_fallback")
    # short call: qty=-1; mark @ model_price=95 vs entry=100 → -1*(95-100)*50 = +250
    assert val == pytest.approx(250.0)
    # n_fallback_surface 計數正確
    assert p.last_mark_n_legs_marked == 1
    assert p.last_mark_n_fallback_surface == 1
    assert p.last_mark_n_fallback_settle == 0
    assert p.last_mark_fallback_rate == pytest.approx(1.0)


def test_mark_to_market_surface_fallback_3rd_layer_settle_r12_4(
    short_call_leg: OptionLeg,
) -> None:
    """R12.4 P fix (Codex audit): mid_with_surface_fallback now 3-layer:
    bid/ask 缺 + model_price 缺 + settle 有 → settle_3rd_fallback (mark @ settle, no raise).

    Replaces old test that expected raise on NaN model_price; institutional-grade
    gate now defers to settle (Pro convention for far-OTM worthless strikes
    where SVI/SABR fit fails — settle=0 is correct mark).
    """
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="day5.2_r12_4_test")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": float("nan"),
                "ask": float("nan"),
                "settle": 88.0,
                "model_price": np.nan,  # cache miss → 3rd layer
                "underlying": 17050.0,
            }
        ]
    )
    val = p.mark_to_market(chain, mark_policy="mid_with_surface_fallback")
    # short call qty=-1 mark @ settle=88 vs entry=100 → -1*(88-100)*50 = +600
    assert val == pytest.approx(600.0)
    # Counted as settle fallback in audit (R12.4 backward-compat: aggregate w/ 2-tier settle)
    assert p.last_mark_n_legs_marked == 1
    assert p.last_mark_n_fallback_settle == 1
    assert p.last_mark_n_fallback_surface == 0


def test_mark_to_market_surface_fallback_truly_unmarkable_raises_r12_4(
    short_call_leg: OptionLeg,
) -> None:
    """R12.4 P fix: only raise when bid/ask AND model_price AND settle ALL missing."""
    import numpy as np

    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="day5.2_r12_4_test")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": float("nan"),
                "ask": float("nan"),
                "settle": np.nan,  # destroy 3rd layer
                "model_price": np.nan,
                "underlying": 17050.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="truly_unmarkable"):
        p.mark_to_market(chain, mark_policy="mid_with_surface_fallback")


def test_mark_to_market_surface_policy_uses_mid_when_bid_ask_present(
    short_call_leg: OptionLeg,
) -> None:
    """mark_policy='mid_with_surface_fallback': bid/ask 完整 → 用 mid，不退 surface (n_fallback_surface=0)."""
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="day5.2_test")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": 90.0,
                "ask": 110.0,
                "settle": 88.0,
                "model_price": 200.0,  # 故意離譜；若被讀就 detect
                "underlying": 17050.0,
            }
        ]
    )
    val = p.mark_to_market(chain, mark_policy="mid_with_surface_fallback")
    # mid = 100 → -1*(100-100)*50 = 0；若被 model_price=200 讀就會是 -5000
    assert val == pytest.approx(0.0)
    assert p.last_mark_n_fallback_surface == 0
    assert p.last_mark_fallback_rate == pytest.approx(0.0)


def test_mark_to_market_surface_policy_audit_4_columns_backward_compat(
    short_call_leg: OptionLeg,
) -> None:
    """既有 R10.12 三欄 (fallback_rate / n_legs_marked / n_fallback_settle) 仍 OK + 新欄 n_fallback_surface 加上.

    Pattern 4 baseline second-order trap: 既有 callsite 用 getattr default 0
    不 break；engine.py mark_audit_records 4-tuple schema Day 5.3 升級.
    """
    p = Portfolio(initial_capital=1_000_000.0)
    p.open([short_call_leg], strategy_name="day5.2_test")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": 90.0,
                "ask": 110.0,
                "settle": 88.0,
                "model_price": 95.0,
                "underlying": 17050.0,
            }
        ]
    )
    p.mark_to_market(chain, mark_policy="mid_with_surface_fallback")
    # 4 attrs 都該存在
    assert hasattr(p, "last_mark_fallback_rate")
    assert hasattr(p, "last_mark_n_legs_marked")
    assert hasattr(p, "last_mark_n_fallback_settle")
    assert hasattr(p, "last_mark_n_fallback_surface")  # NEW Day 5.2
    # backward-compat: 既有 3 attr 仍正確
    assert p.last_mark_n_legs_marked == 1
    assert p.last_mark_n_fallback_settle == 0  # surface policy 不退 settle


def test_mark_to_market_invalid_mark_policy_message_includes_surface_option() -> None:
    """invalid mark_policy 錯誤訊息應列 3 種有效 option (R10.12 a sanity 升級)."""
    p = Portfolio(initial_capital=1_000_000.0)
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-20"),
                "strike": 17000,
                "option_type": "call",
                "expiry": pd.Timestamp("2026-02-19"),
                "bid": 90.0,
                "ask": 110.0,
                "settle": 88.0,
                "underlying": 17050.0,
            }
        ]
    )
    with pytest.raises(ValueError, match="mark_policy must be.*mid_with_surface_fallback"):
        p.mark_to_market(chain, mark_policy="forward_fill")
