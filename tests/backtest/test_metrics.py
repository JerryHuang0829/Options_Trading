"""Tests for src/backtest/metrics.py — Week 2 Day 5."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import max_drawdown, sharpe_ratio, win_rate


def test_sharpe_zero_on_empty() -> None:
    assert sharpe_ratio(pd.Series([], dtype=float)) == 0.0


def test_sharpe_zero_on_constant_series() -> None:
    """Zero std → undefined; we return 0.0 instead of raising."""
    assert sharpe_ratio(pd.Series([100.0, 100.0, 100.0])) == 0.0


def test_sharpe_known_value() -> None:
    """Known mean=1, std=1 daily PnL with rf=0 → Sharpe = 1 * sqrt(252)."""
    rng = np.random.default_rng(0)
    series = pd.Series(rng.standard_normal(10_000) + 1.0)
    s = sharpe_ratio(series, risk_free_rate=0.0)
    # mean ≈ 1, std ≈ 1 → sharpe ≈ sqrt(252) ≈ 15.87
    assert s == pytest.approx(math.sqrt(252), rel=0.05)


def test_sharpe_twd_mode_with_capital() -> None:
    """R10 F1: TWD PnL must divide by initial_capital before rf arithmetic.

    Construct daily_pnl with mean=88.8 TWD, std=380.7 TWD, capital=1M, rf=1.5%:
      returns_mean = 88.8 / 1e6 = 8.88e-5
      returns_std  = 380.7 / 1e6 = 3.807e-4
      rf_daily     = 0.015 / 252 ≈ 5.95e-5
      excess_mean  = 8.88e-5 - 5.95e-5 ≈ 2.93e-5
      sharpe       = 2.93e-5 / 3.807e-4 × sqrt(252) ≈ 1.22

    The buggy version subtracted rf_daily (decimal) directly from TWD PnL,
    which gave Sharpe ≈ 3.70 (way overstated).
    """
    rng = np.random.default_rng(7)
    twd = pd.Series(rng.normal(loc=88.8, scale=380.7, size=10_000))
    s = sharpe_ratio(twd, risk_free_rate=0.015, initial_capital=1_000_000.0)
    # Expected ~1.22 (analytical); allow 5% noise from RNG sample.
    assert 1.0 < s < 1.5, f"expected ~1.22, got {s}"


def test_sharpe_twd_without_capital_warns_via_units() -> None:
    """R10 F1 sanity: TWD PnL with rf=0 and no capital still works (returns
    mode degenerates to mean/std × sqrt(252) — magnitude same as old buggy
    code, but caller is responsible)."""
    twd = pd.Series([100.0, -50.0, 200.0, 30.0, -10.0])
    # rf=0 → no unit conflict
    s = sharpe_ratio(twd, risk_free_rate=0.0)
    assert s != 0.0  # smoke


def test_sharpe_invalid_capital_raises() -> None:
    twd = pd.Series([100.0, -50.0, 200.0])
    with pytest.raises(ValueError, match="initial_capital"):
        sharpe_ratio(twd, initial_capital=0)


def test_max_drawdown_zero_on_monotonic_up() -> None:
    cum = pd.Series([0, 1, 2, 3, 5])
    assert max_drawdown(cum) == 0.0


def test_max_drawdown_correct_value() -> None:
    """Cum: [0, 10, 5, 8, 2]; running max: [0, 10, 10, 10, 10]; dd: [0,0,-5,-2,-8]."""
    cum = pd.Series([0, 10, 5, 8, 2])
    assert max_drawdown(cum) == pytest.approx(-8.0)


def test_max_drawdown_scaled_by_initial() -> None:
    """initial_capital=100 → -8 / 100 = -0.08."""
    cum = pd.Series([0, 10, 5, 8, 2])
    assert max_drawdown(cum, initial_capital=100.0) == pytest.approx(-0.08)


def test_max_drawdown_first_day_loss_registers() -> None:
    """R9 P2: if day 1 is already a loss, max DD must reflect that loss
    (not 0). The entry baseline cum_pnl=0 is the implicit pre-trade peak.
    """
    # Single-day cumulative PnL of -240 (day-1 loss)
    cum = pd.Series([-240.0])
    assert max_drawdown(cum) == pytest.approx(-240.0)
    assert max_drawdown(cum, initial_capital=1_000_000.0) == pytest.approx(-0.00024)


def test_max_drawdown_all_negative_uses_zero_baseline() -> None:
    """R9 P2: every-day-loss series → DD equals worst cumulative loss
    (running peak stays at 0)."""
    cum = pd.Series([-100, -250, -400, -300])
    assert max_drawdown(cum) == pytest.approx(-400.0)


def test_max_drawdown_recovery_then_loss() -> None:
    """Peak (10) at index 1; trough (-5) at index 4 → DD = -15."""
    cum = pd.Series([0, 10, 5, 0, -5])
    assert max_drawdown(cum) == pytest.approx(-15.0)


def test_win_rate_zero_on_empty() -> None:
    assert win_rate(pd.DataFrame()) == 0.0


def test_win_rate_correct() -> None:
    trades = pd.DataFrame({"realised_pnl": [10.0, -5.0, 7.0, -3.0, 0.0]})
    # 2 wins / 5 = 0.4
    assert win_rate(trades) == pytest.approx(0.4)


def test_win_rate_missing_column_raises() -> None:
    with pytest.raises(KeyError):
        win_rate(pd.DataFrame({"foo": [1, 2, 3]}))
