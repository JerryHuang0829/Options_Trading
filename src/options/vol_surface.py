"""Vol surface fitting (Week 4 Day 1 — SVI raw form + Lee 2004 arb-free).

D-soft pivot 核心：把 60% NaN bid/ask rows 用 model price 補滿 → 100% markable
→ Week 6+ 真 backtest 才有資格跑可發表 Sharpe.

Module 範圍 (Week 4 Day 1):
  - SVI raw form 5-param fit per (date, expiry):
        w(k) = a + b{ρ(k-m) + sqrt((k-m)² + σ²)}
    where w = total variance (σ² × T), k = log-moneyness (ln(K/F)).
  - Arbitrage-free constraints (Gatheral & Jacquier 2014, "Arbitrage-free SVI
    volatility surfaces"; Lee 2004 strict bounds):
      * butterfly: g(k) = (1 - k·w'(k)/(2·w(k)))²
                   - (w'(k)/2)² · (1/w(k) + 1/4)
                   + w''(k)/2 ≥ 0  for all k
      * 0 ≤ b ≤ 4/(T·(1+|ρ|))    (Roger Lee moment formula)
      * |ρ| < 1
      * σ > 0
      * a + b·σ·sqrt(1-ρ²) ≥ 0   (positivity at minimum)

Day 2 (本 module): SABR β=1 lognormal fallback (Hagan, Kumar, Lesniewski,
Woodward 2002 "Managing Smile Risk", Wilmott Magazine, eq. 2.17a; β=1 case
σ_B(K, F, T) = α · (z/x(z)) · {1 + T·[(ρ·ν·α)/4 + (2-3·ρ²)·ν²/24]}
where z = (ν/α)·log(F/K), x(z) = log[(√(1-2ρz+z²) + z - ρ) / (1-ρ)].
ATM (K=F): z=0, limit z/x(z) → 1, σ_B(F, F) ≈ α·(1 + T·corrections).
3 free params (α, ρ, ν) when β=1 (fixed for index option lognormal).

Day 3 (本 module): polynomial degree-2 fallback + 3-tier orchestration
`fit_with_fallback` (SVI → SABR → polynomial)。每 fit 紀錄 `model_type` audit
回 SmileFitResult — 對應 Codex R11.6 P5 prerequisite #4「3-tier silent reason
audit」。Polynomial 是 last-resort：σ(k) = a + b·k + c·k²，degree-2 因 R11.6
plan v2 決議（避 over-fit；no-arb wing 不可控）。

Codex R11.6 P 修法 acknowledged:
  - Fit universe filter 在 caller 端做 (見 Week 4 Day 1.5 plan); 本 module
    假設 caller 已過濾 (bid/ask>0 / spread cap / volume gate / OTM-only)
  - Pro 驗收矩陣 (R11.6 P5) 留給 batch fit script 計算 (spread-IV-RMSE /
    butterfly arb grid / temporal drift RMSE; R11.15 P4 改名 from OOS holdout
    — 嚴格 OOS validation 留 Week 6+ 真 backtest)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import minimize


@dataclass(frozen=True)
class SVIFitResult:
    """SVI raw form fit output.

    Attributes:
        a, b, rho, m, sigma: 5 SVI raw form params.
        converged: optimizer converged (no constraint violation, residual finite).
        in_sample_rmse: in-sample IV RMSE (vol points, not total var).
        butterfly_arb_free: butterfly arb-free across observed strikes (post-fit
            check; full grid check 留給 batch fit script).
        n_points: number of (k, w) pairs used in fit.
        fit_time_ms: optimizer wall-clock ms.
    """

    a: float
    b: float
    rho: float
    m: float
    sigma: float
    converged: bool
    in_sample_rmse: float
    butterfly_arb_free: bool
    n_points: int
    fit_time_ms: int = 0
    extras: dict = field(default_factory=dict)


def svi_raw(k: np.ndarray, a: float, b: float, rho: float, m: float, sigma: float) -> np.ndarray:
    """SVI raw form total variance:
        w(k) = a + b·{ρ·(k-m) + sqrt((k-m)² + σ²)}

    Args:
        k: log-moneyness array (ln(K/F)).
        a, b, rho, m, sigma: 5 SVI raw form params (sigma > 0; |rho| < 1; b ≥ 0).

    Returns:
        Total variance array w(k); convert to vol via sigma_iv = sqrt(w/T).
    """
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma**2))


def _svi_first_derivative(
    k: np.ndarray, b: float, rho: float, m: float, sigma: float
) -> np.ndarray:
    """w'(k) = b·{ρ + (k-m)/sqrt((k-m)² + σ²)}."""
    return b * (rho + (k - m) / np.sqrt((k - m) ** 2 + sigma**2))


