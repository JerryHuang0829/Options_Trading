"""Tests for scripts/_gated_vertical.py — Week 6 Day 6.0.

5 tests:
  1. test_gated_vertical_passthrough_when_can_buy_can_sell_present
  2. test_gated_vertical_rejects_open_when_short_leg_can_sell_false
  3. test_gated_vertical_rejects_open_when_long_leg_can_buy_false
  4. test_gated_vertical_should_close_defers_when_close_side_blocked
  5. test_gated_vertical_inherits_skew_signal_from_base
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from scripts._gated_vertical import GatedVerticalStrategy
from src.common.types import PortfolioState

if TYPE_CHECKING:
    pass


@pytest.fixture
def empty_state() -> PortfolioState:
    return PortfolioState(
        cash=1_000_000.0, positions=[], realised_pnl=0.0, unrealised_pnl=0.0, aggregate_greeks={}
    )


@pytest.fixture
def synthetic_chain_bull_put() -> pd.DataFrame:
    """Synthetic chain with put_iv > call_iv (bull put signal) + can_buy/can_sell."""
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
    is_put = chain["option_type"] == "put"
    chain.loc[is_put, "iv"] = chain.loc[is_put, "iv"] + 0.02
    # Synthetic chain 100% bid/ask → can_buy/can_sell 全 True
    chain["can_buy"] = chain["ask"].notna()
    chain["can_sell"] = chain["bid"].notna()
    return chain


def test_gated_vertical_passthrough_when_can_buy_can_sell_present(
    synthetic_chain_bull_put: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """所有 leg can_buy/can_sell=True → signal 不變 pass-through."""
    strategy = GatedVerticalStrategy(skew_threshold=0.01)
    signal = strategy.open_position(synthetic_chain_bull_put, empty_state)
    assert signal is not None
    assert signal.action == "open"
    assert "rejected_reason" not in signal.metadata


def test_gated_vertical_rejects_open_when_short_leg_can_sell_false(
    synthetic_chain_bull_put: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Short put can_sell=False → reject + rejected_reason."""
    strategy = GatedVerticalStrategy(skew_threshold=0.01)
    # 先找 short_put 對應 strike
    raw_signal = strategy.open_position(synthetic_chain_bull_put, empty_state)
    assert raw_signal is not None and raw_signal.action == "open"
    short_strike = raw_signal.orders[0].strike
    # mask 該 strike 的 can_sell
    chain = synthetic_chain_bull_put.copy()
    chain.loc[(chain["strike"] == short_strike) & (chain["option_type"] == "put"), "can_sell"] = (
        False
    )
    signal = strategy.open_position(chain, empty_state)
    assert signal is not None
    assert signal.action == "hold"
    assert "execution_gate_fail" in signal.metadata["rejected_reason"]


def test_gated_vertical_rejects_open_when_long_leg_can_buy_false(
    synthetic_chain_bull_put: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Long put can_buy=False → reject."""
    strategy = GatedVerticalStrategy(skew_threshold=0.01)
    raw = strategy.open_position(synthetic_chain_bull_put, empty_state)
    assert raw is not None
    long_strike = raw.orders[1].strike
    chain = synthetic_chain_bull_put.copy()
    chain.loc[(chain["strike"] == long_strike) & (chain["option_type"] == "put"), "can_buy"] = False
    signal = strategy.open_position(chain, empty_state)
    assert signal is not None
    assert signal.action == "hold"


def test_gated_vertical_should_close_defers_when_close_side_blocked(
    synthetic_chain_bull_put: pd.DataFrame,
) -> None:
    """Short leg close = buy back; can_buy=False → should_close=False (defer)."""
    from src.common.types import OptionLeg, Position

    strategy = GatedVerticalStrategy(skew_threshold=0.01, exit_dte=21, target_dte=45)
    # Build a fake short put position about to expire (DTE < exit_dte triggers close)
    # 先在 chain 找一個合法 put strike 配對 expiry
    put_rows = synthetic_chain_bull_put[synthetic_chain_bull_put["option_type"] == "put"]
    short_row = put_rows.iloc[len(put_rows) // 2]  # 中間 strike
    leg = OptionLeg(
        contract=f"TXO20260221P{int(short_row['strike'])}",
        strike=int(short_row["strike"]),
        expiry=pd.Timestamp(short_row["expiry"]),
        option_type="put",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=120.0,
    )
    position = Position(
        legs=[leg], open_date=pd.Timestamp("2026-01-05"), strategy_name="VerticalGatedTest"
    )
    # Ensure DTE close trigger (chain date close to expiry)
    # mask close-side (buy back → can_buy 缺) on this strike
    chain = synthetic_chain_bull_put.copy()
    chain.loc[(chain["strike"] == leg.strike) & (chain["option_type"] == "put"), "can_buy"] = False
    # Use a chain date 4 days before expiry (within exit_dte=21 trigger)
    chain.loc[:, "date"] = pd.Timestamp(leg.expiry) - pd.Timedelta(days=4)
    # base.should_close checks DTE → True; gate 阻 close
    assert strategy.should_close(chain, position) is False


def test_gated_vertical_inherits_skew_signal_from_base(
    synthetic_chain_bull_put: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Gated 仍走 base.VerticalStrategy 的 skew gate (signal 在 metadata)."""
    strategy = GatedVerticalStrategy(skew_threshold=0.01)
    signal = strategy.open_position(synthetic_chain_bull_put, empty_state)
    assert signal is not None
    assert signal.metadata["signal"] == "bull_put"
    assert signal.metadata["option_type"] == "put"
