"""Tests for src/backtest/walk_forward.py — Week 6 Day 6.2.

7 tests:
  1. WalkForwardConfig validation: train/test/step <= 0 raise
  2. _generate_fold_windows: known chain length → expected fold count
  3. expanding vs rolling: train_start 第二 fold 起不同
  4. walk_forward_backtest: empty / missing 'date' col raise
  5. walk_forward_backtest smoke run on synthetic chain (mini config)
  6. PIT correctness: strategy_factory train_returns 不含 test 期 dates
  7. Aggregate: concat OOS daily_pnl 跨 fold 非重疊 + sorted index
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.walk_forward import (
    WalkForwardConfig,
    _generate_fold_windows,
    walk_forward_backtest,
)
from src.strategies.base import Strategy

# ---------------------------------------------------------------------------
# WalkForwardConfig validation
# ---------------------------------------------------------------------------


def test_walk_forward_config_validation() -> None:
    with pytest.raises(ValueError, match="train_window_days must be > 0"):
        WalkForwardConfig(train_window_days=0)
    with pytest.raises(ValueError, match="test_window_days must be > 0"):
        WalkForwardConfig(test_window_days=0)
    with pytest.raises(ValueError, match="step_days must be > 0"):
        WalkForwardConfig(step_days=0)
    with pytest.raises(ValueError, match="initial_capital must be > 0"):
        WalkForwardConfig(initial_capital=0)


def test_walk_forward_config_step_lt_test_raises_r12_0() -> None:
    """R12.0 P3 fix (Codex audit): step < test → fold OOS overlap → reject."""
    with pytest.raises(ValueError, match="step_days .* must be >= test_window_days"):
        WalkForwardConfig(train_window_days=252, test_window_days=63, step_days=21)
    # step == test 接受 (disjoint boundary)
    cfg = WalkForwardConfig(train_window_days=252, test_window_days=63, step_days=63)
    assert cfg.step_days == cfg.test_window_days
    # step > test 接受 (gap between folds)
    cfg2 = WalkForwardConfig(train_window_days=252, test_window_days=63, step_days=126)
    assert cfg2.step_days > cfg2.test_window_days


# ---------------------------------------------------------------------------
# _generate_fold_windows
# ---------------------------------------------------------------------------


def test_generate_fold_windows_count() -> None:
    """10-day chain, train=2/test=1/step=1 →
    train_end=2 test_end=3 (idx 3 valid <10), step → train_start 0,1,2,...
    fold idx 0: train 0-1, test 2;  ... fold k: train k..k+1, test k+2 (k+2<10) → k=0..7 = 8 folds
    """
    dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10, freq="D"))
    cfg = WalkForwardConfig(
        train_window_days=2, test_window_days=1, step_days=1, initial_capital=1.0
    )
    folds = _generate_fold_windows(dates, cfg)
    assert len(folds) == 8
    # Fold 0: train [0..1], test [2..2]
    assert folds[0][0] == dates[0]
    assert folds[0][1] == dates[1]
    assert folds[0][2] == dates[2]
    assert folds[0][3] == dates[2]


def test_generate_fold_windows_too_short_returns_empty() -> None:
    dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=2, freq="D"))
    cfg = WalkForwardConfig(
        train_window_days=2, test_window_days=1, step_days=1, initial_capital=1.0
    )
    folds = _generate_fold_windows(dates, cfg)
    assert folds == []


def test_generate_fold_windows_expanding_vs_rolling() -> None:
    """Expanding: train_start 永遠 = dates[0]; rolling: train_start sliding."""
    dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10, freq="D"))
    cfg_rolling = WalkForwardConfig(
        train_window_days=2,
        test_window_days=1,
        step_days=1,
        expanding=False,
        initial_capital=1.0,
    )
    cfg_expanding = WalkForwardConfig(
        train_window_days=2,
        test_window_days=1,
        step_days=1,
        expanding=True,
        initial_capital=1.0,
    )
    rolling = _generate_fold_windows(dates, cfg_rolling)
    expanding = _generate_fold_windows(dates, cfg_expanding)
    assert len(rolling) == len(expanding)
    # Fold 1: rolling train_start = dates[1]; expanding train_start = dates[0]
    assert rolling[1][0] == dates[1]
    assert expanding[1][0] == dates[0]


# ---------------------------------------------------------------------------
# walk_forward_backtest input validation
# ---------------------------------------------------------------------------


def test_walk_forward_backtest_empty_chain_raises() -> None:
    cfg = WalkForwardConfig(
        train_window_days=2, test_window_days=1, step_days=1, initial_capital=1.0
    )
    with pytest.raises(ValueError, match="chain is empty"):
        walk_forward_backtest(lambda r: _NoOpStrategy(), pd.DataFrame(), cfg)


def test_walk_forward_backtest_missing_date_col_raises() -> None:
    cfg = WalkForwardConfig(
        train_window_days=2, test_window_days=1, step_days=1, initial_capital=1.0
    )
    bad = pd.DataFrame({"strike": [16800.0]})
    with pytest.raises(ValueError, match="missing 'date' column"):
        walk_forward_backtest(lambda r: _NoOpStrategy(), bad, cfg)


# ---------------------------------------------------------------------------
# Smoke: walk-forward on synthetic chain (mini config)
# ---------------------------------------------------------------------------


class _NoOpStrategy(Strategy):
    """Strategy that opens nothing — used to smoke walk-forward plumbing."""

    def __init__(self) -> None:
        self.train_returns_seen: list[pd.Series] = []

    def should_open(self, chain, state):
        return False

    def open_position(self, chain, state):
        return None

    def should_close(self, chain, position):
        return False

    def should_adjust(self, chain, position):
        return None


def test_walk_forward_smoke_synthetic_chain(synthetic_chain: pd.DataFrame) -> None:
    """3-month synthetic chain, mini config → fold count > 0, no crash."""
    # synthetic chain ~63 trading days; mini config train=20/test=5/step=10
    cfg = WalkForwardConfig(
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        initial_capital=1_000_000.0,
        mark_policy="strict_mid",
    )

    def factory(train_returns: pd.Series) -> Strategy:
        return _NoOpStrategy()

    result = walk_forward_backtest(factory, synthetic_chain, cfg)
    assert result.n_folds > 0
    assert result.n_failed_folds == 0
    # No-op strategy → no trades, daily_pnl all zero
    if not result.daily_pnl.empty:
        assert (result.daily_pnl == 0.0).all()


# ---------------------------------------------------------------------------
# PIT correctness: factory sees train returns only
# ---------------------------------------------------------------------------


def test_walk_forward_pit_train_returns_no_lookahead(
    synthetic_chain: pd.DataFrame,
) -> None:
    """strategy_factory(train_returns) — train_returns 必須 all <= train_end (PIT).

    每 fold 攔截 factory 收到的 train_returns, 確認其 index 全 <= 該 fold train_end.
    """
    cfg = WalkForwardConfig(
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        initial_capital=1_000_000.0,
        mark_policy="strict_mid",
    )
    # Build underlying returns indexed by chain dates
    chain_dates = pd.DatetimeIndex(pd.to_datetime(synthetic_chain["date"]).unique())
    chain_dates = chain_dates.sort_values()
    rng = np.random.default_rng(seed=0)
    underlying_returns = pd.Series(
        rng.normal(0, 0.01, len(chain_dates)),
        index=chain_dates,
    )

    folds = _generate_fold_windows(chain_dates, cfg)
    assert folds  # smoke

    received: list[tuple[pd.Timestamp, pd.Timestamp, pd.Series]] = []

    def factory(train_returns: pd.Series) -> Strategy:
        # train_end will be paired up after — push current returns
        received.append((train_returns.index.min(), train_returns.index.max(), train_returns))
        return _NoOpStrategy()

    walk_forward_backtest(factory, synthetic_chain, cfg, underlying_returns=underlying_returns)

    # Each fold's received train_returns max date <= that fold's train_end
    assert len(received) == len(folds)
    for i, (_train_start, train_end, _, _) in enumerate(folds):
        rcv_max = received[i][1]
        assert rcv_max <= train_end, (
            f"Fold {i}: factory saw return on {rcv_max} > train_end {train_end} (look-ahead!)"
        )


# ---------------------------------------------------------------------------
# Aggregate: concat OOS daily_pnl is sorted + non-overlapping
# ---------------------------------------------------------------------------


def test_walk_forward_aggregate_concat_sorted(synthetic_chain: pd.DataFrame) -> None:
    """Aggregated daily_pnl across folds: sorted index, no duplicate dates."""
    cfg = WalkForwardConfig(
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        initial_capital=1_000_000.0,
        mark_policy="strict_mid",
    )

    def factory(train_returns: pd.Series) -> Strategy:
        return _NoOpStrategy()

    result = walk_forward_backtest(factory, synthetic_chain, cfg)
    if not result.daily_pnl.empty:
        # Sorted ascending
        assert result.daily_pnl.index.is_monotonic_increasing
        # No duplicate dates (folds 設計上 OOS 不重疊 — step >= test_window)
        # Note: when step < test_window, OOS overlaps and dup is allowed; here step=10 > test=5
        assert not result.daily_pnl.index.duplicated().any()


# ---------------------------------------------------------------------------
# R12.4 P fix: _extract_rejected_reasons unwrap depth (Codex audit)
# ---------------------------------------------------------------------------


def test_extract_rejected_reasons_unwrap_depth_r12_4() -> None:
    """R12.4 P fix (Codex audit): _extract_rejected_reasons unwraps arbitrary
    wrapper depth up to _MAX_UNWRAP_DEPTH=16. Original `range(3)` silent
    failed at depth 3+. Verifies depth 0-15 all succeed.
    """
    from src.backtest.walk_forward import _extract_rejected_reasons

    class L0:
        def get_rejected_reasons(self) -> pd.DataFrame:
            return pd.DataFrame(
                [{"date": "2026-01-01", "path": "open", "reason": "test", "leg": "X"}]
            )

    class W:
        def __init__(self, b: object) -> None:
            self.base = b

    candidate: object = L0()
    for depth in range(16):
        result = _extract_rejected_reasons(candidate)
        assert len(result) == 1, f"depth {depth} silent fail: got {len(result)} rows"
        candidate = W(candidate)


def test_extract_rejected_reasons_cycle_safe_r12_4() -> None:
    """R12.4 P fix: cycle detection prevents infinite loop on self-referential wrappers."""
    from src.backtest.walk_forward import _extract_rejected_reasons

    class CycleWrapper:
        def __init__(self) -> None:
            self.base = self  # self-reference

    # Should not infinite-loop, returns empty
    result = _extract_rejected_reasons(CycleWrapper())
    assert result.empty
    assert list(result.columns) == ["date", "path", "reason", "leg"]
