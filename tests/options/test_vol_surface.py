"""Tests for src/options/vol_surface.py — Week 4 Day 1-3 (SVI / SABR / poly).

21 tests (R11.7 + R11.8 + R11.9 + Day 2 SABR + Day 3 polynomial + 3-tier orchestration):

SVI raw form (Day 1, 11 tests):
  1. test_fit_svi_raw_recovers_known_params (round-trip on synthetic SVI smile)
  2. test_fit_svi_raw_arb_free_constraint_active (Lee 2004 b upper bound respected)
  3. test_fit_svi_raw_rejects_invalid_input (negative / T<=0 / shape / few points)
  4. test_fit_svi_raw_rejects_non_finite_input (R11.7 P1: NaN/Inf in either array)
  5. test_fit_svi_raw_in_sample_rmse_under_threshold (synthetic V-shape RMSE < 0.02)
  6. test_butterfly_arb_indicator_zero_density_violation (g(k) < 0 detection)
  7. test_butterfly_arb_indicator_rejects_negative_w (R11.7 P2: w<=0 raise 不 mask)
  8. test_fit_svi_raw_rejects_non_finite_T (R11.8 P1: T NaN/Inf raise)
  9. test_fit_svi_raw_rejects_non_finite_initial_guess (R11.8 P1: 5 param finite + domain)
 10. test_butterfly_arb_indicator_rejects_non_finite_inputs (R11.8 P2: k+5 param finite + domain)
 11. test_butterfly_arb_indicator_accepts_list_tuple_input (R11.9 P2: k_arr 一致性)

SABR β=1 lognormal (Day 2, 4 tests, Hagan 2002 §2.17a):
 12. test_sabr_lognormal_iv_atm_limit (K=F → IV ≈ alpha, z/x(z) → 1)
 13. test_fit_sabr_recovers_known_params (round-trip on synthetic SABR smile)
 14. test_fit_sabr_in_sample_rmse_under_threshold (synthetic V-shape RMSE < 0.005)
 15. test_sabr_rejects_invalid_input (NaN/Inf K/F/T/params + domain + beta != 1)

Polynomial degree-2 (Day 3, 3 tests):
 16. test_fit_smile_polynomial_recovers_known_params (round-trip σ = a+b·k+c·k²)
 17. test_fit_smile_polynomial_rejects_invalid_input (NaN/Inf/iv≤0/<3 points)
 18. test_fit_smile_polynomial_in_sample_rmse_under_threshold (V-shape noisy)

3-tier orchestration `fit_with_fallback` (Day 3, 3 tests, R11.6 P5 #4):
 19. test_fit_with_fallback_svi_first (healthy SVI → model_type='svi'; attempts len=1)
 20. test_fit_with_fallback_falls_to_poly_when_few_points (n=3 points → SVI fail → SABR fail → poly)
 21. test_fit_with_fallback_audit_attempts_log (model_type / attempts / total_fit_time_ms 都齊)
"""

from __future__ import annotations

import numpy as np
import pytest

from src.options.vol_surface import (
    butterfly_arb_indicator,
    fit_sabr,
    fit_smile_polynomial,
    fit_svi_raw,
    fit_with_fallback,
    sabr_lognormal_iv,
    svi_raw,
)


def _generate_svi_smile(
    a: float = 0.04,
    b: float = 0.4,
    rho: float = -0.4,
    m: float = 0.0,
    sigma: float = 0.1,
    n_strikes: int = 21,
    k_range: tuple[float, float] = (-0.3, 0.3),
) -> tuple[np.ndarray, np.ndarray]:
    """Generate ground-truth SVI smile (k, w) for round-trip tests."""
    k = np.linspace(k_range[0], k_range[1], n_strikes)
    w = svi_raw(k, a, b, rho, m, sigma)
    return k, w


# ---------------------------------------------------------------------------
# Test 1: SVI raw form round-trip — recover known params from synthetic smile
# ---------------------------------------------------------------------------


