"""Tests for src/common/types.py — Week 2 Day 1 domain model."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from src.common.types import (
    OptionQuote,
    Order,
    PortfolioState,
    RiskConfig,
    StrategySignal,
)


def test_option_quote_immutable() -> None:
    """OptionQuote must be frozen and reject invalid option_type."""
    q = OptionQuote(
        date=pd.Timestamp("2026-04-25"),
        expiry=pd.Timestamp("2026-05-30"),
        strike=16800,
        option_type="call",
        settle=120.5,
        bid=119.0,
        ask=122.0,
        iv=0.20,
        delta=0.45,
        underlying=16850.0,
    )
    # frozen: setattr should fail
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.strike = 17000  # type: ignore[misc]
    # invalid option_type rejected at construction
    with pytest.raises(ValueError, match="option_type must be"):
        OptionQuote(
            date=pd.Timestamp("2026-04-25"),
            expiry=pd.Timestamp("2026-05-30"),
            strike=16800,
            option_type="cal",  # type: ignore[arg-type]
            settle=120.5,
            bid=119.0,
            ask=122.0,
            iv=0.20,
            delta=0.45,
            underlying=16850.0,
        )


def test_order_qty_positive() -> None:
    """Order.qty must be > 0; sign carried by side."""
    Order(
        contract="TXO202605C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-05-30"),
        option_type="call",
        side="sell",
        qty=1,
    )
    with pytest.raises(ValueError, match="qty must be > 0"):
        Order(
            contract="TXO202605C17000",
            strike=17000,
            expiry=pd.Timestamp("2026-05-30"),
            option_type="call",
            side="sell",
            qty=0,
        )
    with pytest.raises(ValueError, match="qty must be > 0"):
        Order(
            contract="TXO202605C17000",
            strike=17000,
            expiry=pd.Timestamp("2026-05-30"),
            option_type="call",
            side="sell",
            qty=-1,
        )
    with pytest.raises(ValueError, match="side must be"):
        Order(
            contract="TXO202605C17000",
            strike=17000,
            expiry=pd.Timestamp("2026-05-30"),
            option_type="call",
            side="sel",  # type: ignore[arg-type]
            qty=1,
        )


def test_strategy_signal_action_literal() -> None:
    """StrategySignal action must be in closed set; hold has empty orders."""
    sample_order = Order(
        contract="TXO202605C17000",
        strike=17000,
        expiry=pd.Timestamp("2026-05-30"),
        option_type="call",
        side="sell",
        qty=1,
    )
    StrategySignal(action="open", orders=[sample_order])
    StrategySignal(action="hold", orders=[])

    with pytest.raises(ValueError, match="action invalid"):
        StrategySignal(action="OPEN", orders=[sample_order])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty orders"):
        StrategySignal(action="hold", orders=[sample_order])
    with pytest.raises(ValueError, match="requires at least 1 order"):
        StrategySignal(action="open", orders=[])


def test_portfolio_state_aggregate_greeks_keys() -> None:
    """PortfolioState aggregate_greeks must have exactly delta/gamma/theta/vega keys."""
    PortfolioState(
        cash=1_000_000.0,
        positions=[],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0},
    )
    # Empty dict is allowed (initial state).
    PortfolioState(
        cash=1_000_000.0,
        positions=[],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={},
    )
    # Wrong keys rejected.
    with pytest.raises(ValueError, match="aggregate_greeks keys"):
        PortfolioState(
            cash=1_000_000.0,
            positions=[],
            realised_pnl=0.0,
            unrealised_pnl=0.0,
            aggregate_greeks={"delta": 0.0, "gamma": 0.0},
        )


def test_risk_config_validation() -> None:
    """RiskConfig __post_init__ rejects non-positive limits / out-of-range loss cap."""
    RiskConfig(
        max_loss_per_trade_twd=20_000.0,
        max_capital_at_risk_twd=100_000.0,
        max_concurrent_positions=3,
        stop_loss_multiple=2.0,
        portfolio_loss_cap_pct=0.05,
    )
    with pytest.raises(ValueError, match="max_loss_per_trade_twd"):
        RiskConfig(
            max_loss_per_trade_twd=0.0,
            max_capital_at_risk_twd=100_000.0,
            max_concurrent_positions=3,
            stop_loss_multiple=2.0,
            portfolio_loss_cap_pct=0.05,
        )
    with pytest.raises(ValueError, match="stop_loss_multiple"):
        RiskConfig(
            max_loss_per_trade_twd=20_000.0,
            max_capital_at_risk_twd=100_000.0,
            max_concurrent_positions=3,
            stop_loss_multiple=-1.0,
            portfolio_loss_cap_pct=0.05,
        )
    with pytest.raises(ValueError, match="portfolio_loss_cap_pct"):
        RiskConfig(
            max_loss_per_trade_twd=20_000.0,
            max_capital_at_risk_twd=100_000.0,
            max_concurrent_positions=3,
            stop_loss_multiple=2.0,
            portfolio_loss_cap_pct=1.5,  # > 1
        )
    with pytest.raises(ValueError, match="portfolio_loss_cap_pct"):
        RiskConfig(
            max_loss_per_trade_twd=20_000.0,
            max_capital_at_risk_twd=100_000.0,
            max_concurrent_positions=3,
            stop_loss_multiple=2.0,
            portfolio_loss_cap_pct=0.0,  # not > 0
        )


def test_re_exports_from_common() -> None:
    """OptionLeg / Position / Fill / FillModel / ChainQuote re-exported from src.common.types."""
    from src.common.types import ChainQuote, Fill, FillModel, OptionLeg, Position  # noqa: F401
    # smoke test: imports succeed (no error from circular / missing)
