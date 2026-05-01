"""Tests for src/options/pricing.py (BSM-Merton + implied_vol).

Layer 2 SOP cross-checks:
  - test_bsm_matches_py_vollib: 50-sample (S, K, T, r, q, sigma, type)
    vs py_vollib.black_scholes_merton, tol < 1e-8
  - test_put_call_parity (Merton form):
        C - P ≈ S * exp(-qT) - K * exp(-rT) (Merton)  within 1e-6
  - test_no_arbitrage_bounds: lower bound = max(S*e^(-qT) - K*e^(-rT), 0);
    upper bound = S*e^(-qT) for call (parallel for put)
  - test_implied_vol_inverse: round-trip σ → price → IV → σ within 1e-6
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from py_vollib.black_scholes_merton import black_scholes_merton as pv_bsm

from src.options.pricing import bsm_price, implied_vol


# Shared sample generator (deterministic; seeded).
def _random_inputs(rng: np.random.Generator, n: int) -> list[dict]:
    samples = []
    for _ in range(n):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.7, 1.3)  # 30% ITM/OTM either side
        T = rng.uniform(7 / 365, 180 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.10, 0.50)
        option_type = rng.choice(["call", "put"])
        samples.append(dict(S=S, K=K, T=T, r=r, q=q, sigma=sigma, option_type=str(option_type)))
    return samples


def test_bsm_matches_py_vollib() -> None:
    """50 random samples vs py_vollib reference; tol < 1e-8 (Merton form)."""
    rng = np.random.default_rng(seed=42)
    samples = _random_inputs(rng, n=50)
    max_diff = 0.0
    for s in samples:
        flag = "c" if s["option_type"] == "call" else "p"
        my = bsm_price(s["S"], s["K"], s["T"], s["r"], s["q"], s["sigma"], s["option_type"])
        pv = pv_bsm(flag, s["S"], s["K"], s["T"], s["r"], s["sigma"], s["q"])
        diff = abs(my - pv)
        max_diff = max(max_diff, diff)
        assert diff < 1e-8, f"diff={diff:.2e} for {s}"
    # Ensure samples actually exercised the path (not all 0 by accident).
    assert max_diff > 0


def test_put_call_parity() -> None:
    """Same K/T: C - P ≈ S * exp(-qT) - K * exp(-rT) (Merton parity)."""
    rng = np.random.default_rng(seed=43)
    for _ in range(50):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.8, 1.2)
        T = rng.uniform(7 / 365, 180 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.10, 0.50)

        c = bsm_price(S, K, T, r, q, sigma, "call")
        p = bsm_price(S, K, T, r, q, sigma, "put")
        lhs = c - p
        rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
        diff = abs(lhs - rhs)
        assert diff < 1e-6, f"parity broken: lhs={lhs}, rhs={rhs}, diff={diff:.2e}"


def test_no_arbitrage_bounds() -> None:
    """Merton-form lower / upper bounds; deep-ITM call may dip below intrinsic
    (Merton dividend drag) but must stay above max(S·e^(-qT) - K·e^(-rT), 0).
    """
    rng = np.random.default_rng(seed=44)
    for _ in range(100):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.5, 1.5)
        T = rng.uniform(1 / 365, 365 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.05, 0.60)

        disc_S = S * math.exp(-q * T)
        disc_K = K * math.exp(-r * T)

        c = bsm_price(S, K, T, r, q, sigma, "call")
        c_lower = max(disc_S - disc_K, 0.0)
        c_upper = disc_S
        assert c >= c_lower - 1e-10, f"call < lower: c={c}, lower={c_lower}"
        assert c <= c_upper + 1e-10, f"call > upper: c={c}, upper={c_upper}"

        p = bsm_price(S, K, T, r, q, sigma, "put")
        p_lower = max(disc_K - disc_S, 0.0)
        p_upper = disc_K
        assert p >= p_lower - 1e-10, f"put < lower: p={p}, lower={p_lower}"
        assert p <= p_upper + 1e-10, f"put > upper: p={p}, upper={p_upper}"


def test_implied_vol_inverse() -> None:
    """σ → price → IV solve → σ' ; expect |σ' - σ| < 1e-6."""
    rng = np.random.default_rng(seed=45)
    for _ in range(30):
        S = rng.uniform(80.0, 120.0)
        K = S * rng.uniform(0.85, 1.15)
        T = rng.uniform(14 / 365, 90 / 365)
        r = rng.uniform(0.005, 0.04)
        q = rng.uniform(0.005, 0.04)
        sigma = rng.uniform(0.10, 0.40)
        option_type = "call" if rng.uniform() > 0.5 else "put"

        price = bsm_price(S, K, T, r, q, sigma, option_type)
        if price < 1e-6:
            # Skip near-zero prices where IV is ill-defined.
            continue

        recovered = implied_vol(price, S, K, T, r, q, option_type)
        diff = abs(recovered - sigma)
        assert diff < 1e-6, (
            f"IV round-trip failed: σ={sigma:.6f}, recovered={recovered:.6f}, "
            f"diff={diff:.2e}, price={price:.6f}, S={S}, K={K}, T={T}, r={r}, q={q}, "
            f"type={option_type}"
        )


def test_validation_errors() -> None:
    """Edge case: invalid inputs raise ValueError."""
    with pytest.raises(ValueError, match="S must be > 0"):
        bsm_price(-1.0, 100.0, 0.1, 0.01, 0.03, 0.2)
    with pytest.raises(ValueError, match="K must be > 0"):
        bsm_price(100.0, 0.0, 0.1, 0.01, 0.03, 0.2)
    with pytest.raises(ValueError, match="T must be >= 0"):
        bsm_price(100.0, 100.0, -0.1, 0.01, 0.03, 0.2)
    with pytest.raises(ValueError, match="sigma must be >= 0"):
        bsm_price(100.0, 100.0, 0.1, 0.01, 0.03, -0.1)
    with pytest.raises(ValueError, match="option_type must be"):
        bsm_price(100.0, 100.0, 0.1, 0.01, 0.03, 0.2, option_type="cal")


def test_intrinsic_at_expiry() -> None:
    """T == 0 returns spot intrinsic value (option exercised at expiry)."""
    assert bsm_price(110.0, 100.0, 0.0, 0.01, 0.03, 0.2, "call") == 10.0
    assert bsm_price(90.0, 100.0, 0.0, 0.01, 0.03, 0.2, "call") == 0.0
    assert bsm_price(110.0, 100.0, 0.0, 0.01, 0.03, 0.2, "put") == 0.0
    assert bsm_price(90.0, 100.0, 0.0, 0.01, 0.03, 0.2, "put") == 10.0


def test_implied_vol_below_noise_floor_raises() -> None:
    """R5 P2: tiny price above no-arb lower bound → ValueError, not silent 0.05."""
    # Deep OTM put: S=100, K=50, true_sigma=0.4 → price ≈ 1e-9 (below 1e-4 floor).
    S, K, T, r, q = 100.0, 50.0, 30 / 365, 0.015, 0.0
    true_sigma = 0.4
    price = bsm_price(S, K, T, r, q, true_sigma, "put")
    assert price < 1e-4  # adversarial setup — tiny price
    with pytest.raises(ValueError, match="below noise floor|not identifiable"):
        implied_vol(price, S, K, T, r, q, "put")


def test_implied_vol_relative_tolerance_rejects_initial_guess() -> None:
    """R5 P2: small price must not silently accept the Brenner-Subrahmanyam guess.

    For a price safely above the noise floor but still small enough that
    abs(diff) < 1e-8 absolute would be misleading, the relative-tolerance
    branch in the Newton-Raphson loop must drive convergence.
    """
    # Construct a moderately-OTM put at S=100, K=85, T=30/365, sigma=0.30.
    S, K, T, r, q = 100.0, 85.0, 30 / 365, 0.015, 0.02
    true_sigma = 0.30
    price = bsm_price(S, K, T, r, q, true_sigma, "put")
    assert price > 1e-4
    recovered = implied_vol(price, S, K, T, r, q, "put")
    assert abs(recovered - true_sigma) < 1e-6, (
        f"expected sigma≈{true_sigma}, recovered {recovered}; relative tolerance branch failed"
    )


def test_zero_vol_forward_intrinsic_with_T_positive() -> None:
    """sigma == 0 + T > 0: BSM-Merton zero-vol limit = forward intrinsic.

    Must continuous match sigma → 0 limit (Codex R4 P1).
    """
    S, K, T, r, q = 100.0, 99.0, 30 / 365, 0.015, 0.035
    disc_S = S * math.exp(-q * T)
    disc_K = K * math.exp(-r * T)
    expected_call = max(disc_S - disc_K, 0.0)
    expected_put = max(disc_K - disc_S, 0.0)

    actual_call = bsm_price(S, K, T, r, q, 0.0, "call")
    actual_put = bsm_price(S, K, T, r, q, 0.0, "put")
    assert abs(actual_call - expected_call) < 1e-15, (
        f"sigma=0 call: actual={actual_call}, expected={expected_call} (forward intrinsic)"
    )
    assert abs(actual_put - expected_put) < 1e-15

    # Continuity: sigma=0 ≈ sigma=1e-12 (both should give forward intrinsic).
    tiny_call = bsm_price(S, K, T, r, q, 1e-12, "call")
    assert abs(actual_call - tiny_call) < 1e-9, (
        f"discontinuity: sigma=0 → {actual_call}, sigma=1e-12 → {tiny_call}"
    )


def test_implied_vol_at_lower_bound_returns_zero() -> None:
    """price at no-arb lower bound (= forward intrinsic) → IV = 0 exactly.

    Codex R4 P1: previously Brent bracket [1e-6, 5.0] returned a small
    non-zero IV like 0.0057 instead of recognising the boundary.
    """
    S, K, T, r, q = 100.0, 99.0, 30 / 365, 0.015, 0.035
    # Forward-intrinsic lower bound for call.
    lower = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    iv = implied_vol(lower, S, K, T, r, q, "call")
    assert iv == 0.0, f"price at lower bound should give IV=0, got {iv}"
