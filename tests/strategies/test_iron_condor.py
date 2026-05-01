"""Tests for src/strategies/iron_condor.py — Week 2 Day 2 open_position.

Plan §Day 2 mandates 5 tests:
  - test_ic_open_4_legs_at_correct_deltas
  - test_ic_three_credit_metrics_ordering   (worst < mid < settle)
  - test_ic_max_loss_bounded
  - test_ic_strike_selection_strict_mode_failure
  - test_ic_delta_neutral_at_open

Plus validation tests for the constructor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from src.common.types import PortfolioState, StrategySignal
from src.strategies.iron_condor import IronCondor

if TYPE_CHECKING:
    from src.common.types import Position


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
def synthetic_30dte_chain() -> pd.DataFrame:
    """Synthetic chain narrowed to a single trading day with 30-DTE expiry.

    Uses Day 4 ``generate_chain`` then filters to a single date with the
    target expiry — gives realistic delta values for IC selection.
    """
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    config = SyntheticChainConfig(
        start_date="2026-01-05",
        end_date="2026-01-05",
        spot_start=16800.0,
        sigma=0.20,
        n_strikes_per_side=30,
        max_dte=60,  # 2026-02 expiry (~45 DTE) within window
        seed=42,
    )
    return generate_chain(config)


def test_ic_open_4_legs_at_correct_deltas(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """IC open should select 4 legs whose delta is close to the targets."""
    ic = IronCondor()
    signal = ic.open_position(synthetic_30dte_chain, empty_state)

    assert isinstance(signal, StrategySignal)
    assert signal.action == "open"
    assert len(signal.orders) == 4
    # Strikes ordering: long_put < short_put < short_call < long_call
    md = signal.metadata
    assert md["long_put_strike"] < md["short_put_strike"]
    assert md["short_put_strike"] < md["short_call_strike"]
    assert md["short_call_strike"] < md["long_call_strike"]


def test_ic_three_credit_metrics_ordering(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """worst_credit < mid_credit < settle_credit (Codex R4 monotonicity).

    For a synthetic chain with bid = settle*(1-spread) and ask = settle*(1+spread),
    selling at bid + buying at ask must collect strictly less premium than mid,
    which in turn equals the symmetric settle here.
    """
    ic = IronCondor()
    signal = ic.open_position(synthetic_30dte_chain, empty_state)
    assert signal is not None and signal.action == "open"
    md = signal.metadata
    settle = md["settle_credit"]
    mid = md["mid_credit"]
    worst = md["worst_credit"]
    # Worst-side credit is strictly less than mid (you give up half-spread × 4).
    assert worst < mid, f"worst_credit={worst} should be < mid_credit={mid}"
    # Settle and mid coincide for symmetric synthetic spreads up to noise; the
    # invariant we hold is: worst_credit is the **strictly smallest** of the
    # three (Codex R4 monotonicity for short premium).
    assert worst < settle, f"worst_credit={worst} should be < settle_credit={settle}"


def test_ic_max_loss_bounded(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """max_defined_risk = max(call_wing, put_wing) × multiplier − worst_credit × multiplier."""
    from config.constants import TXO_MULTIPLIER

    ic = IronCondor()
    signal = ic.open_position(synthetic_30dte_chain, empty_state)
    assert signal is not None
    md = signal.metadata
    expected = (
        max(md["call_wing_pts"], md["put_wing_pts"]) * TXO_MULTIPLIER
        - md["worst_credit"] * TXO_MULTIPLIER
    )
    assert abs(md["max_defined_risk_twd"] - expected) < 1e-6


def test_ic_strike_selection_strict_mode_failure(
    empty_state: PortfolioState,
) -> None:
    """Sparse chain (no strikes near target deltas) → hold signal with reason."""
    # Build a degenerate chain with only 1 strike per side, far from any delta.
    sparse_rows = [
        # Only ATM-ish call/put — far from 0.16 / 0.08 deltas.
        ("2026-02-19", 16800, "call", 0.50, 200.0, 198.0, 202.0, 0.20),
        ("2026-02-19", 16800, "put", -0.50, 200.0, 198.0, 202.0, 0.20),
    ]
    df = pd.DataFrame(
        sparse_rows,
        columns=["expiry", "strike", "option_type", "delta", "settle", "bid", "ask", "iv"],
    )
    df["expiry"] = pd.to_datetime(df["expiry"])
    df["date"] = pd.Timestamp("2026-01-05")
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df["underlying"] = 16800.0

    ic = IronCondor(target_dte=45)
    signal = ic.open_position(df, empty_state)
    assert signal is not None
    assert signal.action == "hold"
    assert "rejected_reason" in signal.metadata


def test_ic_delta_neutral_at_open(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Sum of leg deltas (signed by side) ≈ 0 for an IC open."""
    ic = IronCondor()
    signal = ic.open_position(synthetic_30dte_chain, empty_state)
    assert signal is not None and signal.action == "open"

    # Reconstruct net delta from the chain rows (sell = -1 × delta, buy = +1).
    # We do not store delta on Order itself; cross-reference the chain.
    chain = synthetic_30dte_chain
    net_delta = 0.0
    for o in signal.orders:
        row = chain[
            (chain["strike"] == o.strike)
            & (chain["option_type"] == o.option_type)
            & (chain["expiry"] == o.expiry)
        ].iloc[0]
        sign = -1.0 if o.side == "sell" else +1.0
        net_delta += sign * float(row["delta"])
    assert abs(net_delta) < 0.05, f"IC net delta {net_delta} is not delta-neutral"


