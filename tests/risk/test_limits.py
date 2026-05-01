"""Tests for src/risk/limits.py — Week 2 Day 4."""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.types import (
    OptionLeg,
    Order,
    PortfolioState,
    Position,
    RiskConfig,
    StrategySignal,
)
from src.risk.limits import check_risk, trigger_stop_loss


@pytest.fixture
def base_config() -> RiskConfig:
    return RiskConfig(
        max_loss_per_trade_twd=20_000.0,
        max_capital_at_risk_twd=80_000.0,
        max_concurrent_positions=3,
        stop_loss_multiple=2.0,
        portfolio_loss_cap_pct=0.05,
    )


@pytest.fixture
def empty_state() -> PortfolioState:
    return PortfolioState(
        cash=1_000_000.0,
        positions=[],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        initial_capital=1_000_000.0,
        aggregate_greeks={},
    )


def _make_open_signal(max_defined_risk_twd: float) -> StrategySignal:
    """Helper: minimal open signal carrying max_defined_risk_twd metadata."""
    o = Order(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        side="sell",
        qty=1,
    )
    return StrategySignal(
        action="open",
        orders=[o],
        metadata={"max_defined_risk_twd": max_defined_risk_twd},
    )


def _make_position(max_defined_risk_twd: float) -> Position:
    leg = OptionLeg(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=100.0,
    )
    return Position(
        legs=[leg],
        open_date=pd.Timestamp("2026-01-05"),
        strategy_name="IC",
        tags={"max_defined_risk_twd": max_defined_risk_twd, "entry_credit_mid": 80.0},
    )


# ---------- check_risk ----------


def test_check_risk_max_loss_per_trade(
    empty_state: PortfolioState, base_config: RiskConfig
) -> None:
    """proposed risk above limit → reject with reason."""
    signal = _make_open_signal(max_defined_risk_twd=25_000.0)  # > 20k limit
    allowed, reason = check_risk(empty_state, signal, base_config)
    assert allowed is False
    assert reason is not None and "max_loss_per_trade" in reason


def test_check_risk_max_concurrent_positions(base_config: RiskConfig) -> None:
    """At max_concurrent already → reject."""
    state = PortfolioState(
        cash=1_000_000.0,
        positions=[_make_position(15_000.0) for _ in range(3)],  # at limit (3)
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        initial_capital=1_000_000.0,
        aggregate_greeks={},
    )
    signal = _make_open_signal(max_defined_risk_twd=10_000.0)
    allowed, reason = check_risk(state, signal, base_config)
    assert allowed is False
    assert reason is not None and "max_concurrent_positions" in reason


def test_check_risk_max_capital_at_risk(base_config: RiskConfig) -> None:
    """Cumulative existing + new > limit → reject."""
    # Existing: 2 positions × 35k = 70k; limit = 80k.
    # New: 15k → total 85k > limit → reject.
    state = PortfolioState(
        cash=1_000_000.0,
        positions=[_make_position(35_000.0), _make_position(35_000.0)],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        initial_capital=1_000_000.0,
        aggregate_greeks={},
    )
    signal = _make_open_signal(max_defined_risk_twd=15_000.0)
    allowed, reason = check_risk(state, signal, base_config)
    assert allowed is False
    assert reason is not None and "max_capital_at_risk" in reason


def test_check_risk_portfolio_loss_cap(base_config: RiskConfig) -> None:
    """Realised + unrealised < -5% of initial_capital → reject (R5 P2 fix).

    initial_capital = 1_000_000; threshold = -0.05 × 1_000_000 = -50_000
    total_pnl = -40_000 + -15_000 = -55_000 < -50_000 → reject.
    """
    state = PortfolioState(
        cash=950_000.0,
        positions=[],
        realised_pnl=-40_000.0,
        unrealised_pnl=-15_000.0,
        initial_capital=1_000_000.0,
        aggregate_greeks={},
    )
    signal = _make_open_signal(max_defined_risk_twd=10_000.0)
    allowed, reason = check_risk(state, signal, base_config)
    assert allowed is False
    assert reason is not None and "portfolio_loss_cap" in reason


def test_check_risk_all_pass(empty_state: PortfolioState, base_config: RiskConfig) -> None:
    """Within all limits → (True, None)."""
    signal = _make_open_signal(max_defined_risk_twd=15_000.0)
    allowed, reason = check_risk(empty_state, signal, base_config)
    assert allowed is True
    assert reason is None


def test_check_risk_passes_through_non_open_signals(
    empty_state: PortfolioState, base_config: RiskConfig
) -> None:
    """close / adjust / hold → pass-through (True, None)."""
    o = Order(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        side="buy",
        qty=1,
    )
    for action in ("close", "adjust"):
        signal = StrategySignal(action=action, orders=[o])
        allowed, reason = check_risk(empty_state, signal, base_config)
        assert allowed is True
        assert reason is None
    # hold has empty orders
    hold = StrategySignal(action="hold", orders=[])
    allowed, reason = check_risk(empty_state, hold, base_config)
    assert allowed is True
    assert reason is None


# ---------- trigger_stop_loss ----------


def test_trigger_stop_loss_below_threshold(base_config: RiskConfig) -> None:
    """unrealised_TWD < -entry_credit_pts × multiple × TXO_MULTIPLIER → True (R8 P1)."""
    from config.constants import TXO_MULTIPLIER

    pos = _make_position(15_000.0)  # entry_credit_mid=80 (points)
    # threshold_twd = -80 × 2.0 × 50 = -8,000 TWD; current=-9,000 TWD → trigger.
    assert trigger_stop_loss(pos, current_unrealised_pnl=-9_000.0, config=base_config) is True
    # Sanity: hand-compute matches 80 × 2 × TXO_MULTIPLIER = 8000.
    assert 80 * 2 * TXO_MULTIPLIER == 8_000


def test_trigger_stop_loss_above_threshold(base_config: RiskConfig) -> None:
    """unrealised_TWD > -entry_credit_pts × multiple × TXO_MULTIPLIER → False."""
    pos = _make_position(15_000.0)  # entry_credit_mid=80 (points)
    # threshold_twd = -8,000; current = -7,000 (less negative) → no trigger.
    assert trigger_stop_loss(pos, current_unrealised_pnl=-7_000.0, config=base_config) is False


def test_trigger_stop_loss_unit_consistency(base_config: RiskConfig) -> None:
    """R8 P1 regression: -160 TWD must NOT trigger when threshold is -8,000 TWD.

    Previous bug compared TWD PnL against points threshold, so a -160 TWD draw
    (50× smaller than the real -8,000 floor) would falsely trigger.
    """
    pos = _make_position(15_000.0)  # entry_credit_mid=80 pts
    assert trigger_stop_loss(pos, current_unrealised_pnl=-160.0, config=base_config) is False, (
        "TWD vs points unit mismatch — see R8 F1"
    )


def test_trigger_stop_loss_no_baseline_returns_false(base_config: RiskConfig) -> None:
    """Position without entry_credit_mid tag → False (cannot evaluate)."""
    leg = OptionLeg(
        contract="TXO20260219C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-05"),
        entry_price=100.0,
    )
    pos = Position(legs=[leg], open_date=pd.Timestamp("2026-01-05"), strategy_name="IC")
    # No tags['entry_credit_mid'] → False even on huge negative PnL.
    assert trigger_stop_loss(pos, current_unrealised_pnl=-1_000_000.0, config=base_config) is False
