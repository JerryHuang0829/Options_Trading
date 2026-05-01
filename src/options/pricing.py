"""Black-Scholes-Merton (BSM) pricing and implied volatility solver.

Closed-form BSM-Merton with continuous dividend yield ``q``. TXO underlying
TAIEX is a price index (not total-return), constituents pay cash dividends
on ex-dates causing scheduled index drops. Plain BSM (q=0) systematically
biases ATM delta and breaks put-call parity by ~q*T·S.

Formulas (Merton 1973)::

    d1 = ( ln(S/K) + (r - q + sigma^2 / 2) * T ) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    Call = S * exp(-q*T) * N(d1)  -  K * exp(-r*T) * N(d2)
    Put  = K * exp(-r*T) * N(-d2) -  S * exp(-q*T) * N(-d1)

Put-Call parity (Merton form)::

    C - P = S * exp(-q*T) - K * exp(-r*T)

Cross-validated against ``py_vollib.black_scholes_merton`` to floating-point
precision (diff ~1e-15 for ATM, < 1e-8 for full sample). See
``tests/options/test_pricing.py::test_bsm_matches_py_vollib``.

All time inputs are in years (e.g. 30/365 = 0.0822 for 30-DTE).
"""

from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm

_SQRT_2PI = math.sqrt(2 * math.pi)


def _validate_inputs(S: float, K: float, T: float, sigma: float, option_type: str) -> None:
    """Validate non-negative / positive constraints + closed-set option_type."""
    if S <= 0:
        raise ValueError(f"S must be > 0, got {S!r}")
    if K <= 0:
        raise ValueError(f"K must be > 0, got {K!r}")
    if T < 0:
        raise ValueError(f"T must be >= 0, got {T!r}")
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma!r}")
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


def _intrinsic(S: float, K: float, option_type: str) -> float:
    """Spot intrinsic value at expiry (T=0)."""
    if option_type == "call":
        return max(S - K, 0.0)
    return max(K - S, 0.0)


def _forward_intrinsic(S: float, K: float, T: float, r: float, q: float, option_type: str) -> float:
    """Discounted forward intrinsic — the BSM-Merton zero-vol limit (T > 0).

    As ``sigma → 0`` with ``T > 0``, BSM-Merton collapses to::

        Call → max(S·e^(-qT) - K·e^(-rT), 0)
        Put  → max(K·e^(-rT) - S·e^(-qT), 0)

    This is NOT the same as spot intrinsic ``max(S - K, 0)`` because the
    Merton model discounts both the spot (by dividends) and the strike (by
    risk-free rate). Codex R4 P1: previous implementation collapsed sigma=0
    to spot intrinsic creating a discontinuity vs sigma=1e-12.
    """
    disc_S = S * math.exp(-q * T)
    disc_K = K * math.exp(-r * T)
    if option_type == "call":
        return max(disc_S - disc_K, 0.0)
    return max(disc_K - disc_S, 0.0)


def _d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    """BSM-Merton d1 / d2 helper (callers must ensure T > 0, sigma > 0)."""
    sigma_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    return d1, d2


