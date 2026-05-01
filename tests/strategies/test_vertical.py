"""Tests for src/strategies/vertical.py — Week 2 Day 3 builders."""

from __future__ import annotations

import pandas as pd
import pytest

from src.strategies.vertical import build_bear_call_spread, build_bull_put_spread


@pytest.fixture
def single_day_chain() -> pd.DataFrame:
    """Synthetic single-day chain with a wide strike grid for vertical building."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    config = SyntheticChainConfig(
        start_date="2026-01-05",
        end_date="2026-01-05",
        spot_start=16800.0,
        sigma=0.20,
        n_strikes_per_side=10,
        max_dte=45,
        seed=42,
    )
    return generate_chain(config)


def test_bull_put_spread_builds_2_legs_short_higher_long_lower(
    single_day_chain: pd.DataFrame,
) -> None:
    """Bull Put: short put @ K1 (higher), long put @ K2 (lower); qty=-1 / +1."""
    legs = build_bull_put_spread(single_day_chain, short_strike=16700.0, long_strike=16500.0)
    assert len(legs) == 2
    short, long_ = legs
    assert short.option_type == "put"
    assert long_.option_type == "put"
    assert short.strike == 16700
    assert long_.strike == 16500
    assert short.qty == -1
    assert long_.qty == +1


def test_bull_put_spread_invalid_strike_order_raises(
    single_day_chain: pd.DataFrame,
) -> None:
    """Bull Put requires long_strike < short_strike."""
    with pytest.raises(ValueError, match="Bull Put"):
        build_bull_put_spread(single_day_chain, short_strike=16500.0, long_strike=16700.0)


def test_bear_call_spread_builds_2_legs_short_lower_long_higher(
    single_day_chain: pd.DataFrame,
) -> None:
    """Bear Call: short call @ K1 (lower), long call @ K2 (higher); qty=-1 / +1."""
    legs = build_bear_call_spread(single_day_chain, short_strike=16900.0, long_strike=17100.0)
    assert len(legs) == 2
    short, long_ = legs
    assert short.option_type == "call"
    assert long_.option_type == "call"
    assert short.strike == 16900
    assert long_.strike == 17100
    assert short.qty == -1
    assert long_.qty == +1


def test_bear_call_spread_invalid_strike_order_raises(
    single_day_chain: pd.DataFrame,
) -> None:
    """Bear Call requires long_strike > short_strike."""
    with pytest.raises(ValueError, match="Bear Call"):
        build_bear_call_spread(single_day_chain, short_strike=17100.0, long_strike=16900.0)


def test_vertical_missing_strike_raises(single_day_chain: pd.DataFrame) -> None:
    """Strike not in chain → ValueError ('No put option at strike ...')."""
    # short=99999 > long=16700 satisfies Bull Put rule (long < short),
    # but 99999 doesn't exist in chain → triggers the missing-strike branch.
    with pytest.raises(ValueError, match="No put option at strike"):
        build_bull_put_spread(single_day_chain, short_strike=99999.0, long_strike=16700.0)
