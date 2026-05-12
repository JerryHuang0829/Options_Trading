"""Closed-form Greeks for Black-Scholes-Merton European options.

All Greeks include continuous dividend yield ``q`` (Merton 1973 form). TXO
underlying TAIEX is a price index — using plain BSM (q=0) systematically
biases delta and breaks put-call parity.

Return conventions::

    delta:  per 1.0 spot move          (call ∈ [0, e^(-qT)]; put ∈ [-e^(-qT), 0])
    gamma:  per 1.0 spot move squared  (always ≥ 0)
    theta:  per-year calendar          (negative for most long positions; can be
                                        positive for deep ITM call when q > r)
    vega:   per 1.0 sigma move         (always ≥ 0)
    rho:    per 1.0 rate move          (call > 0; put < 0)

Greek identifiers use English in code (``delta``); narrative text uses Greek
letters (Δ, Γ, Θ, ν, ρ).

py_vollib unit conversion (cross-check):
  - delta / gamma:  ``my == pv`` (no conversion)
  - theta:          ``my == pv * 365`` (py_vollib per-day-calendar-365)
  - vega:           ``my * 0.01 == pv`` (py_vollib per 1% sigma)
  - rho:            ``my * 0.01 == pv`` (py_vollib per 1% rate)

Closed-form formulas (Merton)::

    Call Δ = exp(-qT) * N(d1)
    Put  Δ = exp(-qT) * (N(d1) - 1)
    Γ     = exp(-qT) * φ(d1) / (S * sigma * sqrt(T))
    ν     = S * exp(-qT) * φ(d1) * sqrt(T)
    Call Θ = -S*exp(-qT)*φ(d1)*sigma / (2*sqrt(T))
              - r*K*exp(-rT)*N(d2) + q*S*exp(-qT)*N(d1)
    Put  Θ = -S*exp(-qT)*φ(d1)*sigma / (2*sqrt(T))
              + r*K*exp(-rT)*N(-d2) - q*S*exp(-qT)*N(-d1)
    Call ρ =  K*T*exp(-rT)*N(d2)
    Put  ρ = -K*T*exp(-rT)*N(-d2)

where φ = standard normal PDF, N = standard normal CDF.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from src.options.pricing import _d1_d2, _validate_inputs


def _validate_greek_inputs(
    S: float, K: float, T: float, sigma: float, option_type: str | None = None
) -> None:
    """Greeks require strictly positive T and sigma (no closed form at T=0)."""
    _validate_inputs(S, K, T, sigma, option_type or "call")
    if T == 0:
        raise ValueError("Greeks undefined at T=0 (collapse to indicator/Dirac)")
    if sigma == 0:
        raise ValueError("Greeks undefined at sigma=0 (Dirac at K)")


def delta(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Δ = ∂V/∂S. Per 1.0 spot move; Merton-form with q discount factor.

    Bounds: call delta ∈ [0, exp(-qT)]; put delta ∈ [-exp(-qT), 0].
    """
    _validate_greek_inputs(S, K, T, sigma, option_type)
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    e_qT = math.exp(-q * T)
    if option_type == "call":
        return e_qT * norm.cdf(d1)
    return e_qT * (norm.cdf(d1) - 1.0)


def gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Γ = ∂²V/∂S². Same for call and put; always ≥ 0."""
    _validate_greek_inputs(S, K, T, sigma)
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    return math.exp(-q * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))


def vega(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """ν = ∂V/∂σ. **Per 1.0 sigma move** (not per 1%); always ≥ 0.

    For per-1% (retail / py_vollib) intuition: ``vega(...) * 0.01``.
    """
    _validate_greek_inputs(S, K, T, sigma)
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T)


def theta(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Θ = ∂V/∂t. **Per-year calendar** (multiply by 1/365 for per-day decay).

    Sign NOT asserted as invariant: deep ITM call with q > r can have
    positive theta (dividend benefit > time decay). Use finite-difference
    or py_vollib cross-check (note py_vollib is per-day-calendar-365).
    """
    _validate_greek_inputs(S, K, T, sigma, option_type)
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    e_qT = math.exp(-q * T)
    e_rT = math.exp(-r * T)
    common = -S * e_qT * norm.pdf(d1) * sigma / (2.0 * math.sqrt(T))
    if option_type == "call":
        return common - r * K * e_rT * norm.cdf(d2) + q * S * e_qT * norm.cdf(d1)
    return common + r * K * e_rT * norm.cdf(-d2) - q * S * e_qT * norm.cdf(-d1)


def rho(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """ρ = ∂V/∂r. **Per 1.0 rate move** (not per 1%).

    Call rho > 0 (higher r → higher forward → higher call price).
    Put rho < 0.

    For per-1% (retail / py_vollib) intuition: ``rho(...) * 0.01``.
    """
    _validate_greek_inputs(S, K, T, sigma, option_type)
    _, d2 = _d1_d2(S, K, T, r, q, sigma)
    e_rT = math.exp(-r * T)
    if option_type == "call":
        return K * T * e_rT * norm.cdf(d2)
    return -K * T * e_rT * norm.cdf(-d2)
