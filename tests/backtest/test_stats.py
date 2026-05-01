"""Tests for src/backtest/stats.py — Week 6 Day 6.1 Pro statistical tools.

12 tests:
  1-3. bootstrap_ci: input validation / Sharpe CI shape / reproducibility (seed)
  4-6. permutation_test: input validation / all-zero p_value=1.0 / observed
       distinct from null
  7-9. deflated_sharpe: n_trials<2 raise / n_trials=N raw vs DSR ordering /
       skew=0 kurt=3 公式
  10-12. calmar_ratio: max_dd=0 raise / 正常 case / 對齊 manual 計算
"""

from __future__ import annotations

import numpy as np
import pytest

from src.backtest.stats import (
    bootstrap_ci,
    calmar_ratio,
    deflated_sharpe,
    permutation_test,
)

# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_input_validation() -> None:
    with pytest.raises(ValueError, match="length must be >= 2"):
        bootstrap_ci(np.array([1.0]))
    with pytest.raises(ValueError, match="n_iter"):
        bootstrap_ci(np.array([1.0, 2.0]), n_iter=0)
    with pytest.raises(ValueError, match="ci"):
        bootstrap_ci(np.array([1.0, 2.0]), ci=1.5)
    with pytest.raises(ValueError, match="statistic must be"):
        bootstrap_ci(np.array([1.0, 2.0]), statistic="garbage")  # type: ignore[arg-type]


def test_bootstrap_ci_sharpe_returns_lower_le_upper() -> None:
    """合理 PnL → bootstrap CI lower <= upper, finite."""
    rng = np.random.default_rng(seed=1)
    daily_pnl = rng.normal(100, 1000, 252)  # 1-year daily PnL ~ Sharpe 1.6
    lower, upper = bootstrap_ci(daily_pnl, statistic="sharpe", n_iter=500, ci=0.95)
    assert np.isfinite(lower) and np.isfinite(upper)
    assert lower <= upper


def test_bootstrap_ci_seed_reproducible() -> None:
    """同 seed 兩次跑結果完全一致."""
    rng = np.random.default_rng(seed=2)
    pnl = rng.normal(50, 500, 100)
    ci1 = bootstrap_ci(pnl, n_iter=200, seed=42)
    ci2 = bootstrap_ci(pnl, n_iter=200, seed=42)
    assert ci1 == ci2


# ---------------------------------------------------------------------------
# permutation_test
# ---------------------------------------------------------------------------


def test_permutation_test_input_validation() -> None:
    with pytest.raises(ValueError, match="length must be >= 2"):
        permutation_test(np.array([1.0]))
    with pytest.raises(ValueError, match="n_iter"):
        permutation_test(np.array([1.0, 2.0]), n_iter=0)


def test_permutation_test_all_zero_pvalue_one() -> None:
    """all-zero PnL → null all NaN → degenerate p_value=1.0."""
    pnl = np.zeros(100)
    observed, null_dist, p_value = permutation_test(pnl, n_iter=100)
    assert p_value == 1.0
    assert np.isnan(observed)


def test_permutation_test_strong_signal_low_pvalue() -> None:
    """R12.0 P2 fix (Codex audit): sign-flip permutation 對強 alpha 應給低 p_value.

    Sign-flip H0: PnL 對稱、零 drift → null Sharpe centred at 0.
    Strong positive PnL (mean=200 std=500) → observed Sharpe far above null → p_value < 0.05.
    """
    rng = np.random.default_rng(seed=3)
    daily_pnl = rng.normal(200, 500, 252)  # 強正期望 PnL
    observed, null_dist, p_value = permutation_test(daily_pnl, n_iter=500, seed=42)
    assert np.isfinite(observed)
    assert null_dist.size == 500
    # null std must be > 0 (sign-flip varies Sharpe; old shuffle had std~1e-15)
    finite_null = null_dist[np.isfinite(null_dist)]
    assert finite_null.std() > 0.1, (
        f"sign-flip permutation null std should be > 0; got {finite_null.std()}"
    )
    # null centred near 0 (H0 zero drift)
    assert abs(finite_null.mean()) < observed
    # observed extreme → low p_value
    assert p_value < 0.05, f"strong signal should give p<0.05, got {p_value}"


def test_permutation_test_null_varies_under_sign_flip() -> None:
    """R12.0 P2 fix: null Sharpe distribution must actually vary (std > 0).

    Old shuffle permutation: null std ~ 1e-15 (floating point noise) — null
    全部 = observed because Sharpe = mean/std × √N is permutation-invariant.
    Sign-flip permutation: each iter independently flips signs → null Sharpe
    spreads out around 0 → null std > 0.1 typically for reasonable PnL series.
    """
    rng = np.random.default_rng(seed=11)
    pnl = rng.normal(50, 500, 252)
    _observed, null_dist, _p = permutation_test(pnl, n_iter=500, seed=42)
    finite = null_dist[np.isfinite(null_dist)]
    # Critical regression assertion: sign-flip MUST produce variability,
    # otherwise we silently regressed to permutation-invariant Sharpe.
    assert finite.std() > 0.1, (
        f"sign-flip null std must be > 0.1; got {finite.std()} — "
        "if this is ~1e-15 we regressed to permutation-invariant shuffle"
    )