def _svi_second_derivative(k: np.ndarray, b: float, m: float, sigma: float) -> np.ndarray:
    """w''(k) = b·σ² / ((k-m)² + σ²)^(3/2)."""
    return b * sigma**2 / ((k - m) ** 2 + sigma**2) ** 1.5


def butterfly_arb_indicator(
    k: np.ndarray | list | tuple,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> np.ndarray:
    """Gatheral 2014 g(k) butterfly arbitrage indicator.

    g(k) ≥ 0 across all k → no butterfly arbitrage. Zero density violation
    occurs where g(k) < 0.

    Codex R11.7 P2 修法：原版用 ``w_safe = np.where(w > 1e-12, w, 1e-12)`` 把
    w ≤ 0 的點 mask 掉 → 對直接餵 negative-w 的 ill-formed SVI params 反而回
    finite 正 g (假通過 arb-free check)。改成：w ≤ 0 視為 invalid params →
    raise，caller 必須先確認 SVI a + b·σ·sqrt(1-ρ²) ≥ 0 才呼叫.

    Codex R11.8 P2 修法：加 finite + domain check 對 k / a / b / rho / m /
    sigma — 原版只檢 w ≤ 0，對 NaN/Inf k 或 NaN/Inf params 仍 silent 回 NaN g.

    Returns: g(k) array; check ``(g >= 0).all()`` for arb-free.

    Raises:
        ValueError: k 含 NaN/Inf；params 含 NaN/Inf；sigma ≤ 0；|rho| ≥ 1；
            b < 0；w(k) ≤ 0 at any k (positivity-at-min 違反).
    """
    k_arr = np.asarray(k, dtype=np.float64)
    if not np.isfinite(k_arr).all():
        n_bad = int((~np.isfinite(k_arr)).sum())
        raise ValueError(
            f"butterfly_arb_indicator: k must be finite (no NaN/Inf); got {n_bad} non-finite points"
        )
    for name, val in [("a", a), ("b", b), ("rho", rho), ("m", m), ("sigma", sigma)]:
        if not math.isfinite(val):
            raise ValueError(f"butterfly_arb_indicator: param {name}={val} is not finite (NaN/Inf)")
    if sigma <= 0:
        raise ValueError(f"butterfly_arb_indicator: sigma must be > 0, got {sigma}")
    if abs(rho) >= 1.0:
        raise ValueError(f"butterfly_arb_indicator: |rho| must be < 1, got {rho}")
    if b < 0:
        raise ValueError(f"butterfly_arb_indicator: b must be ≥ 0, got {b}")

    w = svi_raw(k_arr, a, b, rho, m, sigma)
    if (w <= 0).any():
        raise ValueError(
            f"butterfly_arb_indicator: w(k) ≤ 0 at {(w <= 0).sum()} points "
            f"(SVI params violate positivity-at-min: a + b·σ·sqrt(1-ρ²) ≥ 0). "
            f"min(w)={float(w.min()):.6f}; cannot compute butterfly indicator."
        )
    # R11.9 P2 修法 (Codex 抓 k_arr 不一致): line 117 把 k → k_arr (np.asarray)
    # 但 line 140-142 又用原始 k → list/tuple input TypeError. 全改 k_arr 對齊.
    w_prime = _svi_first_derivative(k_arr, b, rho, m, sigma)
    w_double_prime = _svi_second_derivative(k_arr, b, m, sigma)
    term1 = (1.0 - k_arr * w_prime / (2.0 * w)) ** 2
    term2 = (w_prime / 2.0) ** 2 * (1.0 / w + 0.25)
    term3 = w_double_prime / 2.0
    return term1 - term2 + term3


def _initial_guess(log_moneyness: np.ndarray, total_var: np.ndarray) -> dict[str, float]:
    """Heuristic initial params from Brent-style fit on synthetic V-shape:

    a    = min(w) - 0.001       (small positive offset)
    b    = (max(w) - min(w)) / max(|k|)  (slope)
    rho  = -0.5                 (typical equity index put skew)
    m    = k at min(w)          (smile minimum location)
    sigma= 0.1                  (smoothness, small initial)
    """
    return {
        "a": max(float(total_var.min()) - 1e-3, 1e-6),
        "b": max(
            (float(total_var.max()) - float(total_var.min()))
            / max(float(np.abs(log_moneyness).max()), 1e-3),
            1e-3,
        ),
        "rho": -0.5,
        "m": float(log_moneyness[total_var.argmin()]),
        "sigma": 0.1,
    }


def fit_svi_raw(
    log_moneyness: np.ndarray,
    total_var: np.ndarray,
    *,
    T: float,
    arb_free: bool = True,
    initial_guess: dict[str, float] | None = None,
    method: Literal["SLSQP", "trust-constr"] = "SLSQP",
    max_iter: int = 200,
) -> SVIFitResult:
    """Fit SVI raw form to (log-moneyness, total variance) data.

    Args:
        log_moneyness: k = ln(K/F), shape (N,).
        total_var: w = sigma_iv² × T, shape (N,).
        T: time-to-expiry in years (used for Lee 2004 b upper bound).
        arb_free: if True, enforce Lee 2004 / Gatheral 2014 constraints
            (b upper bound + |ρ|<1 + σ>0 + a + b·σ·sqrt(1-ρ²) ≥ 0).
        initial_guess: 5-param dict or None (use heuristic).
        method: scipy.optimize.minimize method ("SLSQP" supports inequality
            constraints, fast for small problems).
        max_iter: optimizer max iterations.

    Returns:
        SVIFitResult with 5 params + convergence + in-sample RMSE +
        butterfly_arb_free post-fit check.

    Raises:
        ValueError: shape mismatch / < 5 points / negative total_var / T ≤ 0 /
            **non-finite (NaN / Inf) in input arrays** (R11.7 P1) /
            initial_guess missing required keys.
    """
    log_moneyness = np.asarray(log_moneyness, dtype=np.float64)
    total_var = np.asarray(total_var, dtype=np.float64)
    if log_moneyness.shape != total_var.shape:
        raise ValueError(
            f"shape mismatch: log_moneyness={log_moneyness.shape} vs total_var={total_var.shape}"
        )
    if log_moneyness.size < 5:
        raise ValueError(f"fit_svi_raw needs ≥5 (k, w) points (5 params); got {log_moneyness.size}")
    # R11.7 P1 修法 (Codex 抓 silent NaN/Inf 污染): 加 finite check 在 negative/T
    # check 前面，因為 NaN/Inf 對 < 0 的比較 short-circuit 為 False，會繞過
    # downstream gate.
    if not np.isfinite(log_moneyness).all():
        n_bad = int((~np.isfinite(log_moneyness)).sum())
        raise ValueError(
            f"log_moneyness must be finite (no NaN/Inf); got {n_bad} non-finite points"
        )
    if not np.isfinite(total_var).all():
        n_bad = int((~np.isfinite(total_var)).sum())
        raise ValueError(f"total_var must be finite (no NaN/Inf); got {n_bad} non-finite points")
    if (total_var < 0).any():
        raise ValueError(f"total_var must be ≥ 0 (w = σ²·T); got min={total_var.min()}")
    # R11.8 P1 修法 (Codex 抓 T=NaN/Inf silent pass): finite check 跟 ≤0 一起
    if not math.isfinite(T) or T <= 0:
        raise ValueError(f"T must be finite and > 0, got T={T}")

    guess = initial_guess if initial_guess is not None else _initial_guess(log_moneyness, total_var)
    required_keys = {"a", "b", "rho", "m", "sigma"}
    if set(guess.keys()) != required_keys:
        raise ValueError(
            f"initial_guess must have keys {sorted(required_keys)}, got {sorted(guess.keys())}"
        )
    # R11.8 P1 修法 (Codex 抓 initial_guess=NaN/Inf silent pass):
    # finite + domain check 對 5 個 param.
    for key, val in guess.items():
        if not math.isfinite(val):
            raise ValueError(f"initial_guess[{key}]={val} is not finite (NaN/Inf)")
    if guess["sigma"] <= 0:
        raise ValueError(f"initial_guess[sigma] must be > 0, got {guess['sigma']}")
    if abs(guess["rho"]) >= 1.0:
        raise ValueError(f"initial_guess[rho] must satisfy |ρ|<1, got {guess['rho']}")
    if guess["b"] < 0:
        raise ValueError(f"initial_guess[b] must be ≥ 0, got {guess['b']}")

    x0 = np.array([guess["a"], guess["b"], guess["rho"], guess["m"], guess["sigma"]])

    def loss(params: np.ndarray) -> float:
        a, b, rho, m, sigma = params
        if sigma <= 0 or b < 0 or abs(rho) >= 1.0:
            return 1e10  # outside admissible region
        try:
            w_pred = svi_raw(log_moneyness, a, b, rho, m, sigma)
        except (FloatingPointError, ValueError):
            return 1e10
        residual = total_var - w_pred
        return float(np.sum(residual**2))

    constraints: list[dict] = []
    if arb_free:
        # Lee 2004 b upper bound: b ≤ 4 / (T · (1 + |ρ|))
        # 用 inequality: 4/(T·(1+|ρ|)) - b ≥ 0
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda p: 4.0 / (T * (1.0 + abs(p[2]))) - p[1],
            }
        )
        # |ρ| < 1: 1 - |ρ| - 1e-6 ≥ 0 (strict 改 1e-6 buffer)
        constraints.append({"type": "ineq", "fun": lambda p: 1.0 - abs(p[2]) - 1e-6})
        # σ > 0: σ - 1e-6 ≥ 0
        constraints.append({"type": "ineq", "fun": lambda p: p[4] - 1e-6})
        # b ≥ 0
        constraints.append({"type": "ineq", "fun": lambda p: p[1]})
        # Positivity at minimum: a + b·σ·sqrt(1-ρ²) ≥ 0
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda p: p[0] + p[1] * p[4] * math.sqrt(max(1.0 - p[2] ** 2, 0.0)),
            }
        )

    import time as _time

    t_start = _time.perf_counter()
    result = minimize(
        loss,
        x0,
        method=method,
        constraints=constraints if arb_free else (),
        options={"maxiter": max_iter, "ftol": 1e-10},
    )
    fit_time_ms = int((_time.perf_counter() - t_start) * 1000)

    a, b, rho, m, sigma = result.x
    w_pred = svi_raw(log_moneyness, a, b, rho, m, sigma)
    sigma_iv_pred = np.sqrt(np.maximum(w_pred, 0.0) / T)
    sigma_iv_obs = np.sqrt(np.maximum(total_var, 0.0) / T)
    in_sample_rmse = float(np.sqrt(np.mean((sigma_iv_pred - sigma_iv_obs) ** 2)))

    # Butterfly arb-free post-fit check on observed strikes
    g_values = butterfly_arb_indicator(log_moneyness, a, b, rho, m, sigma)
    butterfly_arb_free = bool((g_values >= -1e-9).all())

    return SVIFitResult(
        a=float(a),
        b=float(b),
        rho=float(rho),
        m=float(m),
        sigma=float(sigma),
        converged=bool(result.success),
        in_sample_rmse=in_sample_rmse,
        butterfly_arb_free=butterfly_arb_free,
        n_points=int(log_moneyness.size),
        fit_time_ms=fit_time_ms,
        extras={"optimizer_message": result.message, "loss_final": float(result.fun)},
    )