def test_fit_svi_raw_recovers_known_params() -> None:
    """Synthetic SVI smile with known (a, b, ρ, m, σ) → fit recovers within tol."""
    a_true, b_true, rho_true, m_true, sigma_true = 0.04, 0.4, -0.3, 0.05, 0.1
    k, w = _generate_svi_smile(a_true, b_true, rho_true, m_true, sigma_true, n_strikes=31)

    result = fit_svi_raw(k, w, T=30 / 365.0, arb_free=True)

    assert result.converged
    # Fit IV residual should be ~0 for round-trip (ground-truth smile)
    assert result.in_sample_rmse < 0.005, f"round-trip RMSE too high: {result.in_sample_rmse:.4f}"
    # Re-evaluate fit param vs ground truth — SVI is non-identifiable so loose tol
    # but reconstructed w(k) must match closely (already verified by RMSE)
    w_pred = svi_raw(k, result.a, result.b, result.rho, result.m, result.sigma)
    np.testing.assert_allclose(w_pred, w, atol=1e-3)


# ---------------------------------------------------------------------------
# Test 2: arb_free constraint active — Lee 2004 b upper bound 守線
# ---------------------------------------------------------------------------


def test_fit_svi_raw_arb_free_constraint_active() -> None:
    """arb_free=True 時 b ≤ 4/(T·(1+|ρ|)) 必滿足。"""
    a_true, b_true, rho_true, m_true, sigma_true = 0.04, 0.5, -0.4, 0.0, 0.1
    k, w = _generate_svi_smile(a_true, b_true, rho_true, m_true, sigma_true)
    T = 30 / 365.0

    result = fit_svi_raw(k, w, T=T, arb_free=True)

    # Lee 2004: b <= 4 / (T * (1 + |rho|))
    lee_upper = 4.0 / (T * (1.0 + abs(result.rho)))
    assert result.b <= lee_upper + 1e-6, (
        f"Lee 2004 violated: b={result.b:.4f} > 4/(T·(1+|ρ|))={lee_upper:.4f}"
    )
    # |ρ| < 1
    assert abs(result.rho) < 1.0
    # σ > 0
    assert result.sigma > 0
    # a + b·σ·sqrt(1-ρ²) ≥ 0 (positivity at minimum)
    positivity = result.a + result.b * result.sigma * np.sqrt(1.0 - result.rho**2)
    assert positivity >= -1e-9, f"positivity at min: {positivity}"


# ---------------------------------------------------------------------------
# Test 3: rejects non-finite / negative total_var / shape mismatch
# ---------------------------------------------------------------------------


def test_fit_svi_raw_rejects_invalid_input() -> None:
    """Negative total_var / T<=0 / shape mismatch / too few points → raise."""
    k_ok = np.linspace(-0.2, 0.2, 10)
    w_ok = 0.04 + 0.05 * k_ok**2

    # negative total_var
    w_bad = w_ok.copy()
    w_bad[3] = -0.01
    with pytest.raises(ValueError, match="total_var must be"):
        fit_svi_raw(k_ok, w_bad, T=30 / 365.0)

    # T <= 0
    with pytest.raises(ValueError, match="T must be"):
        fit_svi_raw(k_ok, w_ok, T=0.0)

    # shape mismatch
    with pytest.raises(ValueError, match="shape mismatch"):
        fit_svi_raw(k_ok, w_ok[:5], T=30 / 365.0)

    # too few points
    with pytest.raises(ValueError, match="≥5"):
        fit_svi_raw(k_ok[:3], w_ok[:3], T=30 / 365.0)