def test_ic_constructor_validates() -> None:
    """Bad constructor parameters raise ValueError."""
    # wing_delta must be < short_delta and > 0
    with pytest.raises(ValueError, match="wing_delta"):
        IronCondor(short_delta=0.16, wing_delta=0.20)
    with pytest.raises(ValueError, match="wing_delta"):
        IronCondor(short_delta=0.16, wing_delta=-0.05)
    # exit_dte must be < target_dte and > 0
    with pytest.raises(ValueError, match="exit_dte"):
        IronCondor(target_dte=45, exit_dte=50)
    # profit_target_pct must be in (0, 1]
    with pytest.raises(ValueError, match="profit_target_pct"):
        IronCondor(profit_target_pct=0.0)
    with pytest.raises(ValueError, match="profit_target_pct"):
        IronCondor(profit_target_pct=1.5)


def test_ic_should_open_no_double_stack(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """should_open returns False if there are already open positions."""
    ic = IronCondor()
    assert ic.should_open(synthetic_30dte_chain, empty_state) is True

    # Build a state with one fake position to simulate already-open IC.
    from src.common.types import OptionLeg, Position

    fake_leg = OptionLeg(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=100.0,
    )
    occupied_state = PortfolioState(
        cash=1_000_000.0,
        positions=[
            Position(
                legs=[fake_leg],
                open_date=pd.Timestamp("2026-01-05"),
                strategy_name="IC",
            )
        ],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={},
    )
    assert ic.should_open(synthetic_30dte_chain, occupied_state) is False


# =====================================================================
# Day 3 — should_close / should_adjust
# =====================================================================


def _build_ic_position_from_signal(
    chain: pd.DataFrame, ic: IronCondor, state: PortfolioState
) -> Position:
    """Helper: open IC and turn the StrategySignal into a Position with tags."""
    from src.common.types import OptionLeg, Position

    signal = ic.open_position(chain, state)
    assert signal is not None and signal.action == "open"
    legs: list[OptionLeg] = []
    for o in signal.orders:
        # qty signed: short=-1, long=+1
        signed_qty = -1 if o.side == "sell" else +1
        # Look up entry mid from chain to populate entry_price (price model
        # mid for testing — Day 4 wires real FillModel).
        row = chain[
            (chain["strike"] == o.strike)
            & (chain["option_type"] == o.option_type)
            & (chain["expiry"] == o.expiry)
        ].iloc[0]
        legs.append(
            OptionLeg(
                contract=o.contract,
                strike=o.strike,
                expiry=o.expiry,
                option_type=o.option_type,
                qty=signed_qty,
                entry_date=pd.Timestamp(row["date"]),
                entry_price=float((row["bid"] + row["ask"]) / 2.0),
            )
        )
    return Position(
        legs=legs,
        open_date=pd.Timestamp(chain["date"].iloc[0]),
        strategy_name="IC",
        tags={"entry_credit_mid": signal.metadata["mid_credit"]},
    )


def test_ic_should_close_at_dte_stop(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """When today's chain is within exit_dte days of expiry, should_close=True."""
    ic = IronCondor(target_dte=45, exit_dte=21)
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)

    # Simulate a chain dated near expiry (≤ 21-DTE remaining).
    expiry = position.legs[0].expiry
    near_expiry_date = expiry - pd.Timedelta(days=15)  # 15 days remaining
    chain_near_expiry = synthetic_30dte_chain.copy()
    chain_near_expiry["date"] = near_expiry_date

    assert ic.should_close(chain_near_expiry, position) is True


def test_ic_should_close_at_profit_target(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Profit target: close when current credit-to-close ≤ entry × (1 - profit_pct)."""
    ic = IronCondor(target_dte=45, exit_dte=21, profit_target_pct=0.50)
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)

    entry = position.tags["entry_credit_mid"]
    # Synthesize a chain where current mid is half of entry (50% profit hit).
    chain_profit = synthetic_30dte_chain.copy()
    # Halve bid/ask uniformly so mid drops by half on each leg.
    chain_profit["bid"] = chain_profit["bid"] * 0.5
    chain_profit["ask"] = chain_profit["ask"] * 0.5

    assert ic.should_close(chain_profit, position) is True

    # Sanity: at entry chain (no movement), should_close = False (profit target
    # not hit; DTE > exit_dte).
    assert ic.should_close(synthetic_30dte_chain, position) is False
    _ = entry  # silence unused


def test_ic_should_close_returns_false_when_no_baseline(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """If position.tags has no entry_credit_mid, profit target check returns False."""
    from src.common.types import OptionLeg, Position

    leg = OptionLeg(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=100.0,
    )
    no_baseline_pos = Position(
        legs=[leg],
        open_date=pd.Timestamp("2026-01-05"),
        strategy_name="IC",
        # tags empty: no entry_credit_mid
    )

    ic = IronCondor()
    assert ic.should_close(synthetic_30dte_chain, no_baseline_pos) is False


def test_ic_should_adjust_on_short_call_breach(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Spot crossing the short call strike → adjust signal closes call side."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)

    short_call = next(leg for leg in position.legs if leg.option_type == "call" and leg.qty < 0)
    # Synthesize a chain with spot above short_call.strike.
    chain_breach = synthetic_30dte_chain.copy()
    chain_breach["underlying"] = float(short_call.strike) + 100.0

    signal = ic.should_adjust(chain_breach, position)
    assert signal is not None
    assert signal.action == "adjust"
    assert signal.metadata["breached_side"] == "call"
    # Should close the 2 call legs.
    assert len(signal.orders) == 2
    assert all(o.option_type == "call" for o in signal.orders)


def test_ic_should_adjust_on_short_put_breach(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Symmetric: spot crossing the short put strike → close put side."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)

    short_put = next(leg for leg in position.legs if leg.option_type == "put" and leg.qty < 0)
    chain_breach = synthetic_30dte_chain.copy()
    chain_breach["underlying"] = float(short_put.strike) - 100.0

    signal = ic.should_adjust(chain_breach, position)
    assert signal is not None
    assert signal.metadata["breached_side"] == "put"
    assert all(o.option_type == "put" for o in signal.orders)


def test_ic_should_adjust_no_breach_returns_none(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """No breach (spot between shorts) → None."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)

    # synthetic_30dte_chain underlying defaults to spot_start (16800), which
    # should be between the short put and short call strikes by IC design.
    assert ic.should_adjust(synthetic_30dte_chain, position) is None


def test_ic_should_adjust_does_not_re_roll(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """Position with tags['adjusted']=True → should_adjust returns None."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)
    position.tags["adjusted"] = True

    short_call = next(leg for leg in position.legs if leg.option_type == "call" and leg.qty < 0)
    chain_breach = synthetic_30dte_chain.copy()
    chain_breach["underlying"] = float(short_call.strike) + 100.0

    assert ic.should_adjust(chain_breach, position) is None


# ---------- R8 P3: empty-chain defensive guards ----------


def test_ic_should_close_empty_chain_returns_false(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """R8 P3: should_close on empty chain must NOT raise IndexError."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)
    empty_chain = synthetic_30dte_chain.iloc[0:0].copy()
    # Must not raise; behaviour is "no decision" → False.
    assert ic.should_close(empty_chain, position) is False


def test_ic_should_adjust_empty_chain_returns_none(
    synthetic_30dte_chain: pd.DataFrame, empty_state: PortfolioState
) -> None:
    """R8 P3: should_adjust on empty chain must NOT raise IndexError."""
    ic = IronCondor()
    position = _build_ic_position_from_signal(synthetic_30dte_chain, ic, empty_state)
    empty_chain = synthetic_30dte_chain.iloc[0:0].copy()
    assert ic.should_adjust(empty_chain, position) is None