# ============================================================================
# SABR β=1 lognormal fallback (Day 2 — Hagan 2002 §2.17a)
# ============================================================================


@dataclass(frozen=True)
class SABRFitResult:
    """SABR Hagan 2002 β=1 lognormal fit output.

    Attributes:
        alpha: ATM-level vol (α > 0). For β=1, alpha ≈ ATM IV when T → 0.
        rho: spot-vol correlation, |ρ| < 1; ρ < 0 typical for equity index put skew.
        nu: vol-of-vol (ν ≥ 0); higher → steeper smile wings.
        beta: fixed skew exponent (default 1.0 for lognormal index option).
        converged: optimizer converged.
        in_sample_rmse: in-sample IV RMSE (vol points).
        n_points: (strike, iv) pair count used.
        fit_time_ms: optimizer wall-clock ms.
    """

    alpha: float
    rho: float
    nu: float
    beta: float
    converged: bool
    in_sample_rmse: float
    n_points: int
    fit_time_ms: int = 0
    extras: dict = field(default_factory=dict)


def sabr_lognormal_iv(
    K: np.ndarray | list | tuple | float,
    F: float,
    T: float,
    alpha: float,
    rho: float,
    nu: float,
    beta: float = 1.0,
) -> np.ndarray:
    """SABR Hagan 2002 lognormal IV expansion (β=1 case, eq. 2.17a).

    For β=1 (lognormal):
        σ_B(K, F, T) = α · (z / x(z)) · {1 + T·[(ρ·ν·α)/4 + (2-3·ρ²)·ν²/24]}
    where:
        z    = (ν/α) · log(F/K)
        x(z) = log[(√(1 - 2·ρ·z + z²) + z - ρ) / (1 - ρ)]

    ATM limit (K → F): z → 0, so z/x(z) → 1 by L'Hôpital.

    Args:
        K: strike(s); scalar or 1-D array-like.
        F: forward price (K, F same units).
        T: time-to-expiry in years.
        alpha, rho, nu: 3 SABR params (α>0, |ρ|<1, ν≥0).
        beta: fixed at 1.0 in this implementation (other β requires §2.17b/c forms).

    Returns:
        IV array (annualised vol; sqrt of variance).

    Raises:
        ValueError: K/F/T/params non-finite or domain violation; beta != 1.0.
    """
    K_arr = np.atleast_1d(np.asarray(K, dtype=np.float64))
    if not np.isfinite(K_arr).all():
        raise ValueError("sabr_lognormal_iv: K must be finite (no NaN/Inf)")
    if (K_arr <= 0).any():
        raise ValueError(f"sabr_lognormal_iv: K must be > 0 (lognormal); got min={K_arr.min()}")
    for name, val in [
        ("F", F),
        ("T", T),
        ("alpha", alpha),
        ("rho", rho),
        ("nu", nu),
        ("beta", beta),
    ]:
        if not math.isfinite(val):
            raise ValueError(f"sabr_lognormal_iv: param {name}={val} is not finite (NaN/Inf)")
    if F <= 0:
        raise ValueError(f"sabr_lognormal_iv: F must be > 0, got {F}")
    if T <= 0:
        raise ValueError(f"sabr_lognormal_iv: T must be > 0, got {T}")
    if alpha <= 0:
        raise ValueError(f"sabr_lognormal_iv: alpha must be > 0, got {alpha}")
    if abs(rho) >= 1.0:
        raise ValueError(f"sabr_lognormal_iv: |rho| must be < 1, got {rho}")
    if nu < 0:
        raise ValueError(f"sabr_lognormal_iv: nu must be ≥ 0, got {nu}")
    if abs(beta - 1.0) > 1e-9:
        raise ValueError(
            f"sabr_lognormal_iv: only β=1.0 (lognormal) implemented; got {beta}. "
            f"Other β requires Hagan eq. 2.17b/c."
        )

    log_FK = np.log(F / K_arr)
    # ATM limit: z=0 → z/x(z) = 1 (use mask)
    z = (nu / alpha) * log_FK
    # x(z) = log[(√(1 - 2ρz + z²) + z - ρ) / (1 - ρ)]
    discr = np.sqrt(1.0 - 2.0 * rho * z + z**2)
    x_z = np.log((discr + z - rho) / (1.0 - rho))
    # ATM mask: |z| < ε → ratio = 1.0
    eps = 1e-10
    ratio = np.where(np.abs(z) < eps, 1.0, z / np.where(np.abs(x_z) < eps, eps, x_z))

    # T-correction term (β=1 case)
    correction = 1.0 + T * (rho * nu * alpha / 4.0 + (2.0 - 3.0 * rho**2) * nu**2 / 24.0)
    return alpha * ratio * correction


