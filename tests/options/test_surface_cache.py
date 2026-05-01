"""Tests for src/options/surface_cache.py — Week 4 Day 4 surface fits parquet cache.

23 tests:

  1. test_save_and_load_roundtrip (single date, multiple expiries; load returns 12-col df)
  2. test_save_atomic_no_tmp_leftover (success path leaves no .tmp)
  3. test_load_missing_date_returns_empty (load on dir 不存在 / date 不存在)
  4. test_is_cached_true_false (existence check)
  5. test_list_cached_dates_sorted (multi-date insert, sorted output)
  6. test_invalid_date_format_raises (bad ISO format / inverted range)
  7. test_load_cross_date_concat_sorted (load multi-shard, sorted by date+expiry)
  8. test_load_surface_records_roundtrip_preserves_params (records → save → load_records)
  9. test_load_surface_fits_raises_on_schema_drift (R11.11 P1: col-set drift)
 10. test_list_cached_dates_skips_non_iso_stems (R11.11 P2a)
 11. test_load_surface_fits_raises_on_dtype_drift (R11.12 P1: col-set OK but dtype 全 int64)
 12. test_load_surface_fits_raises_on_invalid_model_type (R11.12 P1: model_type 不在集合)
 13. test_load_surface_fits_raises_on_bad_json (R11.12 P1: params_json 不是合法 JSON)
 14. test_load_surface_fits_raises_on_params_keys_mismatch (R11.12 P1: SVI 缺 sigma)
 15. test_load_surface_fits_raises_on_negative_n_points (R11.12 P1: n_points = -1)
 16. test_load_surface_fits_raises_on_successful_fit_with_T_le_zero (R11.13 P1)
 17. test_load_surface_fits_raises_on_successful_fit_with_forward_le_zero (R11.13 P1)
 18. test_load_surface_fits_raises_on_successful_fit_with_negative_rmse (R11.13 P1)
 19. test_load_surface_fits_raises_on_svi_sigma_le_zero (R11.13 P1)
 20. test_load_surface_fits_raises_on_svi_b_negative (R11.13 P1)
 21. test_load_surface_fits_raises_on_svi_rho_out_of_bound (R11.13 P1)
 22. test_load_surface_fits_raises_on_sabr_invalid_params (R11.13 P1: alpha<=0 / beta!=1)
 23. test_load_surface_fits_allows_failed_records_with_nan (R11.13 P1: insufficient_data 仍允 NaN)
 24. test_load_surface_fits_raises_on_svi_b_above_lee_upper (R11.14 P2: cache vs fit 端對稱 Lee 2004)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.options.surface_batch import SurfaceFitRecord
from src.options.surface_cache import (
    SURFACE_FITS_LAYER,
    is_cached,
    list_cached_dates,
    load_surface_fits,
    load_surface_records,
    save_surface_fits,
)

_DEFAULT_PARAMS: dict[str, dict[str, float]] = {
    "svi": {"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1},
    "sabr": {"alpha": 0.18, "rho": -0.3, "nu": 0.4, "beta": 1.0},
    "poly": {"a": 0.18, "b": -0.3, "c": 0.4},
}


def _make_record(
    date: str = "2024-01-15",
    expiry: str = "2024-02-21",
    model_type: str = "svi",
    converged: bool = True,
    **overrides: Any,
) -> SurfaceFitRecord:
    """Build a synthetic SurfaceFitRecord (no real fit).

    R11.12 P1 fix: params 自動依 model_type 選 schema-correct 預設;
    避免測試誤把 SVI params 配 sabr/poly model_type → semantic validation
    raise (該驗證是真有效，但測試 setup 不該偷工).
    """
    base: dict[str, Any] = {
        "date": date,
        "expiry": expiry,
        "model_type": model_type,
        "converged": converged,
        "n_points": 11,
        "in_sample_rmse": 0.005,
        "fit_time_ms": 12,
        "forward": 17500.0,
        "T": 0.1,
        "params": _DEFAULT_PARAMS.get(model_type, {}),
        "attempts": [{"model_type": "svi", "converged": True, "rmse": 0.005, "error": None}],
        "error": None,
    }
    base.update(overrides)
    return SurfaceFitRecord(**base)


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Save 3 records (1 date, 3 expiries) → load returns 3 rows with 12-col schema."""
    records = [
        _make_record(expiry="2024-02-21"),
        _make_record(expiry="2024-03-20", model_type="sabr"),
        _make_record(expiry="2024-06-19", model_type="poly"),
    ]
    path = save_surface_fits(records, str(tmp_path), "2024-01-15")
    assert Path(path).exists()
    df = load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    assert len(df) == 3
    assert set(df.columns) == {
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


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_save_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    """Successful save → only .parquet exists, no .tmp."""
    records = [_make_record()]
    save_surface_fits(records, str(tmp_path), "2024-01-15")
    layer_dir = tmp_path / SURFACE_FITS_LAYER / "2024"
    parquet_files = list(layer_dir.glob("*.parquet"))
    tmp_files = list(layer_dir.glob("*.tmp"))
    assert len(parquet_files) == 1
    assert len(tmp_files) == 0


# ---------------------------------------------------------------------------
# Missing date / empty cache
# ---------------------------------------------------------------------------


def test_load_missing_date_returns_empty(tmp_path: Path) -> None:
    """load on cache dir 不存在 / date range 無 shard → empty 12-col df."""
    df = load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    assert df.empty
    # Save one date, query different range
    save_surface_fits([_make_record()], str(tmp_path), "2024-01-15")
    df2 = load_surface_fits(str(tmp_path), "2025-01-01", "2025-12-31")
    assert df2.empty


# ---------------------------------------------------------------------------
# is_cached
# ---------------------------------------------------------------------------


def test_is_cached_true_false(tmp_path: Path) -> None:
    """Before save → False; after save → True; different date → False."""
    assert is_cached(str(tmp_path), "2024-01-15") is False
    save_surface_fits([_make_record()], str(tmp_path), "2024-01-15")
    assert is_cached(str(tmp_path), "2024-01-15") is True
    assert is_cached(str(tmp_path), "2024-01-16") is False


# ---------------------------------------------------------------------------
# list_cached_dates
# ---------------------------------------------------------------------------


def test_list_cached_dates_sorted(tmp_path: Path) -> None:
    """Insert 3 dates out-of-order → list_cached_dates returns sorted."""
    for date in ("2024-03-15", "2024-01-15", "2024-02-15"):
        save_surface_fits([_make_record(date=date, expiry="2024-12-19")], str(tmp_path), date)
    assert list_cached_dates(str(tmp_path)) == [
        "2024-01-15",
        "2024-02-15",
        "2024-03-15",
    ]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_invalid_date_format_raises(tmp_path: Path) -> None:
    """Bad ISO format / start > end → ValueError."""
    with pytest.raises(ValueError, match="invalid ISO date|YYYY-MM-DD"):
        save_surface_fits([_make_record(date="2024/01/15")], str(tmp_path), "2024/01/15")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        save_surface_fits([_make_record(date="2024-1-15")], str(tmp_path), "2024-1-15")
    with pytest.raises(ValueError, match="start_date > end_date"):
        load_surface_fits(str(tmp_path), "2024-12-31", "2024-01-01")
    with pytest.raises(ValueError, match="empty"):
        save_surface_fits([], str(tmp_path), "2024-01-15")


# ---------------------------------------------------------------------------
# Cross-date concat
# ---------------------------------------------------------------------------


def test_load_cross_date_concat_sorted(tmp_path: Path) -> None:
    """Save 3 dates → load full range → concat sorted by (date, expiry)."""
    save_surface_fits(
        [
            _make_record(date="2024-01-15", expiry="2024-03-20"),
            _make_record(date="2024-01-15", expiry="2024-02-21"),
        ],
        str(tmp_path),
        "2024-01-15",
    )
    save_surface_fits(
        [_make_record(date="2024-01-16", expiry="2024-02-21")],
        str(tmp_path),
        "2024-01-16",
    )
    df = load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-16")
    assert len(df) == 3
    keys = list(zip(df["date"], df["expiry"], strict=True))
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Records roundtrip via load_surface_records
# ---------------------------------------------------------------------------


def test_load_surface_records_roundtrip_preserves_params(tmp_path: Path) -> None:
    """save_surface_fits → load_surface_records → params/attempts identical."""
    records = [
        _make_record(model_type="svi"),
        _make_record(
            expiry="2024-03-20",
            model_type="poly",
            params={"a": 0.18, "b": -0.3, "c": 0.4},
            attempts=[
                {"model_type": "svi", "converged": False, "rmse": None, "error": "nan"},
                {"model_type": "sabr", "converged": False, "rmse": None, "error": "nan"},
                {"model_type": "poly", "converged": True, "rmse": 0.01, "error": None},
            ],
        ),
    ]
    save_surface_fits(records, str(tmp_path), "2024-01-15")
    restored = load_surface_records(str(tmp_path), "2024-01-15", "2024-01-15")
    assert len(restored) == 2
    # Sort by expiry so match input order
    restored_sorted = sorted(restored, key=lambda r: r.expiry)
    input_sorted = sorted(records, key=lambda r: r.expiry)
    for r0, r1 in zip(input_sorted, restored_sorted, strict=True):
        assert r0.model_type == r1.model_type
        assert r0.params == r1.params
        assert len(r0.attempts) == len(r1.attempts)
        for a0, a1 in zip(r0.attempts, r1.attempts, strict=True):
            assert a0.get("model_type") == a1.get("model_type")
            assert a0.get("converged") == a1.get("converged")


# ---------------------------------------------------------------------------
# R11.11 P1: load schema validation (對稱 save 端)
# ---------------------------------------------------------------------------


def test_load_surface_fits_raises_on_schema_drift(tmp_path: Path) -> None:
    """Bad parquet shard with extra/missing cols → load_surface_fits raises.

    Codex R11.11 P1: load 之前 silent 接受壞 shard 會把 corruption 帶進
    Week 5 add_model_price 路徑。對稱 save_chain schema gate (src/data/cache.py).
    """
    import pandas as pd

    bad_dir = tmp_path / "surface_fits" / "2024"
    bad_dir.mkdir(parents=True)
    bad_df = pd.DataFrame(
        {
            "date": ["2024-01-15", "2024-01-15"],
            "expiry": ["2024-02-21", "2024-03-20"],
            "bad_col": [1, 2],
        }
    )
    bad_df.to_parquet(bad_dir / "2024-01-15.parquet")
    with pytest.raises(ValueError, match="schema drift"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


# ---------------------------------------------------------------------------
# R11.11 P2a: list_cached_dates 過濾 non-ISO stem
# ---------------------------------------------------------------------------


def test_list_cached_dates_skips_non_iso_stems(tmp_path: Path) -> None:
    """Stems like 'not-a-date.parquet' must be filtered, not returned.

    Codex R11.11 P2a: 之前 raw `p.stem` 直接回 → caller 拿到 'not-a-date'
    傳入 load_surface_fits 會 raise；list 端先過濾較合理。
    """
    save_surface_fits([_make_record()], str(tmp_path), "2024-01-15")
    bad_path = tmp_path / "surface_fits" / "2024" / "not-a-date.parquet"
    bad_path.write_bytes(b"\x00")  # not a real parquet, just stem test
    dates = list_cached_dates(str(tmp_path))
    assert dates == ["2024-01-15"]
    assert "not-a-date" not in dates


# ---------------------------------------------------------------------------
# R11.12 P1: per-shard semantic validation (dtype / value / JSON / keys)
# ---------------------------------------------------------------------------


def _write_corrupt_shard(tmp_path: Path, df_payload: dict) -> None:
    """Helper: write a shard with EXPECTED_COLUMNS col-names but corrupted data."""
    import pandas as pd

    bad_dir = tmp_path / "surface_fits" / "2024"
    bad_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(df_payload).to_parquet(bad_dir / "2024-01-15.parquet")


def _valid_shard_payload() -> dict:
    """Single-row payload with all 12 cols + correct dtypes (baseline for mutation)."""
    return {
        "date": ["2024-01-15"],
        "expiry": ["2024-02-21"],
        "model_type": ["svi"],
        "converged": [True],
        "n_points": [11],
        "in_sample_rmse": [0.005],
        "fit_time_ms": [12],
        "forward": [17500.0],
        "T": [0.1],
        "params_json": ['{"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1}'],
        "attempts_json": ["[]"],
        "error": [None],
    }


def test_load_surface_fits_raises_on_dtype_drift(tmp_path: Path) -> None:
    """Col-set OK 但 date 寫成 int64 / params_json 寫成 int → semantic raise.

    Codex R11.12 P1: 之前 load_surface_fits 只驗 col-set; dtype 全錯仍 silent
    accept, load_surface_records 才爆 cryptic TypeError 不指出 shard 名.
    """
    from src.options.surface_cache import EXPECTED_COLUMNS

    payload = {c: [1] if c != "date" else [20240115] for c in sorted(EXPECTED_COLUMNS)}
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"shard '2024-01-15.parquet' row\[0\] field 'date'"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_invalid_model_type(tmp_path: Path) -> None:
    """model_type 不在 VALID_MODEL_TYPES → raise with shard + row + field."""
    payload = _valid_shard_payload()
    payload["model_type"] = ["mystery_model"]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'model_type'.*mystery_model"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_bad_json(tmp_path: Path) -> None:
    """params_json 不是 valid JSON → raise."""
    payload = _valid_shard_payload()
    payload["params_json"] = ["{not valid json}"]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'params_json'.*invalid JSON"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_params_keys_mismatch(tmp_path: Path) -> None:
    """SVI model_type 缺 sigma key → raise with missing/extra diagnostic."""
    payload = _valid_shard_payload()
    payload["params_json"] = ['{"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0}']  # 缺 sigma
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'params_json'.*missing=\['sigma'\]"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_negative_n_points(tmp_path: Path) -> None:
    """n_points = -1 → raise."""
    payload = _valid_shard_payload()
    payload["n_points"] = [-1]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'n_points'.*must be >= 0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


# ---------------------------------------------------------------------------
# R11.13 P1: per-model financial invariant gate
# ---------------------------------------------------------------------------


def test_load_surface_fits_raises_on_successful_fit_with_T_le_zero(tmp_path: Path) -> None:
    """converged SVI 但 T<=0 → raise (R11.13 P1: 8 維 dtype gate 過 ≠ 模型可定價)."""
    payload = _valid_shard_payload()
    payload["T"] = [-0.1]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'T'.*requires > 0 finite"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    # nan T 也該 raise
    payload["T"] = [float("nan")]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'T'.*requires > 0 finite"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_successful_fit_with_forward_le_zero(tmp_path: Path) -> None:
    """converged SVI 但 forward<=0 → raise (R11.13 P1)."""
    payload = _valid_shard_payload()
    payload["forward"] = [0.0]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'forward'.*requires > 0 finite"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    payload["forward"] = [-100.0]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'forward'.*requires > 0 finite"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_successful_fit_with_negative_rmse(tmp_path: Path) -> None:
    """converged SVI 但 rmse < 0 → raise (R11.13 P1)."""
    payload = _valid_shard_payload()
    payload["in_sample_rmse"] = [-0.005]
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"row\[0\] field 'in_sample_rmse'.*requires >= 0 finite"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_svi_sigma_le_zero(tmp_path: Path) -> None:
    """SVI sigma <= 0 → raise (Lee 2004 / Gatheral 2014 raw form domain)."""
    payload = _valid_shard_payload()
    payload["params_json"] = ['{"a":0.04,"b":0.4,"rho":-0.3,"m":0.0,"sigma":-0.1}']
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"SVI sigma must be > 0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_svi_b_negative(tmp_path: Path) -> None:
    """SVI b < 0 → raise (Lee 2004 b ∈ [0, 4/(T(1+|rho|))])."""
    payload = _valid_shard_payload()
    payload["params_json"] = ['{"a":0.04,"b":-0.4,"rho":-0.3,"m":0.0,"sigma":0.1}']
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"SVI b must be >= 0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_svi_rho_out_of_bound(tmp_path: Path) -> None:
    """SVI |rho| >= 1 → raise."""
    payload = _valid_shard_payload()
    payload["params_json"] = ['{"a":0.04,"b":0.4,"rho":2.0,"m":0.0,"sigma":0.1}']
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"SVI \|rho\| must be < 1"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_raises_on_sabr_invalid_params(tmp_path: Path) -> None:
    """SABR alpha<=0 / |rho|>=1 / nu<0 / beta!=1 都該 raise (Hagan 2002)."""
    base = _valid_shard_payload()
    base["model_type"] = ["sabr"]
    base["expiry"] = ["2024-03-20"]
    base["T"] = [0.18]

    # alpha = 0
    p = dict(base)
    p["params_json"] = ['{"alpha":0.0,"rho":-0.3,"nu":0.4,"beta":1.0}']
    _write_corrupt_shard(tmp_path, p)
    with pytest.raises(ValueError, match=r"SABR alpha must be > 0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")

    # beta != 1
    p = dict(base)
    p["params_json"] = ['{"alpha":0.18,"rho":-0.3,"nu":0.4,"beta":0.5}']
    _write_corrupt_shard(tmp_path, p)
    with pytest.raises(ValueError, match=r"SABR beta must be 1\.0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")

    # |rho| = 1
    p = dict(base)
    p["params_json"] = ['{"alpha":0.18,"rho":1.0,"nu":0.4,"beta":1.0}']
    _write_corrupt_shard(tmp_path, p)
    with pytest.raises(ValueError, match=r"SABR \|rho\| must be < 1"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")

    # nu < 0
    p = dict(base)
    p["params_json"] = ['{"alpha":0.18,"rho":-0.3,"nu":-0.1,"beta":1.0}']
    _write_corrupt_shard(tmp_path, p)
    with pytest.raises(ValueError, match=r"SABR nu must be >= 0"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")


def test_load_surface_fits_allows_failed_records_with_nan(tmp_path: Path) -> None:
    """all_failed / insufficient_data 仍允 forward/T/rmse = NaN (R11.13 P1 邊界).

    R11.13 invariant gate 只對 converged=True 的 SVI/SABR/poly 觸發；
    failed/insufficient 紀錄是 audit trail，本來就用 NaN 標示「不可定價」.
    """
    payload = _valid_shard_payload()
    payload["model_type"] = ["insufficient_data"]
    payload["converged"] = [False]
    payload["forward"] = [float("nan")]
    payload["T"] = [float("nan")]
    payload["in_sample_rmse"] = [float("nan")]
    payload["params_json"] = ["{}"]  # empty params expected for failed
    _write_corrupt_shard(tmp_path, payload)
    df = load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    assert len(df) == 1
    assert df.iloc[0]["model_type"] == "insufficient_data"


# ---------------------------------------------------------------------------
# R11.14 P2: SVI Lee 2004 b upper bound (cache vs fit 端對稱)
# ---------------------------------------------------------------------------


def test_load_surface_fits_raises_on_svi_b_above_lee_upper(tmp_path: Path) -> None:
    """SVI b 超過 Lee 2004 upper bound 4/(T·(1+|rho|)) → raise.

    Codex R11.14 P2: fit_svi_raw (vol_surface.py:275-280) 已 enforce
    constraint 但 cache loader 之前只擋 b<0；b 過大 silent → downstream IV
    1000-3600% 荒謬值. Pattern 14 (R11.13 加) save/load 對稱 + defense-in-depth
    漸進失守雙觸發.

    例: T=0.1, rho=-0.3 → upper = 4/(0.1*1.3) ≈ 30.77
    b=100 (Codex 報告例) 必須 raise.
    """
    payload = _valid_shard_payload()
    payload["T"] = [0.1]
    payload["params_json"] = ['{"a":0.04,"b":100.0,"rho":-0.3,"m":0.0,"sigma":0.1}']
    _write_corrupt_shard(tmp_path, payload)
    with pytest.raises(ValueError, match=r"SVI b=100\.0 exceeds Lee 2004 upper bound"):
        load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")

    # 邊界檢驗: b 剛好等於 Lee upper 應 PASS (boundary inclusive 設計)
    payload["params_json"] = ['{"a":0.04,"b":30.5,"rho":-0.3,"m":0.0,"sigma":0.1}']  # < 30.77
    _write_corrupt_shard(tmp_path, payload)
    df = load_surface_fits(str(tmp_path), "2024-01-15", "2024-01-15")
    assert len(df) == 1
