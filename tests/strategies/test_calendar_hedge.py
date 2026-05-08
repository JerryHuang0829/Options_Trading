"""Tests for src/strategies/calendar_hedge.py — Phase 1 Week 7 Day 7.1 Quick A.

Coverage:
  - build_long_calendar_atm_call: happy path / no back expiry / no ATM / wrong dtype
  - estimate_calendar_premium: settle-based + edge cases
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.strategies.calendar_hedge import (
    TXO_STRIKE_GRID,
    build_long_calendar_atm_call,
    estimate_calendar_premium,
)


@pytest.fixture
def chain_two_expiries() -> pd.DataFrame:
    """Synthetic 2-expiry chain with ATM strikes available in both."""
    rows = []
    today = pd.Timestamp("2026-01-05")
    front_expiry = pd.Timestamp("2026-02-19")  # ~45 DTE
    back_expiry = pd.Timestamp("2026-03-19")  # ~73 DTE (close to 45+30=75)

    for expiry in (front_expiry, back_expiry):
        for strike in [16700.0, 16750.0, 16800.0, 16850.0, 16900.0]:
            for opt in ("call", "put"):
                # Synthetic: settle = 30 + 0.1 * abs(strike - 16800); calls + puts symmetric
                settle = 30.0 + 0.1 * abs(strike - 16800.0)
                bid = settle - 1.0
                ask = settle + 1.0
                rows.append(
                    {
                        "date": today,
                        "expiry": expiry,
                        "strike": strike,
                        "option_type": opt,
                        "settle": settle,
                        "bid": bid,
                        "ask": ask,
                        "underlying": 16800.0,
                        "can_buy": True,
                        "can_sell": True,
                    }
                )
    return pd.DataFrame(rows)


def test_build_calendar_happy_path(chain_two_expiries):
    """Build returns 2 Orders: sell front + buy back ATM call."""
    front_expiry = pd.Timestamp("2026-02-19")
    orders = build_long_calendar_atm_call(
        chain_two_expiries, ic_expiry=front_expiry, underlying=16800.0
    )
    assert len(orders) == 2
    sell_front, buy_back = orders[0], orders[1]
    assert sell_front.side == "sell"
    assert sell_front.option_type == "call"
    assert sell_front.strike == 16800
    assert sell_front.expiry == front_expiry
    assert buy_back.side == "buy"
    assert buy_back.option_type == "call"
    assert buy_back.strike == 16800
    assert buy_back.expiry == pd.Timestamp("2026-03-19")


def test_build_calendar_no_back_expiry_raises(chain_two_expiries):
    """If chain only has front expiry (no back month) → raise."""
    front_only = chain_two_expiries[
        chain_two_expiries["expiry"] == pd.Timestamp("2026-02-19")
    ].copy()
    with pytest.raises(ValueError, match="no back-month expiry"):
        build_long_calendar_atm_call(
            front_only, ic_expiry=pd.Timestamp("2026-02-19"), underlying=16800.0
        )


def test_build_calendar_atm_strike_missing_in_back_raises(chain_two_expiries):
    """If back expiry has no ATM strike row → raise (chain incomplete)."""
    back_expiry = pd.Timestamp("2026-03-19")
    df = chain_two_expiries[
        ~((chain_two_expiries["expiry"] == back_expiry) & (chain_two_expiries["strike"] == 16800.0))
    ].copy()
    with pytest.raises(ValueError, match="back ATM call"):
        build_long_calendar_atm_call(df, ic_expiry=pd.Timestamp("2026-02-19"), underlying=16800.0)


def test_build_calendar_atm_strike_missing_in_front_raises(chain_two_expiries):
    """Front expiry chain missing ATM call → raise."""
    front_expiry = pd.Timestamp("2026-02-19")
    df = chain_two_expiries[
        ~(
            (chain_two_expiries["expiry"] == front_expiry)
            & (chain_two_expiries["strike"] == 16800.0)
            & (chain_two_expiries["option_type"] == "call")
        )
    ].copy()
    with pytest.raises(ValueError, match="front ATM call"):
        build_long_calendar_atm_call(df, ic_expiry=front_expiry, underlying=16800.0)


def test_build_calendar_empty_chain_raises():
    df = pd.DataFrame(columns=["date", "expiry", "strike", "option_type"])
    with pytest.raises(ValueError, match="empty chain"):
        build_long_calendar_atm_call(df, ic_expiry=pd.Timestamp("2026-02-19"), underlying=16800.0)


def test_build_calendar_ic_expiry_in_past_raises(chain_two_expiries):
    """If ic_expiry well before today (such that offset can't reach future) → raise."""
    # ic_expiry = today - 60d, offset=30 → target back_dte = -30 → cannot place
    today = pd.Timestamp("2026-01-05")
    past_ic_expiry = today - pd.Timedelta(days=60)
    with pytest.raises(ValueError, match="cannot place back leg"):
        build_long_calendar_atm_call(
            chain_two_expiries,
            ic_expiry=past_ic_expiry,
            underlying=16800.0,
            hedge_dte_offset=30,
        )


def test_build_calendar_atm_strike_rounded_to_grid(chain_two_expiries):
    """Underlying mid-grid (16825) should round to nearest 50-pt strike (16800 or 16850)."""
    # underlying=16825 → round(16825/50)*50 = round(336.5)*50 = either 336 or 337 banker's rounding
    # Python's round uses banker's: round(336.5) = 336 → 16800
    front_expiry = pd.Timestamp("2026-02-19")
    orders = build_long_calendar_atm_call(
        chain_two_expiries, ic_expiry=front_expiry, underlying=16824.0
    )
    # 16824/50 = 336.48 → round = 336 → 16800
    assert orders[0].strike == 16800


def test_build_calendar_grid_constant_is_50():
    """Sanity guard: TXO_STRIKE_GRID locked at 50.0."""
    assert TXO_STRIKE_GRID == 50.0


def test_estimate_calendar_premium_happy(chain_two_expiries):
    """Estimate returns positive value (back > front in calendar)."""
    front = pd.Timestamp("2026-02-19")
    back = pd.Timestamp("2026-03-19")
    premium = estimate_calendar_premium(
        chain_two_expiries,
        front_strike=16800.0,
        front_expiry=front,
        back_strike=16800.0,
        back_expiry=back,
        use_settle=True,
    )
    # Settle synth: both = 30.0 (ATM) → diff = 0; calendar premium ≈ 0 in synth
    # Real: back > front because longer DTE has more time value; synth equals.
    assert premium == 0.0


def test_estimate_calendar_premium_missing_returns_nan(chain_two_expiries):
    """Missing front or back row → NaN."""
    out = estimate_calendar_premium(
        chain_two_expiries,
        front_strike=99999.0,  # nonexistent
        front_expiry=pd.Timestamp("2026-02-19"),
        back_strike=16800.0,
        back_expiry=pd.Timestamp("2026-03-19"),
    )
    assert pd.isna(out)