def fit_sabr(
    strikes: np.ndarray | list | tuple,
    ivs: np.ndarray | list | tuple,
    *,
    forward: float,
    T: float,
    beta: float = 1.0,
    initial_guess: dict[str, float] | None = None,
    max_iter: int = 200,
) -> SABRFitResult:
    """Fit SABR β=1 lognormal (Hagan 2002) to (strikes, ivs) data.

    3 free params (α, ρ, ν) with β fixed (default 1.0 for index option).

    Args:
        strikes: K array; positive.
        ivs: observed IVs (vol points; annualised).
        forward: F.
        T: time-to-expiry years.
        beta: fixed at 1.0 (other β requires different formula).
        initial_guess: dict {alpha, rho, nu} or None (use heuristic).
        max_iter: optimizer max iterations.

    Returns:
        SABRFitResult.

    Raises:
        ValueError: shape mismatch / < 4 points / non-finite / domain violation.
    """
    strikes_arr = np.asarray(strikes, dtype=np.float64)
    ivs_arr = np.asarray(ivs, dtype=np.float64)
    if strikes_arr.shape != ivs_arr.shape:
        raise ValueError(
            f"fit_sabr: shape mismatch: strikes={strikes_arr.shape} vs ivs={ivs_arr.shape}"
        )
    if strikes_arr.size < 4:
        raise ValueError(
            f"fit_sabr: needs ≥4 (K, iv) points (3 params + 1 dof); got {strikes_arr.size}"
        )
    if not np.isfinite(strikes_arr).all():
        raise ValueError("fit_sabr: strikes must be finite")
    if not np.isfinite(ivs_arr).all():
        raise ValueError("fit_sabr: ivs must be finite")
    if (strikes_arr <= 0).any():
        raise ValueError("fit_sabr: strikes must be > 0")
    if (ivs_arr <= 0).any():
        raise ValueError("fit_sabr: ivs must be > 0")
    if not math.isfinite(forward) or forward <= 0:
        raise ValueError(f"fit_sabr: forward must be finite and > 0, got {forward}")
    if not math.isfinite(T) or T <= 0:
        raise ValueError(f"fit_sabr: T must be finite and > 0, got {T}")
    if abs(beta - 1.0) > 1e-9:
        raise ValueError(f"fit_sabr: only β=1.0 implemented, got {beta}")

    # Heuristic initial guess: ATM IV ≈ alpha; rho=-0.3 (equity put skew); nu=0.5
    if initial_guess is None:
        # 取最接近 ATM 的 IV 當 alpha
        atm_idx = int(np.argmin(np.abs(strikes_arr - forward)))
        guess = {"alpha": float(ivs_arr[atm_idx]), "rho": -0.3, "nu": 0.5}
    else:
        guess = initial_guess
        required = {"alpha", "rho", "nu"}
        if set(guess.keys()) != required:
            raise ValueError(
                f"initial_guess keys must be {sorted(required)}, got {sorted(guess.keys())}"
            )
        for key, val in guess.items():
            if not math.isfinite(val):
                raise ValueError(f"initial_guess[{key}]={val} not finite")
        if guess["alpha"] <= 0:
            raise ValueError(f"initial_guess[alpha] must be > 0, got {guess['alpha']}")
        if abs(guess["rho"]) >= 1.0:
            raise ValueError(f"initial_guess[rho] must satisfy |ρ|<1, got {guess['rho']}")
        if guess["nu"] < 0:
            raise ValueError(f"initial_guess[nu] must be ≥ 0, got {guess['nu']}")

    x0 = np.array([guess["alpha"], guess["rho"], guess["nu"]])

    def loss(params: np.ndarray) -> float:
        alpha, rho, nu = params
        if alpha <= 0 or abs(rho) >= 1.0 or nu < 0:
            return 1e10
        try:
            iv_pred = sabr_lognormal_iv(strikes_arr, forward, T, alpha, rho, nu, beta=1.0)
        except (FloatingPointError, ValueError):
            return 1e10
        residual = ivs_arr - iv_pred
        return float(np.sum(residual**2))

    constraints = [
        {"type": "ineq", "fun": lambda p: p[0] - 1e-6},  # alpha > 0
        {"type": "ineq", "fun": lambda p: 1.0 - abs(p[1]) - 1e-6},  # |rho| < 1
        {"type": "ineq", "fun": lambda p: p[2]},  # nu >= 0
    ]

    import time as _time

    t_start = _time.perf_counter()
    result = minimize(
        loss,
        x0,
        method="SLSQP",
        constraints=constraints,
        options={"maxiter": max_iter, "ftol": 1e-10},
    )
    fit_time_ms = int((_time.perf_counter() - t_start) * 1000)

    alpha, rho, nu = result.x
    iv_pred = sabr_lognormal_iv(strikes_arr, forward, T, alpha, rho, nu, beta=1.0)
    in_sample_rmse = float(np.sqrt(np.mean((iv_pred - ivs_arr) ** 2)))

    return SABRFitResult(
        alpha=float(alpha),
        rho=float(rho),
        nu=float(nu),
        beta=1.0,
        converged=bool(result.success),
        in_sample_rmse=in_sample_rmse,
        n_points=int(strikes_arr.size),
        fit_time_ms=fit_time_ms,
        extras={"optimizer_message": result.message, "loss_final": float(result.fun)},
    )


