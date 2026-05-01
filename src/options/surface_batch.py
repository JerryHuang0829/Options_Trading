"""Vol surface batch fit + multiprocessing (Week 4 Day 4).

Iterates an enriched chain DataFrame per (date, expiry) group, computes
log-moneyness k = ln(K/F), calls `fit_with_fallback` (Day 3 3-tier SVI →
SABR → polynomial), and returns a list of `SurfaceFitRecord` per group.

Multiprocessing 設計 (Windows pickle 限制):
  - Worker `_fit_one_smile` 在 module top-level (無 closure / lambda)
  - Task tuple 為 plain dict (np.ndarray + scalars), 完全 picklable
  - `n_workers=1` → sequential loop (測試/除錯/小批次默認)
  - `n_workers>1` → ProcessPoolExecutor + chunksize 自適應
  - Caller 須在 `if __name__ == "__main__":` 守護下啟用 (Windows spawn
    模式必要; library code 不負責這條, 文件必說明)

Forward 計算 (Phase 1 簡化):
  - Day 4 假設 caller 已 enrich `underlying` (現貨); F ≈ underlying
  - PCP forward 推 (F = call_mid - put_mid + K·exp(-rT) + S·(1-exp(-qT)))
    留 Week 5 真 backtest 前 (Day 7 plan 步驟 5.0)
  - 本 module 接受 caller 顯式傳入 `forward_fn(date, expiry, group)` 覆寫

T 計算: T = (expiry - date).days / CALENDAR_DAYS_PER_YEAR (365). expired
smile (T<=0) 該 caller 端先過濾; 此處 days <= 0 → silent reason 紀錄
model_type='insufficient_data'. R11.11 P2 fix: 從 365.25 改 365 與 BSM
pricing / enrich / portfolio.aggregate_greeks 全 codebase 一致 (見
config/constants.py CALENDAR_DAYS_PER_YEAR; 365.25 vs 365 0.07% 差距會讓
IV fit 的 T 與 mark-to-market 的 T 系統性不一致).

Codex R11.6 P5 prereq #4 audit 串連:
  - 每筆 SurfaceFitRecord 含 model_type + attempts log
  - batch fit 後可彙整 SVI 收斂率 / SABR 收斂率 / poly 收斂率 (驗收矩陣 #6)
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.constants import CALENDAR_DAYS_PER_YEAR
from src.options.vol_surface import (
    PolyFitResult,
    SABRFitResult,
    SmileFitResult,
    SVIFitResult,
    fit_with_fallback,
)

_LOG = logging.getLogger(__name__)

REQUIRED_COLUMNS: frozenset[str] = frozenset({"date", "expiry", "strike", "iv", "underlying"})


@dataclass(frozen=True)
class SurfaceFitRecord:
    """One (date, expiry) smile fit record (audit + persistence schema).

    Attributes:
        date: ISO 'YYYY-MM-DD'.
        expiry: ISO 'YYYY-MM-DD'.
        model_type: 'svi' | 'sabr' | 'poly' | 'all_failed' | 'insufficient_data'.
        converged: from underlying SmileFitResult; False for insufficient_data.
        n_points: strikes used in fit.
        in_sample_rmse: vol-points RMSE; nan for failed/insufficient.
        fit_time_ms: total wall time across all 3 attempts.
        forward: F used (default `underlying` median per group).
        T: years to expiry used in fit.
        params: model params dict (SVI: a/b/rho/m/sigma; SABR: alpha/rho/nu/beta;
            poly: a/b/c). Empty dict for failed.
        attempts: from SmileFitResult; full 3-tier audit log.
        error: error message if all_failed/insufficient_data; None otherwise.
    """

    date: str
    expiry: str
    model_type: str
    converged: bool
    n_points: int
    in_sample_rmse: float
    fit_time_ms: int
    forward: float
    T: float
    params: dict
    attempts: list[dict] = field(default_factory=list)
    error: str | None = None


def _params_from_fit_result(
    fit_result: SVIFitResult | SABRFitResult | PolyFitResult | None,
    model_type: str,
) -> dict:
    """Extract model params dict for persistence (schema-stable per model_type)."""
    if fit_result is None:
        return {}
    if model_type == "svi" and isinstance(fit_result, SVIFitResult):
        return {
            "a": fit_result.a,
            "b": fit_result.b,
            "rho": fit_result.rho,
            "m": fit_result.m,
            "sigma": fit_result.sigma,
        }
    if model_type == "sabr" and isinstance(fit_result, SABRFitResult):
        return {
            "alpha": fit_result.alpha,
            "rho": fit_result.rho,
            "nu": fit_result.nu,
            "beta": fit_result.beta,
        }
    if model_type == "poly" and isinstance(fit_result, PolyFitResult):
        return {"a": fit_result.a, "b": fit_result.b, "c": fit_result.c}
    return {}


def _fit_one_smile(task: dict) -> SurfaceFitRecord:
    """Worker: fit one (date, expiry) smile. Top-level for Windows pickle.

    Args:
        task: dict with keys
            - date (str ISO), expiry (str ISO)
            - log_moneyness (np.ndarray), ivs (np.ndarray)
            - forward (float), T (float)
            - arb_free_svi (bool)

    Returns:
        SurfaceFitRecord. Never raises (all errors go to model_type='all_failed'
        or 'insufficient_data' so batch loop doesn't abort).
    """
    date = task["date"]
    expiry = task["expiry"]
    log_moneyness = task["log_moneyness"]
    ivs = task["ivs"]
    forward = task["forward"]
    T = task["T"]
    arb_free_svi = task.get("arb_free_svi", True)

    try:
        result: SmileFitResult = fit_with_fallback(
            log_moneyness=log_moneyness,
            ivs=ivs,
            forward=forward,
            T=T,
            arb_free_svi=arb_free_svi,
        )
        params = _params_from_fit_result(result.fit_result, result.model_type)
        return SurfaceFitRecord(
            date=date,
            expiry=expiry,
            model_type=result.model_type,
            converged=result.converged,
            n_points=result.n_points,
            in_sample_rmse=result.in_sample_rmse,
            fit_time_ms=result.total_fit_time_ms,
            forward=forward,
            T=T,
            params=params,
            attempts=list(result.attempts),
            error=None if result.converged else "all_3_tiers_failed",
        )
    except (ValueError, RuntimeError) as e:
        return SurfaceFitRecord(
            date=date,
            expiry=expiry,
            model_type="all_failed",
            converged=False,
            n_points=int(len(log_moneyness)),
            in_sample_rmse=float("nan"),
            fit_time_ms=0,
            forward=forward,
            T=T,
            params={},
            attempts=[],
            error=str(e)[:200],
        )


def _build_task(
    date_ts: pd.Timestamp,
    expiry_ts: pd.Timestamp,
    group: pd.DataFrame,
    arb_free_svi: bool,
    forward_fn: Callable[[pd.Timestamp, pd.Timestamp, pd.DataFrame], float] | None,
) -> dict | None:
    """Build a fit task from one (date, expiry) group, or None if degenerate.

    Returns None when:
      - underlying NaN (forward unknown), OR
      - all IV NaN / non-positive after dedup, OR
      - T <= 0 (expired or same-day expiry — caller should pre-filter)
    Caller upgrades None → SurfaceFitRecord(model_type='insufficient_data').
    """
    if forward_fn is not None:
        forward = float(forward_fn(date_ts, expiry_ts, group))
    else:
        underlying_series = group["underlying"].dropna()
        if underlying_series.empty:
            return None
        forward = float(underlying_series.median())

    if not math.isfinite(forward) or forward <= 0:
        return None

    days = (expiry_ts - date_ts).days
    if days <= 0:
        return None
    T = days / CALENDAR_DAYS_PER_YEAR

    # Dedup per strike: median IV across option_type / multiple rows
    iv_series = group["iv"]
    finite_mask = iv_series.notna() & np.isfinite(iv_series) & (iv_series > 0)
    if not finite_mask.any():
        return None
    valid = group.loc[finite_mask, ["strike", "iv"]].copy()
    median_per_strike = valid.groupby("strike")["iv"].median().sort_index()
    if len(median_per_strike) == 0:
        return None

    strikes = median_per_strike.index.to_numpy(dtype=np.float64)
    ivs = median_per_strike.to_numpy(dtype=np.float64)
    log_moneyness = np.log(strikes / forward)

    return {
        "date": date_ts.strftime("%Y-%m-%d"),
        "expiry": expiry_ts.strftime("%Y-%m-%d"),
        "log_moneyness": log_moneyness,
        "ivs": ivs,
        "forward": forward,
        "T": T,
        "arb_free_svi": arb_free_svi,
    }


def _insufficient_record(
    date_ts: pd.Timestamp, expiry_ts: pd.Timestamp, n_points: int, reason: str
) -> SurfaceFitRecord:
    return SurfaceFitRecord(
        date=date_ts.strftime("%Y-%m-%d"),
        expiry=expiry_ts.strftime("%Y-%m-%d"),
        model_type="insufficient_data",
        converged=False,
        n_points=n_points,
        in_sample_rmse=float("nan"),
        fit_time_ms=0,
        forward=float("nan"),
        T=float("nan"),
        params={},
        attempts=[],
        error=reason,
    )


def batch_fit_surface(
    *,
    chain: pd.DataFrame,
    n_workers: int = 1,
    min_strikes: int = 5,
    arb_free_svi: bool = True,
    forward_fn: Callable[[pd.Timestamp, pd.Timestamp, pd.DataFrame], float] | None = None,
    chunksize: int | None = None,
) -> list[SurfaceFitRecord]:
    """Fit vol surface per (date, expiry) group across a (multi-day) chain.

    Args:
        chain: enriched chain with at minimum {date, expiry, strike, iv,
            underlying}; date/expiry = datetime64; strike/iv/underlying = float.
        n_workers: 1 → sequential loop; >1 → ProcessPoolExecutor (Windows spawn).
            Caller in script must wrap in `if __name__ == "__main__":` for
            n_workers > 1.
        min_strikes: groups with fewer surviving strikes (post IV finite filter)
            return record with model_type='insufficient_data' (skip fit).
        arb_free_svi: pass-through to fit_with_fallback.
        forward_fn: optional override (date_ts, expiry_ts, group_df) → float;
            default uses median(group['underlying']).
        chunksize: ProcessPoolExecutor chunksize; default = max(1, n_groups // (4*n_workers)).

    Returns:
        list[SurfaceFitRecord] — one per group, in (date, expiry) sort order.

    Raises:
        ValueError: missing required columns / empty chain / n_workers < 1 /
            min_strikes < 3 (need at least 3 for poly OLS).
    """
    if chain is None or chain.empty:
        raise ValueError("batch_fit_surface: chain is empty")
    missing = REQUIRED_COLUMNS - set(chain.columns)
    if missing:
        raise ValueError(f"batch_fit_surface: chain missing required columns: {sorted(missing)}")
    if n_workers < 1:
        raise ValueError(f"batch_fit_surface: n_workers must be >= 1, got {n_workers}")
    if min_strikes < 3:
        raise ValueError(
            f"batch_fit_surface: min_strikes must be >= 3 (poly OLS needs ≥3), got {min_strikes}"
        )

    # Coerce date/expiry to Timestamp for groupby + arithmetic
    chain = chain.copy()
    chain["date"] = pd.to_datetime(chain["date"])
    chain["expiry"] = pd.to_datetime(chain["expiry"])

    # Build tasks + insufficient records
    tasks: list[dict] = []
    insufficient: list[SurfaceFitRecord] = []
    grouped = chain.groupby(["date", "expiry"], sort=True)
    for key, group in grouped:
        # groupby on 2 keys returns Hashable tuple; cast for mypy + Timestamp constructor
        key_tuple = key if isinstance(key, tuple) else (key,)
        date_ts = pd.Timestamp(str(key_tuple[0]))
        expiry_ts = pd.Timestamp(str(key_tuple[1]))
        task = _build_task(date_ts, expiry_ts, group, arb_free_svi, forward_fn)
        if task is None:
            insufficient.append(
                _insufficient_record(
                    date_ts,
                    expiry_ts,
                    n_points=int(len(group)),
                    reason="forward_or_T_or_iv_unusable",
                )
            )
            continue
        if len(task["log_moneyness"]) < min_strikes:
            insufficient.append(
                _insufficient_record(
                    date_ts,
                    expiry_ts,
                    n_points=int(len(task["log_moneyness"])),
                    reason=f"fewer_than_min_strikes={min_strikes}",
                )
            )
            continue
        tasks.append(task)

    # Execute fits
    fitted: list[SurfaceFitRecord] = []
    if not tasks:
        pass
    elif n_workers == 1:
        for task in tasks:
            fitted.append(_fit_one_smile(task))
    else:
        cs = chunksize if chunksize is not None else max(1, len(tasks) // (4 * n_workers))
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            for record in executor.map(_fit_one_smile, tasks, chunksize=cs):
                fitted.append(record)

    # Concat + sort by (date, expiry) for deterministic output
    all_records = fitted + insufficient
    all_records.sort(key=lambda r: (r.date, r.expiry))
    return all_records


def records_to_dataframe(records: Iterable[SurfaceFitRecord]) -> pd.DataFrame:
    """Convert SurfaceFitRecord list to flat DataFrame (params/attempts → JSON str).

    Schema (cache layer):
      date / expiry / model_type / converged / n_points / in_sample_rmse /
      fit_time_ms / forward / T / params_json / attempts_json / error
    """
    import json

    rows = []
    for r in records:
        rows.append(
            {
                "date": r.date,
                "expiry": r.expiry,
                "model_type": r.model_type,
                "converged": r.converged,
                "n_points": r.n_points,
                "in_sample_rmse": r.in_sample_rmse,
                "fit_time_ms": r.fit_time_ms,
                "forward": r.forward,
                "T": r.T,
                "params_json": json.dumps(r.params, sort_keys=True),
                "attempts_json": json.dumps(r.attempts, sort_keys=True),
                "error": r.error,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "expiry",
            "model_type",
            "converged",
            "n_points",
            "in_sample_rmse",
            "fit_time_ms",
            "forward",
            "T",
            "params_json",
            "attempts_json",
            "error",
        ],
    )


def dataframe_to_records(df: pd.DataFrame) -> list[SurfaceFitRecord]:
    """Inverse of records_to_dataframe (load → records for downstream use)."""
    import json

    records: list[SurfaceFitRecord] = []
    for _, row in df.iterrows():
        records.append(
            SurfaceFitRecord(
                date=str(row["date"]),
                expiry=str(row["expiry"]),
                model_type=str(row["model_type"]),
                converged=bool(row["converged"]),
                n_points=int(row["n_points"]),
                in_sample_rmse=float(row["in_sample_rmse"]),
                fit_time_ms=int(row["fit_time_ms"]),
                forward=float(row["forward"]),
                T=float(row["T"]),
                params=json.loads(row["params_json"]),
                attempts=json.loads(row["attempts_json"]),
                error=None if pd.isna(row["error"]) else str(row["error"]),
            )
        )
    return records
