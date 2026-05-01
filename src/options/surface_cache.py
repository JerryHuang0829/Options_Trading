"""Surface fit parquet cache (Week 4 Day 4).

Date-sharded persistence for `SurfaceFitRecord` lists from `surface_batch.
batch_fit_surface`. Schema mirrors `records_to_dataframe` output (12 cols
含 params_json / attempts_json).

Layout:
  <cache_dir>/surface_fits/<YYYY>/<YYYY-MM-DD>.parquet

Year-folder split same as src/data/cache.py (1963 trading days × 8-12
expiries → ~20k rows total → ~2k rows/year). Atomic write tmp → rename.

R10.10 P2-#5 schema-versioning ethos: 不在 col-count gate (poly 沒 m/sigma);
JSON-serialise variable schema 留給 caller `dataframe_to_records` 還原.

R11.12 P1 fix (Codex): load_surface_fits 加 semantic validation —
之前只驗 col-set，dtype/JSON/value 全錯仍 silent accept (e.g. date 寫
int64, params_json 寫 int → load_surface_records 才爆 cryptic TypeError
不指出 shard 名). Week 5 add_model_price 開工前的 cache contract gate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from src.options.surface_batch import (
    SurfaceFitRecord,
    dataframe_to_records,
    records_to_dataframe,
)

SURFACE_FITS_LAYER = "surface_fits"

EXPECTED_COLUMNS: frozenset[str] = frozenset(
    {
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
    }
)

VALID_MODEL_TYPES: frozenset[str] = frozenset(
    {"svi", "sabr", "poly", "all_failed", "insufficient_data"}
)

# Per-model_type required keys in params dict (post json.loads)
MODEL_PARAMS_KEYS: dict[str, frozenset[str]] = {
    "svi": frozenset({"a", "b", "rho", "m", "sigma"}),
    "sabr": frozenset({"alpha", "rho", "nu", "beta"}),
    "poly": frozenset({"a", "b", "c"}),
    "all_failed": frozenset(),  # empty params expected
    "insufficient_data": frozenset(),  # empty params expected
}


def _shard_error(shard_name: str, msg: str) -> str:
    return f"load_surface_fits: shard '{shard_name}' {msg}"


def _validate_shard_semantic(df: pd.DataFrame, shard_name: str) -> None:
    """Per-row semantic validation — dtype + value range + JSON well-formed.

    Codex R11.12 P1 fix. Errors include shard name + row index + field name
    so debugging a corrupted shard at scale (1963 days) doesn't require
    binary-search through pyarrow output.

    Validates:
      - date / expiry: parseable as ISO 'YYYY-MM-DD'
      - model_type ∈ VALID_MODEL_TYPES
      - converged: bool-castable
      - n_points: non-negative integer
      - T / forward: finite or NaN (NaN OK for failed/insufficient)
      - in_sample_rmse: finite or NaN
      - fit_time_ms: non-negative integer
      - params_json / attempts_json: parse to dict / list
      - params_json keys match MODEL_PARAMS_KEYS[model_type]
      - **R11.13 P1 (financial invariant)**: converged=True SVI/SABR/poly →
        forward > 0, T > 0, rmse >= 0 finite + per-model param domain
        (SVI: sigma>0, b>=0, |rho|<1; SABR: alpha>0, |rho|<1, nu>=0, beta==1)
    """

    def _row_err(idx: int, field: str, msg: str) -> ValueError:
        return ValueError(_shard_error(shard_name, f"row[{idx}] field '{field}': {msg}"))

    for raw_idx, row in df.iterrows():
        idx = int(str(raw_idx))
        # date / expiry ISO parse
        for field in ("date", "expiry"):
            val = row[field]
            if not isinstance(val, str):
                raise _row_err(idx, field, f"expected str ISO date, got {type(val).__name__}")
            if len(val) != 10 or val[4] != "-" or val[7] != "-":
                raise _row_err(idx, field, f"expected 'YYYY-MM-DD', got {val!r}")
            try:
                pd.Timestamp(val)
            except (ValueError, TypeError) as e:
                raise _row_err(idx, field, f"unparseable ISO date {val!r}: {e}") from e

        # model_type
        mt = row["model_type"]
        if not isinstance(mt, str) or mt not in VALID_MODEL_TYPES:
            raise _row_err(
                idx,
                "model_type",
                f"expected one of {sorted(VALID_MODEL_TYPES)}, got {mt!r}",
            )

        # converged: bool / numpy.bool_ / 0|1
        cv = row["converged"]
        if not (isinstance(cv, (bool,)) or hasattr(cv, "dtype")):
            raise _row_err(idx, "converged", f"expected bool, got {type(cv).__name__}")
        try:
            cv_bool = bool(cv)
        except (ValueError, TypeError) as e:
            raise _row_err(idx, "converged", f"not bool-castable: {e}") from e
        if cv_bool not in (True, False):
            raise _row_err(idx, "converged", f"not in {{True,False}}: {cv!r}")

        # n_points: non-negative int
        try:
            np_val = int(row["n_points"])
        except (ValueError, TypeError) as e:
            raise _row_err(idx, "n_points", f"not int-castable: {e}") from e
        if np_val < 0:
            raise _row_err(idx, "n_points", f"must be >= 0, got {np_val}")

        # fit_time_ms: non-negative int
        try:
            ft_val = int(row["fit_time_ms"])
        except (ValueError, TypeError) as e:
            raise _row_err(idx, "fit_time_ms", f"not int-castable: {e}") from e
        if ft_val < 0:
            raise _row_err(idx, "fit_time_ms", f"must be >= 0, got {ft_val}")

        # T / forward / in_sample_rmse: finite OR NaN (NaN OK for failed)
        for field in ("T", "forward", "in_sample_rmse"):
            try:
                val = float(row[field])
            except (ValueError, TypeError) as e:
                raise _row_err(idx, field, f"not float-castable: {e}") from e
            if not (math.isnan(val) or math.isfinite(val)):
                raise _row_err(idx, field, f"must be finite or NaN, got {val}")

        # params_json: parse to dict
        pj = row["params_json"]
        if not isinstance(pj, (str, bytes, bytearray)):
            raise _row_err(
                idx,
                "params_json",
                f"expected JSON string, got {type(pj).__name__}",
            )
        try:
            params: Any = json.loads(pj)
        except json.JSONDecodeError as e:
            raise _row_err(idx, "params_json", f"invalid JSON: {e}") from e
        if not isinstance(params, dict):
            raise _row_err(idx, "params_json", f"expected JSON object, got {type(params).__name__}")

        # attempts_json: parse to list
        aj = row["attempts_json"]
        if not isinstance(aj, (str, bytes, bytearray)):
            raise _row_err(
                idx,
                "attempts_json",
                f"expected JSON string, got {type(aj).__name__}",
            )
        try:
            attempts: Any = json.loads(aj)
        except json.JSONDecodeError as e:
            raise _row_err(idx, "attempts_json", f"invalid JSON: {e}") from e
        if not isinstance(attempts, list):
            raise _row_err(
                idx, "attempts_json", f"expected JSON array, got {type(attempts).__name__}"
            )

        # params keys match model_type schema
        expected_keys = MODEL_PARAMS_KEYS[mt]
        actual_keys = frozenset(params.keys())
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
            raise _row_err(
                idx,
                "params_json",
                f"model_type={mt!r} requires keys {sorted(expected_keys)}; "
                f"missing={missing}, extra={extra}",
            )

        # ===========================================================
        # R11.13 P1 fix (Codex): per-model financial invariant gate.
        # 8 維 dtype/JSON gate 過 ≠ 模型可用於定價. converged=True 的 SVI/SABR/poly
        # 必須 forward > 0 / T > 0 / rmse >= 0 finite + 各 model 數學 domain
        # (SVI: sigma>0, b>=0, |rho|<1; SABR: alpha>0, |rho|<1, nu>=0, beta==1).
        # all_failed / insufficient_data 才允許 forward/T/rmse = NaN.
        # ===========================================================
        is_successful_fit = mt in {"svi", "sabr", "poly"} and cv_bool
        if is_successful_fit:
            forward_val = float(row["forward"])
            t_val = float(row["T"])
            rmse_val = float(row["in_sample_rmse"])
            if not (math.isfinite(forward_val) and forward_val > 0):
                raise _row_err(
                    idx, "forward", f"successful {mt} fit requires > 0 finite, got {forward_val}"
                )
            if not (math.isfinite(t_val) and t_val > 0):
                raise _row_err(idx, "T", f"successful {mt} fit requires > 0 finite, got {t_val}")
            if not (math.isfinite(rmse_val) and rmse_val >= 0):
                raise _row_err(
                    idx,
                    "in_sample_rmse",
                    f"successful {mt} fit requires >= 0 finite, got {rmse_val}",
                )
            # Per-model param domain
            if mt == "svi":
                # Lee 2004 / Gatheral 2014 raw form: sigma>0, b>=0, |rho|<1, all finite
                for k in ("a", "b", "rho", "m", "sigma"):
                    if not math.isfinite(float(params[k])):
                        raise _row_err(
                            idx, "params_json", f"SVI param {k!r} must be finite, got {params[k]}"
                        )
                if float(params["sigma"]) <= 0:
                    raise _row_err(
                        idx, "params_json", f"SVI sigma must be > 0, got {params['sigma']}"
                    )
                if float(params["b"]) < 0:
                    raise _row_err(idx, "params_json", f"SVI b must be >= 0, got {params['b']}")
                if abs(float(params["rho"])) >= 1:
                    raise _row_err(
                        idx, "params_json", f"SVI |rho| must be < 1, got {params['rho']}"
                    )
                # R11.14 P2 fix (Codex): Lee 2004 b upper bound 與 fit 端
                # (vol_surface.py:275-280) 對稱 — fit 已 enforce 4/(T·(1+|ρ|))
                # 但 cache loader 之前只擋 b<0; b > Lee upper silent accept →
                # downstream IV 可能 1000-3600% 荒謬值. Pattern 14 (R11.13 加)
                # save/load 對稱 + defense-in-depth 漸進失守雙觸發.
                # R12.3 P fix: |rho| 接近 1 時 lee_upper 對 rho 敏感
                # (b ~ 7.93494847 vs lee_upper 7.9349... 差 6 decimal float noise);
                # 加 1e-4 relative tolerance 處理 boundary float-point edge case
                # (5yr 真資料實證 2025-09-16 SVI fit 觸發). 真 1000% IV 違反早被
                # vol_surface fit 端 cap 住.
                lee_upper = 4.0 / (t_val * (1.0 + abs(float(params["rho"]))))
                lee_upper_with_eps = lee_upper * (1.0 + 1e-4)
                if float(params["b"]) > lee_upper_with_eps:
                    raise _row_err(
                        idx,
                        "params_json",
                        f"SVI b={params['b']} exceeds Lee 2004 upper bound "
                        f"4/(T*(1+|rho|))={lee_upper:.4f} (T={t_val}, rho={params['rho']})",
                    )
            elif mt == "sabr":
                # Hagan 2002: alpha>0, |rho|<1, nu>=0, beta==1 (lognormal)
                for k in ("alpha", "rho", "nu", "beta"):
                    if not math.isfinite(float(params[k])):
                        raise _row_err(
                            idx, "params_json", f"SABR param {k!r} must be finite, got {params[k]}"
                        )
                if float(params["alpha"]) <= 0:
                    raise _row_err(
                        idx, "params_json", f"SABR alpha must be > 0, got {params['alpha']}"
                    )
                if abs(float(params["rho"])) >= 1:
                    raise _row_err(
                        idx, "params_json", f"SABR |rho| must be < 1, got {params['rho']}"
                    )
                if float(params["nu"]) < 0:
                    raise _row_err(idx, "params_json", f"SABR nu must be >= 0, got {params['nu']}")
                if float(params["beta"]) != 1.0:
                    raise _row_err(
                        idx,
                        "params_json",
                        f"SABR beta must be 1.0 (lognormal expansion only), got {params['beta']}",
                    )
            elif mt == "poly":
                # σ(k) = a + b·k + c·k² — all finite (no domain on coeffs)
                for k in ("a", "b", "c"):
                    if not math.isfinite(float(params[k])):
                        raise _row_err(
                            idx,
                            "params_json",
                            f"poly param {k!r} must be finite, got {params[k]}",
                        )


def _validate_iso_date(date: str) -> None:
    try:
        pd.Timestamp(date)
    except (ValueError, TypeError) as e:
        raise ValueError(f"surface_cache: invalid ISO date {date!r} ({e})") from e
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise ValueError(f"surface_cache: date must be 'YYYY-MM-DD' format, got {date!r}")


def _shard_path(cache_dir: str, date: str) -> Path:
    """Year-sharded path: <cache_dir>/surface_fits/<YYYY>/<YYYY-MM-DD>.parquet."""
    year = date[:4]
    return Path(cache_dir) / SURFACE_FITS_LAYER / year / f"{date}.parquet"


def save_surface_fits(
    records: list[SurfaceFitRecord],
    cache_dir: str,
    date: str,
) -> str:
    """Atomic-write one date's surface fits.

    Args:
        records: list[SurfaceFitRecord] (must all share `date`).
        cache_dir: cache root.
        date: ISO 'YYYY-MM-DD'.

    Returns: absolute path string.

    Raises:
        ValueError: empty records / mismatched date / invalid date format /
            schema drift after records_to_dataframe.
    """
    _validate_iso_date(date)
    if not records:
        raise ValueError(f"save_surface_fits: records is empty (date={date})")

    mismatched = [r for r in records if r.date != date]
    if mismatched:
        raise ValueError(
            f"save_surface_fits: {len(mismatched)} record(s) have date != {date} "
            f"(first mismatched: {mismatched[0].date})"
        )

    df = records_to_dataframe(records)
    actual_cols = set(df.columns)
    if actual_cols != set(EXPECTED_COLUMNS):
        raise ValueError(
            f"save_surface_fits: column drift; actual={sorted(actual_cols)}, "
            f"expected={sorted(EXPECTED_COLUMNS)}"
        )

    path = _shard_path(cache_dir, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp_path, engine="pyarrow", index=False)
    tmp_path.replace(path)
    return str(path.resolve())


def load_surface_fits(
    cache_dir: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Read shards in [start, end] inclusive, concat sorted by (date, expiry).

    Missing dates: silent skip.

    Each shard validated 兩層 before concat:
      Layer 1 (R11.11 P1 fix): col-set 對齊 EXPECTED_COLUMNS
      Layer 2 (R11.12 P1 fix): per-row semantic — dtype / value range /
        JSON well-formed / params keys 對 model_type schema (`_validate_shard_semantic`)

    Layer 1 only catches missing/extra cols. Layer 2 catches dtype drift
    (e.g. date 寫成 int64), bad JSON in params_json/attempts_json, out-of-set
    model_type, negative n_points 等 silent corruption — pre-Week 5
    add_model_price 必要 contract.

    Returns: DataFrame (empty if no shards in range). 12-col schema preserved;
    use `dataframe_to_records` to reconstruct SurfaceFitRecord list.

    Raises:
        ValueError: invalid date format / start_date > end_date /
            schema drift (shard col-set ≠ EXPECTED_COLUMNS) /
            semantic drift (dtype / JSON / value / params keys; error message
            includes shard name + row index + field).
    """
    _validate_iso_date(start_date)
    _validate_iso_date(end_date)
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    if start_ts > end_ts:
        raise ValueError(f"load_surface_fits: start_date > end_date ({start_date} > {end_date})")

    layer_dir = Path(cache_dir) / SURFACE_FITS_LAYER
    if not layer_dir.exists():
        return pd.DataFrame(columns=sorted(EXPECTED_COLUMNS))

    shards = []
    for year in range(start_ts.year, end_ts.year + 1):
        year_dir = layer_dir / str(year)
        if not year_dir.exists():
            continue
        for shard_path in sorted(year_dir.glob("*.parquet")):
            date_str = shard_path.stem
            try:
                shard_ts = pd.Timestamp(date_str)
            except (ValueError, TypeError):
                continue
            if not (start_ts <= shard_ts <= end_ts):
                continue
            shard_df = pd.read_parquet(shard_path, engine="pyarrow")
            shard_cols = set(shard_df.columns)
            if shard_cols != set(EXPECTED_COLUMNS):
                missing = sorted(set(EXPECTED_COLUMNS) - shard_cols)
                extra = sorted(shard_cols - set(EXPECTED_COLUMNS))
                raise ValueError(
                    f"load_surface_fits: schema drift in {shard_path.name}; "
                    f"missing={missing}, extra={extra}"
                )
            _validate_shard_semantic(shard_df, shard_path.name)
            shards.append(shard_df)

    if not shards:
        return pd.DataFrame(columns=sorted(EXPECTED_COLUMNS))

    return (
        pd.concat(shards, ignore_index=True).sort_values(["date", "expiry"]).reset_index(drop=True)
    )