# ============================================================================
# Polynomial degree-2 fallback (Day 3 — last-resort backup)
# ============================================================================


@dataclass(frozen=True)
class PolyFitResult:
    """Polynomial degree-2 IV smile fit output: σ(k) = a + b·k + c·k².

    Attributes:
        a, b, c: 3 polynomial params (degree-2 in log-moneyness k = ln(K/F)).
        converged: always True (closed-form OLS); kept for SmileFitResult uniformity.
        in_sample_rmse: in-sample IV RMSE (vol points).
        n_points: (k, iv) pair count.
        fit_time_ms: wall-clock ms (negligible for closed-form).
    """

    a: float
    b: float
    c: float
    converged: bool
    in_sample_rmse: float
    n_points: int
    fit_time_ms: int = 0
    extras: dict = field(default_factory=dict)


def fit_smile_polynomial(
    log_moneyness: np.ndarray | list | tuple,
    ivs: np.ndarray | list | tuple,
) -> PolyFitResult:
    """Polynomial degree-2 OLS fit: σ(k) = a + b·k + c·k².

    Last-resort fallback when SVI/SABR fail. degree-2 (not 3+) chosen by R11.6
    plan v2 — degree-2 is convex in k², low over-fit risk; degree ≥ 3 can imply
    negative density at wings (no-arb violation).

    Args:
        log_moneyness: k = ln(K/F).
        ivs: observed IVs (annualised).

    Returns:
        PolyFitResult; converged always True (closed-form OLS).

    Raises:
        ValueError: shape mismatch / < 3 points / non-finite / iv ≤ 0.
    """
    import time as _time

    k_arr = np.asarray(log_moneyness, dtype=np.float64)
    iv_arr = np.asarray(ivs, dtype=np.float64)
    if k_arr.shape != iv_arr.shape:
        raise ValueError(
            f"fit_smile_polynomial: shape mismatch: log_moneyness={k_arr.shape} vs ivs={iv_arr.shape}"
        )
    if k_arr.size < 3:
        raise ValueError(
            f"fit_smile_polynomial: needs ≥3 (k, iv) points (3 params); got {k_arr.size}"
        )
    if not np.isfinite(k_arr).all():
        raise ValueError("fit_smile_polynomial: log_moneyness must be finite")
    if not np.isfinite(iv_arr).all():
        raise ValueError("fit_smile_polynomial: ivs must be finite")
    if (iv_arr <= 0).any():
        raise ValueError("fit_smile_polynomial: ivs must be > 0")

    t_start = _time.perf_counter()
    # np.polyfit returns coeffs high → low: [c, b, a]
    coeffs = np.polyfit(k_arr, iv_arr, deg=2)
    fit_time_ms = int((_time.perf_counter() - t_start) * 1000)
    c, b, a = float(coeffs[0]), float(coeffs[1]), float(coeffs[2])

    iv_pred = a + b * k_arr + c * k_arr**2
    in_sample_rmse = float(np.sqrt(np.mean((iv_pred - iv_arr) ** 2)))

    return PolyFitResult(
        a=a,
        b=b,
        c=c,
        converged=True,
        in_sample_rmse=in_sample_rmse,
        n_points=int(k_arr.size),
        fit_time_ms=fit_time_ms,
    )


