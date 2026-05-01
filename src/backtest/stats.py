"""Pro statistical tools for backtest validation (Week 6 Day 6.1).

Per Pro 量化紀律 (`feedback_pro_research_standard.md`): retail 100 萬 baseline
+ Pro methodology (FDR / Deflated Sharpe / Bootstrap CI / Permutation / PIT).

4 工具:
  1. bootstrap_ci(daily_pnl, statistic, n_iter, ci, seed) → (lower, upper)
     Bootstrap percentile CI for any backtest statistic (default Sharpe).
     Used for Pro 閾值 「Bootstrap CI 不跨零硬條件」.

  2. permutation_test(daily_pnl, n_iter, seed) → (observed, null_dist, p_value)
     Shuffle PnL → null distribution → p-value of observed Sharpe.
     Used for "edge vs random baseline" significance test.

  3. deflated_sharpe(observed_sharpe, n_trials, T, skew, kurt) → DSR
     López-de-Prado 2014: corrects raw Sharpe for selection bias / non-normality.
     Used after multi-strategy / multi-parameter ablation (Phase 1 N=6 scenario).

  4. calmar_ratio(daily_pnl, initial_capital, periods_per_year) → ratio
     Annualized return / max drawdown. Phase 1 Pro 閾值含 Calmar > 0.5.

References:
  - López de Prado (2014). "The Deflated Sharpe Ratio: Correcting for Selection
    Bias, Backtest Overfitting, and Non-Normality." Journal of Portfolio
    Management 40(5).
  - Lo (2002). "The Statistics of Sharpe Ratios." Financial Analysts Journal 58.
  - Sharpe (1994). "The Sharpe Ratio." Journal of Portfolio Management 21(1).

Pro 紀律 (R10.5 P2 PIT correctness):
  All functions are pure (no I/O, no global state). Caller responsibility to
  pass non-look-ahead data. seed defaults to 42 for reproducibility.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

import numpy as np
import pandas as pd
from scipy.stats import norm

# Euler-Mascheroni constant (used in López-de-Prado SR_0 formula)
EULER_MASCHERONI = 0.5772156649015329

# Trading days per year (CALENDAR_DAYS_PER_YEAR=365 is for BSM T; trading
# days for annualization is 252 — Pro 量化標準).
TRADING_DAYS_PER_YEAR = 252


def _annualised_sharpe(
    pnl_series: np.ndarray | pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """Annualised Sharpe ratio from daily PnL (TWD or returns).

    SR = sqrt(periods_per_year) * mean(pnl) / std(pnl)
    Returns NaN if std == 0 or len < 2.
    """
    arr = np.asarray(pnl_series, dtype=np.float64)
    if arr.size < 2:
        return float("nan")
    std = float(np.std(arr, ddof=1))
    if std == 0:
        return float("nan")
    return float(math.sqrt(periods_per_year) * np.mean(arr) / std)


def bootstrap_ci(
    daily_pnl: np.ndarray | pd.Series,
    statistic: Literal["sharpe", "mean", "total_return"] | Callable = "sharpe",
    n_iter: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> tuple[float, float]:
    """Bootstrap percentile confidence interval for a backtest statistic.

    R&D usage:
        sharpe = _annualised_sharpe(daily_pnl)
        lower, upper = bootstrap_ci(daily_pnl, statistic='sharpe', ci=0.95)
        # Pro 閾值: lower > 0 表示統計顯著 (CI 不跨零)

    Args:
        daily_pnl: 1D array-like of daily PnL (TWD) or returns.
        statistic: 'sharpe' (default) | 'mean' | 'total_return' | callable
            Custom callable: f(np.ndarray) -> float.
        n_iter: bootstrap iterations (default 1000; Pro 標準).
        ci: confidence level in (0, 1); default 0.95 → 95% CI.
        seed: numpy seed for reproducibility (default 42).
        periods_per_year: annualisation factor for sharpe stat (252 trading).

    Returns:
        (lower, upper) percentile bounds.

    Raises:
        ValueError: len(daily_pnl) < 2 / n_iter < 1 / ci not in (0, 1).
    """
    arr = np.asarray(daily_pnl, dtype=np.float64)
    if arr.size < 2:
        raise ValueError(f"bootstrap_ci: daily_pnl length must be >= 2, got {arr.size}")
    if n_iter < 1:
        raise ValueError(f"bootstrap_ci: n_iter must be >= 1, got {n_iter}")
    if not 0 < ci < 1:
        raise ValueError(f"bootstrap_ci: ci must be in (0, 1), got {ci}")

    if callable(statistic):
        stat_fn: Callable = statistic
    elif statistic == "sharpe":

        def stat_fn(x: np.ndarray) -> float:
            return _annualised_sharpe(x, periods_per_year)
    elif statistic == "mean":
        stat_fn = lambda x: float(np.mean(x))  # noqa: E731
    elif statistic == "total_return":
        stat_fn = lambda x: float(np.sum(x))  # noqa: E731
    else:
        raise ValueError(
            f"bootstrap_ci: statistic must be 'sharpe'|'mean'|'total_return'|callable, got {statistic!r}"
        )

    rng = np.random.default_rng(seed=seed)
    n = arr.size
    samples = np.empty(n_iter, dtype=np.float64)
    for i in range(n_iter):
        idx = rng.integers(0, n, size=n)
        samples[i] = stat_fn(arr[idx])

    finite = samples[np.isfinite(samples)]
    if finite.size == 0:
        return (float("nan"), float("nan"))
    alpha = (1.0 - ci) / 2.0
    lower = float(np.percentile(finite, 100.0 * alpha))
    upper = float(np.percentile(finite, 100.0 * (1.0 - alpha)))
    return (lower, upper)


def permutation_test(
    daily_pnl: np.ndarray | pd.Series,
    n_iter: int = 1000,
    seed: int = 42,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> tuple[float, np.ndarray, float]:
    """Sign-flip permutation test for Sharpe significance.

    R12.0 P2 fix (Codex audit):
        Old impl shuffled the same daily_pnl. But Sharpe = mean / std × √N is
        permutation-invariant: shuffling preserves both mean and std exactly,
        so null = observed within float round-off (std ~ 1e-15) and p-value
        was meaningless.

        Sign-flip permutation (Politis & Romano 2010) preserves the marginal
        distribution under H0: "PnL is symmetric / has zero mean drift" and
        actively varies the mean (and Sharpe) across iterations. Concretely
        each iter independently flips signs:
            permuted[i] = ±1 * arr[i]   (Bernoulli 0.5)
        Under H0 (zero drift) the sign-flipped Sharpe ~ centred at 0.
        Observed Sharpe far from null distribution → reject H0 (alpha exists).

    Logic:
        observed_sharpe = sharpe(daily_pnl)
        null_sharpes = [sharpe(±1 · daily_pnl) for _ in n_iter]
        p_value = (sum(|null| >= |observed|) + 1) / (n_iter + 1)   (two-sided,
            Phipson & Smyth 2010 unbiased estimator)

    Pro usage: p_value < 0.05 → strategy mean PnL is statistically distinct
    from a zero-drift symmetric process.

    Args:
        daily_pnl: 1D array-like.
        n_iter: permutation iterations.
        seed: numpy seed.
        periods_per_year: annualisation.

    Returns:
        (observed_sharpe, null_sharpe_distribution, p_value)

    Raises:
        ValueError: len(daily_pnl) < 2 / n_iter < 1.
    """
    arr = np.asarray(daily_pnl, dtype=np.float64)
    if arr.size < 2:
        raise ValueError(f"permutation_test: daily_pnl length must be >= 2, got {arr.size}")
    if n_iter < 1:
        raise ValueError(f"permutation_test: n_iter must be >= 1, got {n_iter}")

    observed = _annualised_sharpe(arr, periods_per_year)
    if not math.isfinite(observed):
        # All-zero / constant PnL → null all also identical → p_value=1.0
        return (observed, np.full(n_iter, observed), 1.0)

    rng = np.random.default_rng(seed=seed)
    null_sharpes = np.empty(n_iter, dtype=np.float64)
    for i in range(n_iter):
        signs = rng.choice([-1.0, 1.0], size=arr.size)
        null_sharpes[i] = _annualised_sharpe(signs * arr, periods_per_year)

    # Two-sided p-value (Phipson & Smyth 2010 unbiased): test H0 mean=0 →
    # |observed| extreme vs |null|. (One-sided would bias toward positive Sharpe.)
    finite_null = null_sharpes[np.isfinite(null_sharpes)]
    n_extreme = int(np.sum(np.abs(finite_null) >= np.abs(observed)))
    p_value = (n_extreme + 1) / (finite_null.size + 1)
    return (observed, null_sharpes, p_value)


def deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    T: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """López-de-Prado 2014 Deflated Sharpe Ratio (DSR).

    Formula:
        SR_0 = sqrt(V[SR]) * ((1-γ_E) Φ⁻¹(1 - 1/N) + γ_E Φ⁻¹(1 - 1/(N e)))
        DSR = Φ((SR_obs - SR_0) sqrt(T-1) / sqrt(1 - skew*SR_obs + (kurt-1)/4 * SR_obs²))

    where:
        γ_E = 0.5772156649 (Euler-Mascheroni)
        Φ⁻¹ = inverse normal CDF (scipy.stats.norm.ppf)
        Φ = normal CDF
        V[SR] under H0 (= 1, normal-iid baseline; Lopez-de-Prado simplification)
        N = n_trials (multi-strategy / multi-parameter scan)
        T = sample size (trading days)
        skew, kurt = sample skewness / kurtosis (kurt = 3 for normal)

    Pro 用途: 多策略 ablation (Phase 1 N=6 scenario) 校正 selection bias —
    raw Sharpe 偏高估，DSR 是「真實顯著」修正版。

    Args:
        observed_sharpe: raw annualised Sharpe.
        n_trials: number of strategies/parameters tested (must be >= 2).
        T: sample size (number of daily returns).
        skew: sample skewness (default 0 = symmetric).
        kurt: sample kurtosis (default 3 = normal; Fisher kurtosis = kurt - 3).

    Returns:
        DSR ∈ [0, 1] — probability that observed_sharpe > SR_0 (selection-bias
        corrected). DSR > 0.95 → significant.

    Raises:
        ValueError: n_trials < 2 (boundary fail; Φ⁻¹(0) = -inf) / T < 2.
    """
    if n_trials < 2:
        raise ValueError(
            f"deflated_sharpe: n_trials must be >= 2 (Φ⁻¹(0) = -inf 邊界), got {n_trials}"
        )
    if T < 2:
        raise ValueError(f"deflated_sharpe: T must be >= 2, got {T}")

    # V[SR] under iid normal H0 (Lo 2002): Var(SR) ≈ 1/(T-1) — but Lopez-de-Prado
    # simplifies SR_0 with V=1 unitless. Use unit V; scaling handled in DSR formula.
    sqrt_v = 1.0  # standard form (single-scale stat)

    # SR_0: expected max Sharpe under N independent random strategies (Bonferroni-like)
    sr_0 = sqrt_v * (
        (1.0 - EULER_MASCHERONI) * norm.ppf(1.0 - 1.0 / n_trials)
        + EULER_MASCHERONI * norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    )

    # Numerator: (SR - SR_0) * sqrt(T-1)
    num = (observed_sharpe - sr_0) * math.sqrt(T - 1)

    # Denominator: sqrt(1 - skew*SR + (kurt-1)/4 * SR^2)  (Lo 2002 / Mertens 2002)
    denom_sq = 1.0 - skew * observed_sharpe + ((kurt - 1.0) / 4.0) * observed_sharpe**2
    if denom_sq <= 0:
        # Degenerate (extreme skew/kurt) → DSR undefined → return 0 (most conservative)
        return 0.0
    denom = math.sqrt(denom_sq)

    z = num / denom
    return float(norm.cdf(z))


def calmar_ratio(
    daily_pnl: np.ndarray | pd.Series,
    initial_capital: float,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calmar ratio = annualised return / max drawdown (absolute %).

    Calmar > 0.5 = Phase 1 Pro 閾值 (per `feedback_pro_research_standard.md`).

    Logic:
        cum_pnl = cumulative sum of daily_pnl (TWD)
        equity = initial_capital + cum_pnl
        max_dd_pct = max((peak - trough) / peak) over equity curve
        annual_return_pct = (sum(daily_pnl) / initial_capital) * (periods_per_year / T)
        calmar = annual_return_pct / max_dd_pct

    Args:
        daily_pnl: 1D array-like daily PnL in TWD.
        initial_capital: starting capital in TWD (must be > 0).
        periods_per_year: annualisation (default 252 trading).

    Returns:
        Calmar ratio (positive = good; negative = losing strategy).

    Raises:
        ValueError: max_dd == 0 (策略沒虧過 → Calmar 無意義) / initial_capital <= 0 / len < 2.
    """
    arr = np.asarray(daily_pnl, dtype=np.float64)
    if arr.size < 2:
        raise ValueError(f"calmar_ratio: daily_pnl length must be >= 2, got {arr.size}")
    if initial_capital <= 0:
        raise ValueError(f"calmar_ratio: initial_capital must be > 0, got {initial_capital}")

    cum_pnl = np.cumsum(arr)
    equity = initial_capital + cum_pnl
    # Include initial_capital as peak 0 (R9 P2 紀律 — max_drawdown 含初始 0 peak)
    full_equity = np.concatenate([[initial_capital], equity])
    running_peak = np.maximum.accumulate(full_equity)
    drawdown = (running_peak - full_equity) / running_peak
    max_dd = float(np.max(drawdown))

    if max_dd == 0:
        raise ValueError(
            "calmar_ratio: max_drawdown == 0 (策略一直賺沒虧過 — Calmar 無意義); "
            "use raw return for evaluation"
        )

    total_return_pct = float(np.sum(arr)) / initial_capital
    n_periods = arr.size
    annual_return_pct = total_return_pct * (periods_per_year / n_periods)

    return annual_return_pct / max_dd