def is_cached(cache_dir: str, date: str) -> bool:
    """O(1) existence check for cached surface-fits shard."""
    _validate_iso_date(date)
    return _shard_path(cache_dir, date).exists()


def list_cached_dates(cache_dir: str) -> list[str]:
    """Return sorted ISO dates of cached surface-fits shards (year-sharded).

    Stems that don't parse as ISO 'YYYY-MM-DD' are skipped (R11.11 P2 fix —
    raw `p.stem` previously leaked non-date filenames into caller; downstream
    code might pass these to load_surface_fits → ValueError loop).
    """
    layer_dir = Path(cache_dir) / SURFACE_FITS_LAYER
    if not layer_dir.exists():
        return []
    iso_dates: list[str] = []
    for p in layer_dir.glob("*/*.parquet"):
        stem = p.stem
        if len(stem) != 10 or stem[4] != "-" or stem[7] != "-":
            continue
        try:
            pd.Timestamp(stem)
        except (ValueError, TypeError):
            continue
        iso_dates.append(stem)
    return sorted(iso_dates)


def load_surface_records(
    cache_dir: str,
    start_date: str,
    end_date: str,
) -> list[SurfaceFitRecord]:
    """Convenience: load_surface_fits + dataframe_to_records."""
    df = load_surface_fits(cache_dir, start_date, end_date)
    if df.empty:
        return []
    return dataframe_to_records(df)