# ============================================================================
# 3-tier orchestration: fit_with_fallback (Day 3 — Codex R11.6 P5 prereq #4)
# ============================================================================


@dataclass(frozen=True)
class SmileFitResult:
    """Wrapper for 3-tier fallback fit (SVI / SABR / polynomial).

    `model_type` audit (Codex R11.6 P5 prereq #4 silent reason gate):
      'svi' / 'sabr' / 'poly' / 'all_failed'

    Attributes:
        model_type: which model finally fit (audit transparency).
        fit_result: the underlying SVIFitResult / SABRFitResult / PolyFitResult.
        converged: from underlying fit.
        in_sample_rmse: from underlying fit.
        attempts: list of (model_type, converged, rmse_or_None, error_or_None);
            shows the full 3-tier audit trail for batch fit statistics.
        n_points: from underlying.
        total_fit_time_ms: sum of all attempts (not just successful).
    """

    model_type: str
    fit_result: SVIFitResult | SABRFitResult | PolyFitResult | None
    converged: bool
    in_sample_rmse: float
    attempts: list[dict]
    n_points: int
    total_fit_time_ms: int = 0


def fit_with_fallback(
    *,
    log_moneyness: np.ndarray | list | tuple,
    ivs: np.ndarray | list | tuple,
    forward: float,
    T: float,
    arb_free_svi: bool = True,
) -> SmileFitResult:
    """3-tier orchestration: try SVI raw → SABR β=1 → polynomial degree-2.

    Each tier failure (raise / converged=False / RMSE > 1.0) → fall to next.
    `model_type` and full `attempts` log returned for Codex R11.6 P5 prereq #4
    silent-reason audit (which model used per (date, expiry) for batch fit
    statistics: SVI≥60% / SABR≤30% / poly≤10% Pro 驗收矩陣 #6).

    Args:
        log_moneyness: k = ln(K/F).
        ivs: observed IVs (annualised vol).
        forward: F (used for SABR strikes = F · exp(k)).
        T: time-to-expiry years.
        arb_free_svi: pass-through to fit_svi_raw.

    Returns:
        SmileFitResult with model_type ∈ {svi, sabr, poly, all_failed}.

    Raises:
        ValueError: input validation upstream (shape / non-finite / etc.).
    """
    log_moneyness_arr = np.asarray(log_moneyness, dtype=np.float64)
    ivs_arr = np.asarray(ivs, dtype=np.float64)
    if log_moneyness_arr.shape != ivs_arr.shape:
        raise ValueError(
            f"fit_with_fallback: shape mismatch: log_moneyness={log_moneyness_arr.shape} "
            f"vs ivs={ivs_arr.shape}"
        )
    if not math.isfinite(forward) or forward <= 0:
        raise ValueError(f"fit_with_fallback: forward must be finite and > 0, got {forward}")
    if not math.isfinite(T) or T <= 0:
        raise ValueError(f"fit_with_fallback: T must be finite and > 0, got {T}")

    attempts: list[dict] = []
    total_time_ms = 0
    rmse_threshold = 1.0  # vol points; >1 vol = nonsense fit, fall through

    # Tier 1: SVI raw
    try:
        total_var = ivs_arr**2 * T
        svi_result = fit_svi_raw(log_moneyness_arr, total_var, T=T, arb_free=arb_free_svi)
        total_time_ms += svi_result.fit_time_ms
        attempts.append(
            {
                "model_type": "svi",
                "converged": svi_result.converged,
                "rmse": svi_result.in_sample_rmse,
                "error": None,
            }
        )
        if svi_result.converged and svi_result.in_sample_rmse < rmse_threshold:
            return SmileFitResult(
                model_type="svi",
                fit_result=svi_result,
                converged=True,
                in_sample_rmse=svi_result.in_sample_rmse,
                attempts=attempts,
                n_points=svi_result.n_points,
                total_fit_time_ms=total_time_ms,
            )
    except (ValueError, RuntimeError) as e:
        attempts.append(
            {"model_type": "svi", "converged": False, "rmse": None, "error": str(e)[:100]}
        )

    # Tier 2: SABR β=1 (need strikes; convert from log_moneyness)
    try:
        strikes = forward * np.exp(log_moneyness_arr)
        sabr_result = fit_sabr(strikes, ivs_arr, forward=forward, T=T, beta=1.0)
        total_time_ms += sabr_result.fit_time_ms
        attempts.append(
            {
                "model_type": "sabr",
                "converged": sabr_result.converged,
                "rmse": sabr_result.in_sample_rmse,
                "error": None,
            }
        )
        if sabr_result.converged and sabr_result.in_sample_rmse < rmse_threshold:
            return SmileFitResult(
                model_type="sabr",
                fit_result=sabr_result,
                converged=True,
                in_sample_rmse=sabr_result.in_sample_rmse,
                attempts=attempts,
                n_points=sabr_result.n_points,
                total_fit_time_ms=total_time_ms,
            )
    except (ValueError, RuntimeError) as e:
        attempts.append(
            {"model_type": "sabr", "converged": False, "rmse": None, "error": str(e)[:100]}
        )

    # Tier 3: polynomial degree-2 (last resort)
    try:
        poly_result = fit_smile_polynomial(log_moneyness_arr, ivs_arr)
        total_time_ms += poly_result.fit_time_ms
        attempts.append(
            {
                "model_type": "poly",
                "converged": poly_result.converged,
                "rmse": poly_result.in_sample_rmse,
                "error": None,
            }
        )
        if poly_result.converged:  # poly always converges (closed-form OLS)
            return SmileFitResult(
                model_type="poly",
                fit_result=poly_result,
                converged=True,
                in_sample_rmse=poly_result.in_sample_rmse,
                attempts=attempts,
                n_points=poly_result.n_points,
                total_fit_time_ms=total_time_ms,
            )
    except (ValueError, RuntimeError) as e:
        attempts.append(
            {"model_type": "poly", "converged": False, "rmse": None, "error": str(e)[:100]}
        )

    # All 3 tiers fail (rare — would mean degenerate input)
    return SmileFitResult(
        model_type="all_failed",
        fit_result=None,
        converged=False,
        in_sample_rmse=float("nan"),
        attempts=attempts,
        n_points=int(log_moneyness_arr.size),
        total_fit_time_ms=total_time_ms,
    )
