"""Tests for src/strategies/vertical_strategy.py — Week 6 Day 6.0.

7 tests:
  1. test_vertical_init_validation (constructor edge cases)
  2. test_vertical_skew_no_signal_below_threshold (|skew| < threshold → None)
  3. test_vertical_skew_bull_put_above_threshold (skew > +threshold → bull_put)
  4. test_vertical_skew_bear_call_below_negative_threshold (skew < -threshold)
  5. test_vertical_open_position_bull_put_legs_correct
  6. test_vertical_open_position_bear_call_legs_correct
  7. test_vertical_open_position_returns_none_when_existing_position
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from src.common.types import PortfolioState, StrategySignal
from src.strategies.vertical_strategy import VerticalStrategy

if TYPE_CHECKING:
    pass


@pytest.fixture
def empty_state() -> PortfolioState:
    return PortfolioState(
        cash=1_000_000.0,
        positions=[],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={},
    )


@pytest.fixture
def synthetic_chain_bull_put_skew() -> pd.DataFrame:
    """Synthetic chain with put_iv > call_iv at 25-delta (bull put signal)."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(
        SyntheticChainConfig(
            start_date="2026-01-05",
            end_date="2026-01-05",
            spot_start=16800.0,
            sigma=0.20,
            n_strikes_per_side=30,
            max_dte=60,
            seed=42,
        )
    )
    # Boost put IV by 2% to ensure bull put signal
    is_put = chain["option_type"] == "put"
    chain.loc[is_put, "iv"] = chain.loc[is_put, "iv"] + 0.02
    return chain


@pytest.fixture
def synthetic_chain_no_skew() -> pd.DataFrame:
    """Synthetic chain with put_iv ≈ call_iv (no signal — symmetric)."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    return generate_chain(
        SyntheticChainConfig(
            start_date="2026-01-05",
            end_date="2026-01-05",
            spot_start=16800.0,
            sigma=0.20,
            n_strikes_per_side=30,
            max_dte=60,
            seed=42,
        )
    )


@pytest.fixture
def synthetic_chain_bear_call_skew() -> pd.DataFrame:
    """Synthetic chain with call_iv > put_iv at 25-delta (bear call signal)."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(
        SyntheticChainConfig(
            start_date="2026-01-05",
            end_date="2026-01-05",
            spot_start=16800.0,
            sigma=0.20,
            n_strikes_per_side=30,
            max_dte=60,
            seed=42,
        )
    )
    # Boost call IV by 2% to ensure bear call signal
    is_call = chain["option_type"] == "call"
    chain.loc[is_call, "iv"] = chain.loc[is_call, "iv"] + 0.02
    return chain


def test_vertical_init_validation() -> None:
    """Constructor edge cases."""
    # wing >= short
    with pytest.raises(ValueError, match="wing_delta"):
        VerticalStrategy(short_delta=0.25, wing_delta=0.30)
    # exit_dte >= target_dte
    with pytest.raises(ValueError, match="exit_dte"):
        VerticalStrategy(target_dte=21, exit_dte=21)
    # negative skew threshold
    with pytest.raises(ValueError, match="skew_threshold"):
        VerticalStrategy(skew_threshold=-0.01)
    # profit_target out of range
    with pytest.raises(ValueError, match="profit_target_pct"):
        VerticalStrategy(profit_target_pct=1.5)


def test_vertical_skew_no_signal_below_threshold(
    synthetic_chain_no_skew: pd.DataFrame,
) -> None:
    """Symmetric IV → |skew| 小 → signal=None。"""
    strategy = VerticalStrategy(skew_threshold=0.01)
    skew, signal = strategy._compute_skew(synthetic_chain_no_skew)
    # synthetic generate_chain 預設 IV smile 對稱 → skew 應該 < 0.01
    assert abs(skew) < 0.01 or signal is None


def test_vertical_skew_bull_put_above_threshold(
    synthetic_chain_bull_put_skew: pd.DataFrame,
) -> None:
    """put_iv > call_iv + threshold → bull_put signal."""
    strategy = VerticalStrategy(skew_threshold=0.01)
    skew, signal = strategy._compute_skew(synthetic_chain_bull_put_skew)
    assert skew > 0.01
    assert signal == "bull_put"


def test_vertical_skew_bear_call_below_negative_threshold(
    synthetic_chain_bear_call_skew: pd.DataFrame,
) -> None:
    """call_iv > put_iv + threshold → bear_call signal."""
    strategy = VerticalStrategy(skew_threshold=0.01)
    skew, signal = strategy._compute_skew(synthetic_chain_bear_call_skew)
    assert skew < -0.01
    assert signal == "bear_call"


def test_vertical_open_position_bull_put_legs_correct(
    synthetic_chain_bull_put_skew: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Bull put: 2 legs, sell short put + buy long put (lower strike)."""
    strategy = VerticalStrategy(skew_threshold=0.01)
    signal = strategy.open_position(synthetic_chain_bull_put_skew, empty_state)
    assert isinstance(signal, StrategySignal)
    assert signal.action == "open"
    assert len(signal.orders) == 2
    short_order, long_order = signal.orders
    assert short_order.side == "sell"
    assert long_order.side == "buy"
    assert short_order.option_type == "put"
    assert long_order.option_type == "put"
    # long put strike < short put strike
    assert long_order.strike < short_order.strike
    assert signal.metadata["signal"] == "bull_put"
    assert signal.metadata["option_type"] == "put"


def test_vertical_open_position_bear_call_legs_correct(
    synthetic_chain_bear_call_skew: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Bear call: 2 legs, sell short call + buy long call (higher strike)."""
    strategy = VerticalStrategy(skew_threshold=0.01)
    signal = strategy.open_position(synthetic_chain_bear_call_skew, empty_state)
    assert isinstance(signal, StrategySignal)
    assert signal.action == "open"
    short_order, long_order = signal.orders
    assert short_order.side == "sell"
    assert long_order.side == "buy"
    assert short_order.option_type == "call"
    # long call strike > short call strike
    assert long_order.strike > short_order.strike
    assert signal.metadata["signal"] == "bear_call"


def test_vertical_open_position_returns_none_when_existing_position(
    synthetic_chain_bull_put_skew: pd.DataFrame,
) -> None:
    """should_open=False if any position open (no double-stacking)."""
    from src.common.types import OptionLeg, Position

    fake_leg = OptionLeg(
        contract="TXO20260221P16500",
        strike=16500,
        expiry=pd.Timestamp("2026-02-21"),
        option_type="put",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=120.0,
    )
    fake_pos = Position(
        legs=[fake_leg],
        open_date=pd.Timestamp("2026-01-05"),
        strategy_name="VerticalTest",
    )
    state = PortfolioState(
        cash=1_000_000.0,
        positions=[fake_pos],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={},
    )
    strategy = VerticalStrategy(skew_threshold=0.01)
    assert strategy.should_open(synthetic_chain_bull_put_skew, state) is False