def bsm_price(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Black-Scholes-Merton European option price with continuous dividend yield.

    Args:
        S: Spot price of the underlying index (TAIEX for TXO).
        K: Strike price.
        T: Time to expiry in years (e.g. 30/365 for 30-DTE).
        r: Annualised risk-free rate (decimal; e.g. 0.015).
        q: Annualised continuous dividend yield (decimal; e.g. 0.035 for TAIEX).
        sigma: Annualised volatility (decimal; e.g. 0.20).
        option_type: ``"call"`` or ``"put"``.

    Returns:
        Theoretical option price in the same units as ``S``.

    Raises:
        ValueError: If S/K <= 0, T < 0, sigma < 0, or option_type invalid.

    Notes:
        Boundary cases T == 0 or sigma == 0 collapse to intrinsic value
        (no time value). Deep ITM call may be below intrinsic when q > r
        (Merton dividend drag), which is correct model behaviour, not a bug.
    """
    _validate_inputs(S, K, T, sigma, option_type)

    # Boundary cases:
    #   T == 0  → spot intrinsic (option at expiry, no time / vol matters)
    #   sigma==0, T>0 → forward intrinsic (Merton zero-vol limit; ≠ spot intrinsic)
    if T == 0:
        return _intrinsic(S, K, option_type)
    if sigma == 0:
        return _forward_intrinsic(S, K, T, r, q, option_type)

    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    discounted_S = S * math.exp(-q * T)
    discounted_K = K * math.exp(-r * T)

    if option_type == "call":
        return discounted_S * norm.cdf(d1) - discounted_K * norm.cdf(d2)
    return discounted_K * norm.cdf(-d2) - discounted_S * norm.cdf(-d1)


def _vega(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Vega for the IV solver (module-private; intentional duplicate of greeks.vega).

    Per-1.0-sigma vega::

        vega = S * exp(-q*T) * phi(d1) * sqrt(T)

    where ``phi`` is the standard normal PDF.

    Why duplicate (not import from greeks.py)::
        ``src.options.greeks`` imports ``_d1_d2`` and ``_validate_inputs`` from
        this module. Importing back ``vega`` from ``greeks`` would create a
        circular import. Resolving cleanly would require extracting shared
        internals to ``src/options/_internal.py``, which is plan-外 scope.
        The body is 3 lines; DRY cost is acceptable.
    """
    if T == 0 or sigma == 0:
        return 0.0
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T)


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    option_type: str = "call",
) -> float:
    """Implied volatility via Newton-Raphson on ``bsm_price``, Brent's fallback.

    Initial guess uses Brenner-Subrahmanyam ATM approximation::

        sigma_0 = sqrt(2*pi/T) * (price / S)

    Newton-Raphson iterates with vega derivative. If the iteration diverges
    or vega becomes too small (deep OTM/ITM regions), falls back to
    ``scipy.optimize.brentq`` on ``[1e-6, 5.0]``.

    Args:
        price: Observed market price of the option.
        S, K, T, r, q: Standard BSM-Merton inputs (see ``bsm_price``).
        option_type: ``"call"`` or ``"put"``.

    Returns:
        Implied volatility (annualised decimal).

    Raises:
        ValueError: If price violates no-arbitrage bounds, T == 0 with
            non-intrinsic price, or both Newton-Raphson and Brent fail.
    """
    _validate_inputs(S, K, T, 1.0, option_type)
    if price < 0:
        raise ValueError(f"price must be >= 0, got {price!r}")

    # Edge: T == 0 → IV undefined (price must equal intrinsic).
    if T == 0:
        intrinsic = _intrinsic(S, K, option_type)
        if abs(price - intrinsic) < 1e-10:
            return 0.0
        raise ValueError(f"At T=0 price must equal intrinsic ({intrinsic}), got {price}")

    # No-arbitrage bound check (Merton form).
    discounted_S = S * math.exp(-q * T)
    discounted_K = K * math.exp(-r * T)
    if option_type == "call":
        lower = max(discounted_S - discounted_K, 0.0)
        upper = discounted_S
    else:
        lower = max(discounted_K - discounted_S, 0.0)
        upper = discounted_K
    tol_arb = 1e-10
    if price < lower - tol_arb or price > upper + tol_arb:
        raise ValueError(f"price {price} outside no-arbitrage bounds [{lower:.6f}, {upper:.6f}]")

    # Edge: price at forward-intrinsic lower bound → IV is exactly 0
    # (not a small positive IV from Brent bracket [1e-6, 5.0]; Codex R4 P1).
    if abs(price - lower) < tol_arb:
        return 0.0

    # Codex R5 P2: tiny prices above the lower bound carry no IV signal
    # (1 cent / 1e-3 of underlying noise floor swamps the small premium).
    # Refuse to silently return the initial guess; let caller decide whether
    # to drop the strike or use a model fallback. Threshold = max(1e-4 abs,
    # 1e-7 * S relative) — captures both 1-tick noise and 1bp-of-spot noise.
    excess = price - lower
    noise_floor = max(1e-4, 1e-7 * S)
    if 0 < excess < noise_floor:
        raise ValueError(
            f"implied_vol: price excess over no-arb lower bound = {excess:.3e} is "
            f"below noise floor {noise_floor:.3e}; IV not identifiable "
            f"(price={price}, lower={lower:.6e}, S={S}, K={K}, T={T})"
        )

    # Newton-Raphson with Brenner-Subrahmanyam initial guess. The noise-floor
    # check above guarantees price has signal beyond the 1e-4 threshold, so an
    # absolute tolerance of 1e-8 is safe (R5 P2 silent-accept of initial guess
    # is only possible when the price itself is sub-noise — already rejected).
    sigma = max(_SQRT_2PI / math.sqrt(T) * price / S, 0.05)
    max_iter = 100
    tol_abs = 1e-8
    for _ in range(max_iter):
        bsm = bsm_price(S, K, T, r, q, sigma, option_type)
        diff = bsm - price
        if abs(diff) < tol_abs:
            return sigma
        v = _vega(S, K, T, r, q, sigma)
        if v < 1e-12:
            break  # vega vanishing → Newton unstable, fall back.
        sigma = sigma - diff / v
        if sigma <= 0:
            sigma = 1e-6  # clamp; will likely break out via tol or to brent.

    # Fallback: Brent on bracket [1e-6, 5.0].
    def _objective(sig: float) -> float:
        return bsm_price(S, K, T, r, q, sig, option_type) - price

    try:
        return brentq(_objective, 1e-6, 5.0, xtol=1e-8, maxiter=200)
    except (ValueError, RuntimeError) as exc:
        raise ValueError(
            f"implied_vol failed to converge: price={price}, S={S}, K={K}, "
            f"T={T}, r={r}, q={q}, option_type={option_type}: {exc}"
        ) from exc
