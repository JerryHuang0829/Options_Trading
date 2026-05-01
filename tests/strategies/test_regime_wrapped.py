"""Tests for src/strategies/regime_wrapped.py — Week 6 Day 6.0.

5 tests:
  1. test_regime_wrapped_vanilla_passthrough (gate=None → 等同 base)
  2. test_regime_wrapped_with_gate_blocks_open (gate.is_active=False → should_open=False)
  3. test_regime_wrapped_with_gate_allows_open (gate.is_active=True → 等同 base)
  4. test_regime_wrapped_close_adjust_delegate (close/adjust 不被 gate 影響)
  5. test_regime_wrapped_init_validation (gate without returns_history raises)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from src.common.types import PortfolioState
from src.options.regime_gate import RegimeGate
from src.strategies.regime_wrapped import RegimeWrappedStrategy

if TYPE_CHECKING:
    pass


class _AlwaysOpenStrategy:
    """Tiny stub implementing Strategy 4-method interface."""

    def __init__(self, returns_open=True, returns_close=False) -> None:
        self.returns_open = returns_open
        self.returns_close = returns_close

    def should_open(self, chain, state):
        return self.returns_open

    def open_position(self, chain, state):
        from src.common.types import StrategySignal

        return StrategySignal(action="open", orders=[], metadata={"signal": "stub"})

    def should_close(self, chain, position):
        return self.returns_close

    def should_adjust(self, chain, position):
        return None


class _StubGate(RegimeGate):
    def __init__(self, active: bool) -> None:
        self.active = active

    def is_active(self, date, returns_history):
        return self.active


@pytest.fixture
def empty_state() -> PortfolioState:
    return PortfolioState(
        cash=1_000_000.0, positions=[], realised_pnl=0.0, unrealised_pnl=0.0, aggregate_greeks={}
    )


@pytest.fixture
def fake_chain() -> pd.DataFrame:
    return pd.DataFrame({"date": [pd.Timestamp("2024-01-15")], "underlying": [17500.0]})


@pytest.fixture
def returns_history() -> pd.Series:
    dates = pd.date_range("2023-01-01", periods=300, freq="B")
    return pd.Series([0.01] * 300, index=dates, name="returns")


def test_regime_wrapped_vanilla_passthrough(fake_chain, empty_state) -> None:
    """gate=None → 等同 base.should_open."""
    base = _AlwaysOpenStrategy(returns_open=True)
    wrapped = RegimeWrappedStrategy(base, regime_gate=None)  # type: ignore[arg-type]
    assert wrapped.should_open(fake_chain, empty_state) is True

    base.returns_open = False
    assert wrapped.should_open(fake_chain, empty_state) is False


def test_regime_wrapped_with_gate_blocks_open(fake_chain, empty_state, returns_history) -> None:
    """gate.is_active=False → should_open=False (儘管 base.should_open=True)."""
    base = _AlwaysOpenStrategy(returns_open=True)
    gate = _StubGate(active=False)
    wrapped = RegimeWrappedStrategy(base, regime_gate=gate, returns_history=returns_history)  # type: ignore[arg-type]
    assert wrapped.should_open(fake_chain, empty_state) is False


def test_regime_wrapped_with_gate_allows_open(fake_chain, empty_state, returns_history) -> None:
    """gate.is_active=True → 等同 base.should_open."""
    base = _AlwaysOpenStrategy(returns_open=True)
    gate = _StubGate(active=True)
    wrapped = RegimeWrappedStrategy(base, regime_gate=gate, returns_history=returns_history)  # type: ignore[arg-type]
    assert wrapped.should_open(fake_chain, empty_state) is True


def test_regime_wrapped_close_adjust_delegate(fake_chain, returns_history) -> None:
    """close / adjust 永遠 delegate 給 base, 不被 gate 影響 (gate 只控 open)."""
    base = _AlwaysOpenStrategy(returns_close=True)
    gate = _StubGate(active=False)  # gate inactive
    wrapped = RegimeWrappedStrategy(base, regime_gate=gate, returns_history=returns_history)  # type: ignore[arg-type]
    # close 仍 = base.should_close = True (gate 不擋 close)
    assert wrapped.should_close(fake_chain, position=None) is True  # type: ignore[arg-type]
    # adjust = base.should_adjust = None
    assert wrapped.should_adjust(fake_chain, position=None) is None  # type: ignore[arg-type]


def test_regime_wrapped_init_validation() -> None:
    """gate 不為 None 但 returns_history=None → raise."""
    base = _AlwaysOpenStrategy()
    gate = _StubGate(active=True)
    with pytest.raises(ValueError, match="returns_history"):
        RegimeWrappedStrategy(base, regime_gate=gate, returns_history=None)  # type: ignore[arg-type]