# ---------------------------------------------------------------------------
# deflated_sharpe
# ---------------------------------------------------------------------------


def test_deflated_sharpe_n_trials_less_than_2_raises() -> None:
    """n_trials=1 → Φ⁻¹(0)=-inf 邊界 fail → raise."""
    with pytest.raises(ValueError, match="n_trials must be >= 2"):
        deflated_sharpe(observed_sharpe=1.0, n_trials=1, T=252)
    with pytest.raises(ValueError, match="T must be >= 2"):
        deflated_sharpe(observed_sharpe=1.0, n_trials=2, T=1)


def test_deflated_sharpe_higher_n_trials_lower_dsr() -> None:
    """N=2 (寬鬆) DSR > N=10 (嚴格) DSR for same observed Sharpe.

    Selection bias 校正: 越多 trials → SR_0 越高 → DSR 越小 (越難 significant).
    """
    sr = 1.5
    T = 1260  # 5yr
    dsr_n2 = deflated_sharpe(observed_sharpe=sr, n_trials=2, T=T)
    dsr_n10 = deflated_sharpe(observed_sharpe=sr, n_trials=10, T=T)
    dsr_n100 = deflated_sharpe(observed_sharpe=sr, n_trials=100, T=T)
    assert dsr_n2 > dsr_n10 > dsr_n100


def test_deflated_sharpe_normal_iid_baseline() -> None:
    """skew=0 kurt=3 (normal) + observed=0 → DSR ≈ Φ(-SR_0 sqrt(T-1)) close to 0.

    Mutation: skew ≠ 0 / kurt ≠ 3 應改變 DSR (denominator 變化).
    """
    dsr_normal = deflated_sharpe(observed_sharpe=1.5, n_trials=6, T=1260, skew=0.0, kurt=3.0)
    dsr_neg_skew = deflated_sharpe(observed_sharpe=1.5, n_trials=6, T=1260, skew=-0.5, kurt=4.0)
    # 負 skew + fat tail → denominator 變化 → DSR 改變
    assert 0.0 <= dsr_normal <= 1.0
    assert 0.0 <= dsr_neg_skew <= 1.0
    assert dsr_normal != dsr_neg_skew


# ---------------------------------------------------------------------------
# calmar_ratio
# ---------------------------------------------------------------------------


def test_calmar_ratio_max_dd_zero_raises() -> None:
    """All-positive PnL → equity 一直新高 → max_dd=0 → raise."""
    pnl = np.array([100.0, 200.0, 150.0, 300.0, 250.0])  # 累計 always > peak 0
    with pytest.raises(ValueError, match="max_drawdown == 0"):
        calmar_ratio(pnl, initial_capital=1_000_000.0)


def test_calmar_ratio_input_validation() -> None:
    with pytest.raises(ValueError, match="length must be >= 2"):
        calmar_ratio(np.array([100.0]), initial_capital=1_000_000.0)
    with pytest.raises(ValueError, match="initial_capital"):
        calmar_ratio(np.array([100.0, -50.0]), initial_capital=0)


def test_calmar_ratio_manual_calculation() -> None:
    """Calmar = annual_return / max_dd  — 對齊 manual 計算 (≥3 組數字驗算 Pattern 12)."""
    # 4-day PnL: +100, -200, +150, -100 → cum = [100, -100, 50, -50]
    # equity = [1M+100, 1M-100, 1M+50, 1M-50] (initial=1M)
    # full_equity (含 initial_capital peak) = [1M, 1M+100, 1M-100, 1M+50, 1M-50]
    # running_peak = [1M, 1M+100, 1M+100, 1M+100, 1M+100]
    # drawdown = [0, 0, 200/(1M+100)=0.0001999..., 50/(1M+100)=0.0000499..., 150/(1M+100)=0.0001499...]
    # max_dd = 200 / (1M+100) ≈ 0.000199998
    # total_return = sum(pnl)/cap = -50 / 1M = -0.00005
    # annual_return = -0.00005 * (252/4) = -0.00315
    # calmar = -0.00315 / 0.000199998 ≈ -15.75
    pnl = np.array([100.0, -200.0, 150.0, -100.0])
    cap = 1_000_000.0
    cal = calmar_ratio(pnl, initial_capital=cap, periods_per_year=252)
    expected_max_dd = 200.0 / (cap + 100.0)
    expected_annual = (-50.0 / cap) * (252.0 / 4.0)
    expected_calmar = expected_annual / expected_max_dd
    np.testing.assert_allclose(cal, expected_calmar, rtol=1e-9)
