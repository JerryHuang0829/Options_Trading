"""Tests for src/options/greeks.py (Merton form, per-year theta).

Layer 2 SOP cross-checks:
  - test_greeks_matches_py_vollib: 5 Greeks vs py_vollib analytical with
    proper unit conversion (delta/gamma direct; vega/rho * 0.01; theta * 365)
  - test_greeks_boundaries (Merton form):
        call delta in [0, exp(-qT)], put delta in [-exp(-qT), 0]
        gamma >= 0, vega >= 0
        theta sign NOT asserted (high q + deep ITM call can be positive)
  - test_greek_symmetry: call_delta - put_delta ≈ exp(-qT)  (same K, T, q)
  - test_theta_finite_difference: closed-form vs (BSM(T-dt) - BSM(T)) / dt
    with dt = 1/365; tol < 1e-2 (fd one-step backward is O(dt) accurate)
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from py_vollib.black_scholes_merton.greeks.analytical import (
    delta as pv_delta,
)
from py_vollib.black_scholes_merton.greeks.analytical import (
    gamma as pv_gamma,
)
from py_vollib.black_scholes_merton.greeks.analytical import (
    rho as pv_rho,
)
from py_vollib.black_scholes_merton.greeks.analytical import (
    theta as pv_theta,
)
from py_vollib.black_scholes_merton.greeks.analytical import (
    vega as pv_vega,
)

from src.options.greeks import delta, gamma, rho, theta, vega
from src.options.pricing import bsm_price


def _flag(option_type: str) -> str:
    return "c" if option_type == "call" else "p"


def test_greeks_matches_py_vollib() -> None:
    """5 Greeks vs py_vollib with documented unit conversions.

    py_vollib units:
      - delta / gamma:  per 1.0 (no conversion)
      - theta:          per-day-calendar-365  (my * 1 == pv * 365)
      - vega:           per 1% sigma           (my * 0.01 == pv)
      - rho:            per 1% rate            (my * 0.01 == pv)
    """
    rng = np.random.default_rng(seed=46)
    for _ in range(30):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.7, 1.3)
        T = rng.uniform(7 / 365, 180 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.10, 0.50)
        opt = "call" if rng.uniform() > 0.5 else "put"
        f = _flag(opt)

        # delta / gamma: direct compare
        assert abs(delta(S, K, T, r, q, sigma, opt) - pv_delta(f, S, K, T, r, sigma, q)) < 1e-8
        assert abs(gamma(S, K, T, r, q, sigma) - pv_gamma(f, S, K, T, r, sigma, q)) < 1e-8

        # vega: my per 1.0; pv per 1%
        assert abs(vega(S, K, T, r, q, sigma) * 0.01 - pv_vega(f, S, K, T, r, sigma, q)) < 1e-8

        # theta: my per-year; pv per-day-calendar-365
        assert (
            abs(theta(S, K, T, r, q, sigma, opt) - pv_theta(f, S, K, T, r, sigma, q) * 365) < 1e-8
        )

        # rho: my per 1.0; pv per 1%
        assert abs(rho(S, K, T, r, q, sigma, opt) * 0.01 - pv_rho(f, S, K, T, r, sigma, q)) < 1e-8


def test_greeks_boundaries() -> None:
    """Merton-form bounds on delta / gamma / vega; theta sign NOT asserted."""
    rng = np.random.default_rng(seed=47)
    for _ in range(100):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.5, 1.5)
        T = rng.uniform(1 / 365, 365 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.05, 0.60)

        e_qT_bound = math.exp(-q * T)
        eps = 1e-12

        d_call = delta(S, K, T, r, q, sigma, "call")
        d_put = delta(S, K, T, r, q, sigma, "put")
        g = gamma(S, K, T, r, q, sigma)
        v = vega(S, K, T, r, q, sigma)

        assert -eps <= d_call <= e_qT_bound + eps, f"call delta {d_call} out of [0, {e_qT_bound}]"
        assert -e_qT_bound - eps <= d_put <= eps, f"put delta {d_put} out of [-{e_qT_bound}, 0]"
        assert g >= -eps, f"gamma {g} negative"
        assert v >= -eps, f"vega {v} negative"
        # theta sign not asserted — Merton with q > r can flip sign on deep ITM call.


def test_greek_symmetry() -> None:
    """call_delta - put_delta == exp(-qT)  (same K, T, q)."""
    rng = np.random.default_rng(seed=48)
    for _ in range(50):
        S = rng.uniform(50.0, 200.0)
        K = S * rng.uniform(0.7, 1.3)
        T = rng.uniform(7 / 365, 180 / 365)
        r = rng.uniform(0.0, 0.05)
        q = rng.uniform(0.0, 0.05)
        sigma = rng.uniform(0.10, 0.50)

        d_call = delta(S, K, T, r, q, sigma, "call")
        d_put = delta(S, K, T, r, q, sigma, "put")
        diff = (d_call - d_put) - math.exp(-q * T)
        assert abs(diff) < 1e-12, f"symmetry broken: diff={diff}"


def test_theta_finite_difference() -> None:
    """Backward FD per-year ≈ closed-form theta (one-step O(dt) ~ 1% accuracy).

    fd_theta = (BSM(T - dt) - BSM(T)) / dt   with dt = 1/365.

    NOTE: O(dt) backward difference has ~1% truncation error vs closed form.
    Tighter tol would require central difference (BSM(T+dt) - BSM(T-dt)) / (2*dt).
    """
    rng = np.random.default_rng(seed=49)
    dt = 1.0 / 365.0
    for _ in range(30):
        S = rng.uniform(80.0, 120.0)
        K = S * rng.uniform(0.85, 1.15)
        T = rng.uniform(30 / 365, 120 / 365)
        r = rng.uniform(0.005, 0.04)
        q = rng.uniform(0.005, 0.04)
        sigma = rng.uniform(0.15, 0.35)
        opt = "call" if rng.uniform() > 0.5 else "put"

        my_theta = theta(S, K, T, r, q, sigma, opt)
        # Backward FD (per-year): rate of change as T decreases by dt year.
        p0 = bsm_price(S, K, T, r, q, sigma, opt)
        p1 = bsm_price(S, K, T - dt, r, q, sigma, opt)
        fd_theta = (p1 - p0) / dt
        rel_diff = abs(my_theta - fd_theta) / max(abs(my_theta), 1.0)
        assert rel_diff < 0.02, (
            f"theta fd vs closed-form rel_diff={rel_diff:.4f} > 2%, "
            f"my={my_theta:.4f}, fd={fd_theta:.4f}, "
            f"S={S}, K={K}, T={T}, r={r}, q={q}, sigma={sigma}, type={opt}"
        )


def test_greeks_validation_errors() -> None:
    """Invalid inputs raise ValueError (delegates to _validate_inputs / Greeks-specific)."""
    with pytest.raises(ValueError, match="T=0"):
        delta(100.0, 100.0, 0.0, 0.01, 0.03, 0.2, "call")
    with pytest.raises(ValueError, match="sigma=0"):
        gamma(100.0, 100.0, 0.1, 0.01, 0.03, 0.0)
    with pytest.raises(ValueError, match="option_type"):
        delta(100.0, 100.0, 0.1, 0.01, 0.03, 0.2, "cal")