def test_fit_svi_raw_rejects_non_finite_input() -> None:
    """R11.7 P1 修法：NaN/Inf in log_moneyness OR total_var → raise.

    Codex 抓 silent NaN/Inf 污染：原版只檢查 negative/T/shape/few-points，
    未驗 finite → fit 回 NaN params 帶進 cache / fallback pipeline.
    """
    k_ok = np.linspace(-0.2, 0.2, 10)
    w_ok = 0.04 + 0.05 * k_ok**2

    # NaN in total_var
    w_nan = w_ok.copy()
    w_nan[3] = np.nan
    with pytest.raises(ValueError, match="total_var must be finite"):
        fit_svi_raw(k_ok, w_nan, T=30 / 365.0)

    # Inf in total_var
    w_inf = w_ok.copy()
    w_inf[3] = np.inf
    with pytest.raises(ValueError, match="total_var must be finite"):
        fit_svi_raw(k_ok, w_inf, T=30 / 365.0)

    # NaN in log_moneyness
    k_nan = k_ok.copy()
    k_nan[3] = np.nan
    with pytest.raises(ValueError, match="log_moneyness must be finite"):
        fit_svi_raw(k_nan, w_ok, T=30 / 365.0)

    # Inf in log_moneyness
    k_inf = k_ok.copy()
    k_inf[3] = np.inf
    with pytest.raises(ValueError, match="log_moneyness must be finite"):
        fit_svi_raw(k_inf, w_ok, T=30 / 365.0)


def test_fit_svi_raw_rejects_non_finite_T() -> None:
    """R11.8 P1: T=NaN/Inf → raise (原版只檢 T<=0 漏 finite)."""
    k = np.linspace(-0.2, 0.2, 10)
    w = 0.04 + 0.05 * k**2
    for T_bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="T must be finite"):
            fit_svi_raw(k, w, T=T_bad)


