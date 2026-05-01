"""Tests for src/options/regime_gate.py — Week 6 Day 6.0.

8 tests:
  1. test_iv_percentile_init_validation
  2. test_iv_percentile_pre_warm_returns_false (lookback 不夠 → False)
  3. test_iv_percentile_high_vol_active (current >= percentile → True)
  4. test_iv_percentile_low_vol_inactive (current < percentile → False)
  5. test_iv_percentile_pit_correctness (date <= history.index)
  6. test_hmm_init_validation
  7. test_hmm_two_state_high_vs_low (synthetic regime switch → high state catch)
  8. test_hmm_pre_warm_returns_false
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.options.regime_gate import HMMRegimeGate, IVPercentileGate


def _make_returns(n: int, mean: float = 0.0, std: float = 0.01, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed=seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.Series(rng.normal(mean, std, n), index=dates, name="returns")


# ---------------------------------------------------------------------------
# IVPercentileGate
# ---------------------------------------------------------------------------


def test_iv_percentile_init_validation() -> None:
    with pytest.raises(ValueError, match="lookback days"):
        IVPercentileGate(vol_lookback_days=0)
    with pytest.raises(ValueError, match="lookback days"):
        IVPercentileGate(percentile_lookback_days=-1)
    with pytest.raises(ValueError, match="threshold_pct"):
        IVPercentileGate(threshold_pct=1.5)


def test_iv_percentile_pre_warm_returns_false() -> None:
    """Pre-warm: lookback (30 vol + 252 ref) 不夠 → fail-closed."""
    returns = _make_returns(100)  # 太短
    gate = IVPercentileGate()
    last_date = returns.index[-1]
    assert gate.is_active(last_date, returns) is False


def test_iv_percentile_high_vol_active() -> None:
    """近期 vol > 1yr 30%-percentile → active=True."""
    # 前 252 天低 vol，近 30 天高 vol → current 100% percentile → active
    low = _make_returns(282, mean=0.0, std=0.005, seed=1)
    rng = np.random.default_rng(seed=2)
    high_part = rng.normal(0.0, 0.05, 30)
    returns = low.copy()
    returns.iloc[-30:] = high_part
    gate = IVPercentileGate(threshold_pct=0.30)
    last_date = returns.index[-1]
    assert gate.is_active(last_date, returns) is True


def test_iv_percentile_low_vol_inactive() -> None:
    """近期 vol < 1yr 70%-percentile → active=False (用 high threshold)."""
    rng = np.random.default_rng(seed=3)
    # 前段高 vol, 近 30 天低 vol
    dates = pd.date_range("2020-01-01", periods=400, freq="B")
    vals = rng.normal(0.0, 0.05, 400)
    vals[-30:] = rng.normal(0.0, 0.001, 30)  # very low recent vol
    returns = pd.Series(vals, index=dates)
    gate = IVPercentileGate(threshold_pct=0.70)  # 嚴閾值
    last_date = returns.index[-1]
    assert gate.is_active(last_date, returns) is False


def test_iv_percentile_pit_correctness() -> None:
    """Gate 只看 date <= today 的歷史 (PIT)."""
    returns = _make_returns(400, mean=0.0, std=0.02)
    gate = IVPercentileGate()
    # 給 future 資料但 date 在中間
    mid_date = returns.index[200]
    # gate 應只用 returns[<= mid_date] = 200 days 不夠 282 → False
    assert gate.is_active(mid_date, returns) is False


# ---------------------------------------------------------------------------
# HMMRegimeGate
# ---------------------------------------------------------------------------


def test_hmm_init_validation() -> None:
    with pytest.raises(ValueError, match="lookback_days"):
        HMMRegimeGate(lookback_days=0)
    with pytest.raises(ValueError, match="active_state"):
        HMMRegimeGate(active_state="medium_vol")


def test_hmm_two_state_high_vs_low() -> None:
    """Synthetic vol switch: 前半低 vol + 後半高 vol → high state catch.

    R12.0 P1 fix: lookback_days 必須 covers 兩 regime; 原本 lookback=504 在
    1010-day series 取 last 504 全在 high-vol 期 → HMM 無 regime 區分 →
    silent bug 才能 pass. Fix: lookback=n_total 看整段 → HMM 真區分兩 regime.
    """
    rng = np.random.default_rng(seed=42)
    n_total = 1010
    dates = pd.date_range("2020-01-01", periods=n_total, freq="B")
    # 前半 low vol, 後半 high vol
    low = rng.normal(0.0, 0.005, n_total // 2)
    high = rng.normal(0.0, 0.030, n_total - n_total // 2)
    vals = np.concatenate([low, high])
    returns = pd.Series(vals, index=dates)
    gate = HMMRegimeGate(lookback_days=n_total, active_state="high_vol")
    last_date = returns.index[-1]
    # last sample 在 high vol period → high_vol gate active=True
    assert gate.is_active(last_date, returns) is True

    # Mutation Pattern 11: low_vol gate same date 必 False
    gate_low = HMMRegimeGate(lookback_days=n_total, active_state="low_vol")
    assert gate_low.is_active(last_date, returns) is False


def test_hmm_pre_warm_returns_false() -> None:
    """lookback_days=504 但只給 100 days → fail-closed."""
    returns = _make_returns(100)
    gate = HMMRegimeGate(lookback_days=504)
    last_date = returns.index[-1]
    assert gate.is_active(last_date, returns) is False
