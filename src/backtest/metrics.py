"""Backtest performance metrics.

Implementations:
  - sharpe_ratio: annualised; converts TWD daily PnL → returns via
    ``initial_capital`` before subtracting risk-free rate (R10 F1)
  - max_drawdown: peak-to-trough using cummax+clip(0) so day-1 losses
    register against the implicit 0 baseline (R9 P2)
  - win_rate: fraction of closed trades with realised PnL > 0

Phase 2 extensions (in `analytics/`, not here):
  - Greeks attribution, regime-aware metrics, bootstrap CI

Edge cases:
  - Empty inputs return 0.0 (Sharpe / max_dd) or 0.0 (win_rate when no trades).
  - Zero std → Sharpe 0.0 (degenerate; flat PnL is neither great nor bad).
"""

from __future__ import annotations

import math

import pandas as pd

from config.constants import TRADING_DAYS_PER_YEAR


def sharpe_ratio(
    daily_pnl: pd.Series,
    risk_free_rate: float = 0.015,
    *,
    initial_capital: float | None = None,
) -> float:
    """Annualised Sharpe ratio using ``TRADING_DAYS_PER_YEAR`` (252).

    Codex R10 F1 fix: Sharpe is a unit-less ratio; you cannot subtract a
    decimal rate from TWD PnL. Two valid invocations:

      1. **Returns mode** (caller pre-converts): ``daily_pnl`` is decimal
         daily returns and ``initial_capital=None``. ``rf_daily`` is
         subtracted directly.
      2. **TWD mode**: ``daily_pnl`` is TWD per-day PnL and
         ``initial_capital`` is provided in TWD. The function divides PnL by
         capital first to obtain daily returns, then subtracts ``rf / 252``.

    Args:
        daily_pnl: Per-day PnL — TWD (with ``initial_capital`` set) or
            decimal returns (with ``initial_capital=None``).
        risk_free_rate: Annualised RF rate decimal (default 1.5% per TW 1Y).
            Pass 0.0 to skip RF adjustment in either mode.
        initial_capital: TWD capital base for converting TWD PnL → returns.
            Required when ``daily_pnl`` is in TWD; pass None for returns input.

    Returns 0.0 if the series is empty, has < 2 obs, or has zero std.
    """
    if daily_pnl is None or len(daily_pnl) < 2:
        return 0.0
    series = pd.Series(daily_pnl).dropna()
    if len(series) < 2:
        return 0.0
    if initial_capital is not None:
        if initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
        # Convert TWD PnL → decimal daily returns before any rf arithmetic.
        series = series / initial_capital
    std = float(series.std(ddof=1))
    if std == 0.0 or math.isnan(std):
        return 0.0
    rf_daily = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess_mean = float(series.mean()) - rf_daily
    return excess_mean / std * math.sqrt(TRADING_DAYS_PER_YEAR)


def max_drawdown(cumulative_pnl: pd.Series, initial_capital: float | None = None) -> float:
    """Peak-to-trough decline as a (negative) fraction.

    Args:
        cumulative_pnl: Running cumulative PnL series (TWD), where index 0
            is the cumulative PnL **at end of day 1** (entry baseline = 0).
        initial_capital: Denominator for the ratio. If ``None``, drawdown is
            returned in raw TWD (negative number); otherwise scaled to fraction
            (e.g. -0.15 = 15%).

    Codex R9 P2: the entry baseline (cum_pnl = 0) is itself a peak. If day 1
    is already a loss, naive ``series.cummax()`` would set the running peak
    to the day-1 value (a loss), producing 0 drawdown. We prepend an implicit
    0 to running_max so day-1 losses register correctly.

    Returns 0.0 on empty or all-positive series.
    """
    if cumulative_pnl is None or len(cumulative_pnl) == 0:
        return 0.0
    series = pd.Series(cumulative_pnl).dropna()
    if series.empty:
        return 0.0
    # Running peak that includes the implicit pre-trade 0 baseline (R9 P2).
    running_max = series.cummax().clip(lower=0.0)
    drawdowns = series - running_max
    worst = float(drawdowns.min())
    # Drawdown is non-positive by construction (peak >= series at any point).
    if worst > 0:
        worst = 0.0
    if initial_capital is None:
        return worst
    if initial_capital <= 0:
        raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
    return worst / initial_capital


def win_rate(trades: pd.DataFrame) -> float:
    """Fraction of closed trades with realised PnL > 0.

    Args:
        trades: DataFrame with at least a ``realised_pnl`` column.

    Returns 0.0 on empty input.
    """
    if trades is None or len(trades) == 0:
        return 0.0
    if "realised_pnl" not in trades.columns:
        raise KeyError("win_rate requires 'realised_pnl' column in trades DataFrame")
    pnl = trades["realised_pnl"].dropna()
    if pnl.empty:
        return 0.0
    return float((pnl > 0).mean())