def test_fit_svi_raw_rejects_non_finite_initial_guess() -> None:
    """R11.8 P1: initial_guess 5 param 任一 NaN/Inf → raise；domain violate raise。"""
    k = np.linspace(-0.2, 0.2, 10)
    w = 0.04 + 0.05 * k**2
    base = {"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1}

    # NaN / Inf in any param
    for key in ("a", "b", "rho", "m", "sigma"):
        for bad in (float("nan"), float("inf")):
            ig = {**base, key: bad}
            with pytest.raises(ValueError, match="not finite"):
                fit_svi_raw(k, w, T=30 / 365.0, initial_guess=ig)

    # Domain violations
    with pytest.raises(ValueError, match="sigma.*> 0"):
        fit_svi_raw(k, w, T=30 / 365.0, initial_guess={**base, "sigma": -0.1})
    with pytest.raises(ValueError, match="rho.*<1"):
        fit_svi_raw(k, w, T=30 / 365.0, initial_guess={**base, "rho": 1.5})
    with pytest.raises(ValueError, match="b.*≥ 0"):
        fit_svi_raw(k, w, T=30 / 365.0, initial_guess={**base, "b": -0.1})


def test_butterfly_arb_indicator_rejects_non_finite_inputs() -> None:
    """R11.8 P2: butterfly 對 k / a / b / rho / m / sigma NaN/Inf → raise；
    sigma<=0 / |rho|>=1 / b<0 → raise (domain check)."""
    k_ok = np.array([-0.1, 0.0, 0.1])
    base = {"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1}

    # k 含 NaN/Inf
    for k_bad_val in (float("nan"), float("inf")):
        k_bad = np.array([-0.1, k_bad_val, 0.1])
        with pytest.raises(ValueError, match="k must be finite"):
            butterfly_arb_indicator(k_bad, **base)

    # 5 個 param 任一 NaN/Inf
    for key in ("a", "b", "rho", "m", "sigma"):
        for bad in (float("nan"), float("inf")):
            params = {**base, key: bad}
            with pytest.raises(ValueError, match=f"param {key}"):
                butterfly_arb_indicator(k_ok, **params)

    # Domain
    with pytest.raises(ValueError, match="sigma must be"):
        butterfly_arb_indicator(k_ok, **{**base, "sigma": -0.1})
    with pytest.raises(ValueError, match="rho.*< 1"):
        butterfly_arb_indicator(k_ok, **{**base, "rho": 1.5})
    with pytest.raises(ValueError, match="b must be"):
        butterfly_arb_indicator(k_ok, **{**base, "b": -0.1})


def test_butterfly_arb_indicator_accepts_list_tuple_input() -> None:
    """R11.9 P2: k 是 list/tuple 不該 TypeError (k_arr 一致性).

    原版 line 117 把 k 轉 k_arr，但 line 140-142 又用 k → list/tuple 後段
    會 raise TypeError("unsupported operand type(s) for -: 'list' and 'float'").
    修後全段用 k_arr。
    """
    base = {"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1}
    expected_g = butterfly_arb_indicator(np.array([-0.1, 0.0, 0.1]), **base)
    # list input
    g_list = butterfly_arb_indicator([-0.1, 0.0, 0.1], **base)
    np.testing.assert_allclose(g_list, expected_g, atol=1e-12)
    # tuple input
    g_tuple = butterfly_arb_indicator((-0.1, 0.0, 0.1), **base)
    np.testing.assert_allclose(g_tuple, expected_g, atol=1e-12)


def test_butterfly_arb_indicator_rejects_negative_w() -> None:
    """R11.7 P2 修法：w(k) ≤ 0 (positivity-at-min violated) → raise，不 mask 偽通過。

    原版 w_safe = where(w > 1e-12, w, 1e-12) 把 negative-w 點 mask 掉，導致對
    illegal SVI params (a 太負) 也回 finite g → 假通過 arb-free check.
    """
    k = np.array([-0.1, 0.0, 0.1])
    # a = -0.1 + b=0 → w(k) = -0.1 (constant negative)
    with pytest.raises(ValueError, match="w\\(k\\) ≤ 0"):
        butterfly_arb_indicator(k, a=-0.1, b=0.0, rho=-0.3, m=0.0, sigma=0.1)


# ---------------------------------------------------------------------------
# Test 4: in-sample RMSE on V-shape smile under threshold
# ---------------------------------------------------------------------------


def test_fit_svi_raw_in_sample_rmse_under_threshold() -> None:
    """Synthetic noisy V-shape smile → RMSE < 0.02 vol points (Pro 驗收 threshold 0.05 寬)."""
    rng = np.random.default_rng(seed=42)
    T = 30 / 365.0
    k = np.linspace(-0.25, 0.25, 25)
    # V-shape: σ_iv(k) = 0.18 + 0.4 * k^2 + small noise
    sigma_iv = 0.18 + 0.4 * k**2 + rng.normal(scale=0.005, size=k.size)
    w = sigma_iv**2 * T  # total var

    result = fit_svi_raw(k, w, T=T, arb_free=True)

    assert result.converged
    assert result.in_sample_rmse < 0.02, f"in-sample RMSE too high: {result.in_sample_rmse:.4f}"


# ---------------------------------------------------------------------------
# Test 5: butterfly arb indicator detects density violation
# ---------------------------------------------------------------------------


def test_butterfly_arb_indicator_zero_density_violation() -> None:
    """g(k) < 0 → 違反 risk-neutral density ≥ 0 (butterfly arb).

    構造一個 narrow-σ + steep-rho SVI 在 wing 處 violates butterfly arb，
    驗 indicator catches it (g(k) < 0 in the wing).
    """
    # σ 太小 + rho 接近 -1 → wing 收斂太快 → density 在 wing 變負
    # Lee bound: b ≤ 4/(T·(1+|ρ|)). T=30/365, ρ=-0.95 → b_max ≈ 1.02
    # 設 b 接近上限 + sigma 極小 → wing 有 arb
    k = np.linspace(-0.5, 0.5, 51)
    g = butterfly_arb_indicator(k, a=0.001, b=2.0, rho=-0.99, m=0.0, sigma=0.001)
    # 至少有一處 g < 0 (wing 違反 arb)
    assert (g < 0).any(), (
        f"narrow-sigma + extreme-rho should violate butterfly arb; got g.min={g.min():.4f}"
    )

    # 對照組: 合理 SVI 參數 g >= 0 across grid
    g_ok = butterfly_arb_indicator(k, a=0.04, b=0.4, rho=-0.3, m=0.0, sigma=0.1)
    assert (g_ok >= -1e-6).all(), (
        f"healthy SVI should be butterfly arb-free; got g.min={g_ok.min():.4f}"
    )


# ===========================================================================
# Day 2: SABR β=1 lognormal (Hagan 2002 §2.17a)
# ===========================================================================


def test_sabr_lognormal_iv_atm_limit() -> None:
    """K=F (ATM): z=0, z/x(z)→1，所以 σ_B(F,F) ≈ alpha · (1 + T·correction)."""
    F = 17500.0
    T = 30 / 365.0
    alpha, rho, nu = 0.20, -0.3, 0.5
    iv_atm = sabr_lognormal_iv(F, F, T, alpha, rho, nu, beta=1.0)
    # ATM correction: (rho·nu·alpha)/4 + (2-3·rho²)·nu²/24
    correction = 1.0 + T * (rho * nu * alpha / 4.0 + (2.0 - 3.0 * rho**2) * nu**2 / 24.0)
    expected = alpha * correction
    np.testing.assert_allclose(iv_atm, expected, atol=1e-10)


def test_fit_sabr_recovers_known_params() -> None:
    """Synthetic SABR smile (alpha, rho, nu known) → fit recovers within tol."""
    F = 17500.0
    T = 30 / 365.0
    alpha_true, rho_true, nu_true = 0.20, -0.4, 0.6
    strikes = np.linspace(15500.0, 19500.0, 21)
    ivs = sabr_lognormal_iv(strikes, F, T, alpha_true, rho_true, nu_true, beta=1.0)

    result = fit_sabr(strikes, ivs, forward=F, T=T, beta=1.0)

    assert result.converged
    assert result.in_sample_rmse < 1e-6, f"round-trip RMSE too high: {result.in_sample_rmse}"
    # Re-evaluate fit IV vs ground truth
    iv_pred = sabr_lognormal_iv(strikes, F, T, result.alpha, result.rho, result.nu, beta=1.0)
    np.testing.assert_allclose(iv_pred, ivs, atol=1e-5)


def test_fit_sabr_in_sample_rmse_under_threshold() -> None:
    """Synthetic noisy SABR-like smile → RMSE < 0.01 vol points."""
    rng = np.random.default_rng(seed=42)
    F = 17500.0
    T = 60 / 365.0
    alpha_true, rho_true, nu_true = 0.18, -0.35, 0.55
    strikes = np.linspace(14000.0, 21000.0, 25)
    ivs_clean = sabr_lognormal_iv(strikes, F, T, alpha_true, rho_true, nu_true, beta=1.0)
    ivs_noisy = ivs_clean + rng.normal(scale=0.003, size=strikes.size)

    result = fit_sabr(strikes, ivs_noisy, forward=F, T=T, beta=1.0)

    assert result.converged
    assert result.in_sample_rmse < 0.01, f"in-sample RMSE too high: {result.in_sample_rmse:.4f}"


def test_sabr_rejects_invalid_input() -> None:
    """SABR raise on NaN/Inf/domain violations: K/F/T/alpha/rho/nu/beta + shape."""
    K_ok = np.array([16000.0, 17500.0, 19000.0, 20000.0])
    iv_ok = np.array([0.22, 0.20, 0.21, 0.23])

    # NaN K
    with pytest.raises(ValueError, match="strikes must be finite"):
        fit_sabr(np.array([16000.0, np.nan, 19000.0, 20000.0]), iv_ok, forward=17500.0, T=30 / 365)
    # Inf iv
    with pytest.raises(ValueError, match="ivs must be finite"):
        fit_sabr(K_ok, np.array([0.22, np.inf, 0.21, 0.23]), forward=17500.0, T=30 / 365)
    # K <= 0
    with pytest.raises(ValueError, match="strikes must be > 0"):
        fit_sabr(np.array([-100.0, 17500.0, 19000.0, 20000.0]), iv_ok, forward=17500.0, T=30 / 365)
    # iv <= 0
    with pytest.raises(ValueError, match="ivs must be > 0"):
        fit_sabr(K_ok, np.array([0.22, 0.0, 0.21, 0.23]), forward=17500.0, T=30 / 365)
    # F <= 0
    with pytest.raises(ValueError, match="forward must be"):
        fit_sabr(K_ok, iv_ok, forward=-1.0, T=30 / 365)
    # T NaN
    with pytest.raises(ValueError, match="T must be finite"):
        fit_sabr(K_ok, iv_ok, forward=17500.0, T=float("nan"))
    # beta != 1.0
    with pytest.raises(ValueError, match="only β=1"):
        fit_sabr(K_ok, iv_ok, forward=17500.0, T=30 / 365, beta=0.5)
    # too few points
    with pytest.raises(ValueError, match="≥4"):
        fit_sabr(K_ok[:3], iv_ok[:3], forward=17500.0, T=30 / 365)
    # shape mismatch
    with pytest.raises(ValueError, match="shape mismatch"):
        fit_sabr(K_ok, iv_ok[:3], forward=17500.0, T=30 / 365)

    # initial_guess domain (alpha <= 0)
    with pytest.raises(ValueError, match=r"initial_guess\[alpha\] must be"):
        fit_sabr(
            K_ok,
            iv_ok,
            forward=17500.0,
            T=30 / 365,
            initial_guess={"alpha": -0.1, "rho": -0.3, "nu": 0.5},
        )
    # initial_guess |rho| >= 1
    with pytest.raises(ValueError, match=r"initial_guess\[rho\] must satisfy"):
        fit_sabr(
            K_ok,
            iv_ok,
            forward=17500.0,
            T=30 / 365,
            initial_guess={"alpha": 0.2, "rho": 1.5, "nu": 0.5},
        )
    # initial_guess nu < 0
    with pytest.raises(ValueError, match=r"initial_guess\[nu\] must be"):
        fit_sabr(
            K_ok,
            iv_ok,
            forward=17500.0,
            T=30 / 365,
            initial_guess={"alpha": 0.2, "rho": -0.3, "nu": -0.1},
        )

    # sabr_lognormal_iv 直接呼叫的 NaN/Inf
    with pytest.raises(ValueError, match="K must be finite"):
        sabr_lognormal_iv(np.array([16000.0, np.nan, 19000.0]), 17500.0, 30 / 365, 0.2, -0.3, 0.5)
    with pytest.raises(ValueError, match="alpha must be"):
        sabr_lognormal_iv(K_ok, 17500.0, 30 / 365, alpha=-0.1, rho=-0.3, nu=0.5)
    with pytest.raises(ValueError, match=r"\|rho\| must be"):
        sabr_lognormal_iv(K_ok, 17500.0, 30 / 365, alpha=0.2, rho=1.5, nu=0.5)
    with pytest.raises(ValueError, match="nu must be"):
        sabr_lognormal_iv(K_ok, 17500.0, 30 / 365, alpha=0.2, rho=-0.3, nu=-0.1)


# ===========================================================================
# Day 3: Polynomial degree-2 fallback
# ===========================================================================


def test_fit_smile_polynomial_recovers_known_params() -> None:
    """σ(k) = a + b·k + c·k² 已知 params → polyfit 恢復 ±1e-10."""
    a_true, b_true, c_true = 0.20, 0.05, 0.30
    k = np.linspace(-0.25, 0.25, 21)
    iv = a_true + b_true * k + c_true * k**2
    result = fit_smile_polynomial(k, iv)
    assert result.converged
    assert abs(result.a - a_true) < 1e-10
    assert abs(result.b - b_true) < 1e-10
    assert abs(result.c - c_true) < 1e-10
    assert result.in_sample_rmse < 1e-12


def test_fit_smile_polynomial_rejects_invalid_input() -> None:
    """NaN/Inf/iv≤0/<3 points/shape mismatch raise."""
    k_ok = np.linspace(-0.2, 0.2, 5)
    iv_ok = 0.20 + 0.05 * k_ok**2

    with pytest.raises(ValueError, match="ivs must be finite"):
        fit_smile_polynomial(k_ok, np.array([0.20, np.nan, 0.21, 0.22, 0.23]))
    with pytest.raises(ValueError, match="log_moneyness must be finite"):
        fit_smile_polynomial(np.array([-0.2, np.inf, 0.0, 0.1, 0.2]), iv_ok)
    with pytest.raises(ValueError, match="ivs must be > 0"):
        fit_smile_polynomial(k_ok, np.array([0.20, 0.0, 0.21, 0.22, 0.23]))
    with pytest.raises(ValueError, match="≥3"):
        fit_smile_polynomial(k_ok[:2], iv_ok[:2])
    with pytest.raises(ValueError, match="shape mismatch"):
        fit_smile_polynomial(k_ok, iv_ok[:3])


def test_fit_smile_polynomial_in_sample_rmse_under_threshold() -> None:
    """Noisy V-shape → degree-2 fit RMSE < 0.01 vol points."""
    rng = np.random.default_rng(seed=42)
    k = np.linspace(-0.25, 0.25, 25)
    iv = 0.18 + 0.4 * k**2 + rng.normal(scale=0.005, size=k.size)
    result = fit_smile_polynomial(k, iv)
    assert result.converged
    assert result.in_sample_rmse < 0.01, f"RMSE: {result.in_sample_rmse}"


# ===========================================================================
# Day 3: 3-tier orchestration fit_with_fallback (R11.6 P5 prereq #4)
# ===========================================================================


def test_fit_with_fallback_svi_first() -> None:
    """Healthy synthetic SVI smile → model_type='svi'; attempts len=1 (no fallback)."""
    a, b, rho, m, sigma = 0.04, 0.4, -0.3, 0.05, 0.1
    k = np.linspace(-0.3, 0.3, 25)
    T = 30 / 365.0
    w = svi_raw(k, a, b, rho, m, sigma)
    iv = np.sqrt(w / T)
    F = 17500.0
    result = fit_with_fallback(log_moneyness=k, ivs=iv, forward=F, T=T, arb_free_svi=True)
    assert result.model_type == "svi"
    assert result.converged
    assert len(result.attempts) == 1
    assert result.attempts[0]["model_type"] == "svi"
    assert result.attempts[0]["converged"] is True


def test_fit_with_fallback_falls_to_poly_when_few_points() -> None:
    """N=3 points → SVI 需 ≥5 fail → SABR 需 ≥4 fail → polynomial (≥3) 接住."""
    k = np.array([-0.1, 0.0, 0.1])
    iv = np.array([0.22, 0.20, 0.21])
    F = 17500.0
    T = 30 / 365.0
    result = fit_with_fallback(log_moneyness=k, ivs=iv, forward=F, T=T)
    assert result.model_type == "poly"
    assert result.converged
    # 3 attempts: SVI fail (≥5 raise) / SABR fail (≥4 raise) / poly success
    assert len(result.attempts) == 3
    assert result.attempts[0]["model_type"] == "svi"
    assert result.attempts[0]["error"] is not None
    assert result.attempts[1]["model_type"] == "sabr"
    assert result.attempts[1]["error"] is not None
    assert result.attempts[2]["model_type"] == "poly"
    assert result.attempts[2]["converged"] is True


def test_fit_with_fallback_audit_attempts_log() -> None:
    """R11.6 P5 prereq #4: model_type / attempts / total_fit_time_ms 完整 audit log."""
    a, b, rho, m, sigma = 0.04, 0.4, -0.3, 0.0, 0.1
    k = np.linspace(-0.3, 0.3, 25)
    T = 30 / 365.0
    iv = np.sqrt(svi_raw(k, a, b, rho, m, sigma) / T)
    result = fit_with_fallback(log_moneyness=k, ivs=iv, forward=17500.0, T=T)
    # Audit fields 完整
    assert result.model_type in {"svi", "sabr", "poly", "all_failed"}
    assert isinstance(result.attempts, list)
    for att in result.attempts:
        assert {"model_type", "converged", "rmse", "error"} <= set(att.keys())
    assert isinstance(result.total_fit_time_ms, int)
    assert result.total_fit_time_ms >= 0
    assert result.n_points == k.size
