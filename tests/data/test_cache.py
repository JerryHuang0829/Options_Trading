"""Tests for src/data/cache.py — Day 3 parquet two-layer cache (D-soft v6).

R10.7「實測再寫」紀律：raw 用 parse_bulletin(PRE_FIXTURE) 真實 20-col；
strategy_view 用 to_strategy_view 後 10-col；schema drift 用人工裁減 col.

Coverage:
  - save_chain: empty/invalid layer/drift raise，raw 與 strategy_view 兩層 round-trip
  - save_chain atomic: 成功後 tmp 不殘留
  - load_chain: 範圍篩選、排序、columns projection、invalid range raise、空回傳
  - is_cached / list_cached_dates: per-layer isolation + 排序
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data.cache import (
    is_cached,
    list_cached_dates,
    load_chain,
    save_chain,
)
from src.data.schema import (
    RAW_TAIFEX_COLUMNS_PRE_20251208,
    STRATEGY_VIEW_COLUMN_ORDER,
)
from src.data.taifex_loader import parse_bulletin

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PRE_FIXTURE = FIXTURE_DIR / "taifex_2024_01_02_pre_20251208_sample.csv"


def _raw_df() -> pd.DataFrame:
    """20-col PRE raw DataFrame from real fixture."""
    return parse_bulletin(str(PRE_FIXTURE))


def _strategy_view_df(date: str) -> pd.DataFrame:
    """Synthetic 10-col STRATEGY_VIEW row matching schema.STRATEGY_VIEW_COLUMN_ORDER."""
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "expiry": pd.Timestamp("2024-01-17"),
                "strike": 17500.0,
                "option_type": "call",
                "settle": 120.0,
                "close": 118.0,
                "bid": 117.0,
                "ask": 122.0,
                "volume": 100,
                "open_interest": 500,
            },
            {
                "date": pd.Timestamp(date),
                "expiry": pd.Timestamp("2024-01-17"),
                "strike": 17500.0,
                "option_type": "put",
                "settle": 80.0,
                "close": 79.0,
                "bid": 78.0,
                "ask": 82.0,
                "volume": 90,
                "open_interest": 400,
            },
        ],
        columns=STRATEGY_VIEW_COLUMN_ORDER,
    )


# ---------------------------------------------------------------------------
# save_chain — input validation
# ---------------------------------------------------------------------------


def test_save_chain_invalid_layer_raises(tmp_path: Path) -> None:
    df = _strategy_view_df("2024-01-02")
    with pytest.raises(ValueError, match="layer must be"):
        save_chain(df, str(tmp_path), "2024-01-02", layer="bogus")  # type: ignore[arg-type]


def test_save_chain_empty_df_raises(tmp_path: Path) -> None:
    empty = pd.DataFrame(columns=STRATEGY_VIEW_COLUMN_ORDER)
    with pytest.raises(ValueError, match="empty"):
        save_chain(empty, str(tmp_path), "2024-01-02", layer="strategy_view")


def test_save_chain_strategy_view_schema_drift_raises(tmp_path: Path) -> None:
    """STRATEGY_VIEW expects 10 cols；裁掉 'volume' → drift raise (set comparison)."""
    df = _strategy_view_df("2024-01-02").drop(columns=["volume"])
    with pytest.raises(ValueError, match="schema drift"):
        save_chain(df, str(tmp_path), "2024-01-02", layer="strategy_view")


def test_save_chain_raw_schema_drift_raises(tmp_path: Path) -> None:
    """raw 接受 PRE(20) 或 POST(21)；裁掉 'settle' → 兩 set 都不等 → drift raise."""
    df = _raw_df().drop(columns=["settle"])
    with pytest.raises(ValueError, match="schema drift"):
        save_chain(df, str(tmp_path), "2024-01-02", layer="raw")


# ---------------------------------------------------------------------------
# save_chain — successful round-trip per layer
# ---------------------------------------------------------------------------


def test_save_chain_raw_layer_round_trip(tmp_path: Path) -> None:
    df = _raw_df()
    out_path = save_chain(df, str(tmp_path), "2024-01-02", layer="raw")
    expected_path = tmp_path / "raw" / "2024" / "2024-01-02.parquet"
    assert Path(out_path) == expected_path.resolve()
    assert expected_path.exists()
    # round-trip
    loaded = pd.read_parquet(expected_path, engine="pyarrow")
    assert set(loaded.columns) == set(RAW_TAIFEX_COLUMNS_PRE_20251208)
    assert len(loaded) == len(df)


def test_save_chain_strategy_view_layer_round_trip(tmp_path: Path) -> None:
    df = _strategy_view_df("2024-01-02")
    out_path = save_chain(df, str(tmp_path), "2024-01-02", layer="strategy_view")
    expected_path = tmp_path / "strategy_view" / "2024" / "2024-01-02.parquet"
    assert Path(out_path) == expected_path.resolve()
    loaded = pd.read_parquet(expected_path, engine="pyarrow")
    assert set(loaded.columns) == set(STRATEGY_VIEW_COLUMN_ORDER)
    assert len(loaded) == 2


def test_save_chain_year_folder_split_isolation(tmp_path: Path) -> None:
    """跨年 shard 落在不同年度目錄 (year-shard 守護)."""
    save_chain(_strategy_view_df("2024-12-31"), str(tmp_path), "2024-12-31", layer="strategy_view")
    save_chain(_strategy_view_df("2025-01-02"), str(tmp_path), "2025-01-02", layer="strategy_view")
    assert (tmp_path / "strategy_view" / "2024" / "2024-12-31.parquet").exists()
    assert (tmp_path / "strategy_view" / "2025" / "2025-01-02.parquet").exists()
    # year folders are siblings, neither shard collides
    assert sorted(d.name for d in (tmp_path / "strategy_view").iterdir() if d.is_dir()) == [
        "2024",
        "2025",
    ]


def test_save_chain_atomic_leaves_no_tmp_file(tmp_path: Path) -> None:
    """成功 save 後不應留下 .tmp 殘檔 (R10.10 atomic write 守護)."""
    df = _strategy_view_df("2024-01-02")
    save_chain(df, str(tmp_path), "2024-01-02", layer="strategy_view")
    year_dir = tmp_path / "strategy_view" / "2024"
    tmp_files = list(year_dir.glob("*.tmp"))
    assert tmp_files == [], f"unexpected residual tmp files: {tmp_files}"


def test_save_chain_overwrites_existing_shard(tmp_path: Path) -> None:
    """Re-save same (date, layer) overwrites — 用於 backfill_range force=True 場景."""
    df_a = _strategy_view_df("2024-01-02").assign(strike=17500.0)
    df_b = _strategy_view_df("2024-01-02").assign(strike=18000.0)
    save_chain(df_a, str(tmp_path), "2024-01-02", layer="strategy_view")
    save_chain(df_b, str(tmp_path), "2024-01-02", layer="strategy_view")
    loaded = pd.read_parquet(tmp_path / "strategy_view" / "2024" / "2024-01-02.parquet")
    assert (loaded["strike"] == 18000.0).all()


# ---------------------------------------------------------------------------
# load_chain
# ---------------------------------------------------------------------------


def test_load_chain_returns_empty_when_layer_dir_missing(tmp_path: Path) -> None:
    """No <cache_dir>/<layer>/ → empty df, NOT raise (caller decides)."""
    out = load_chain(str(tmp_path), "2024-01-01", "2024-01-31", layer="strategy_view")
    assert out.empty


def test_load_chain_invalid_range_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="start_date > end_date"):
        load_chain(str(tmp_path), "2024-02-01", "2024-01-01", layer="strategy_view")


def test_load_chain_concat_sorted_by_date(tmp_path: Path) -> None:
    """3 個 shard 亂序存，load 後依 date 排序."""
    save_chain(_strategy_view_df("2024-01-10"), str(tmp_path), "2024-01-10", layer="strategy_view")
    save_chain(_strategy_view_df("2024-01-02"), str(tmp_path), "2024-01-02", layer="strategy_view")
    save_chain(_strategy_view_df("2024-01-05"), str(tmp_path), "2024-01-05", layer="strategy_view")
    out = load_chain(str(tmp_path), "2024-01-01", "2024-01-31", layer="strategy_view")
    dates_in_order = out["date"].tolist()
    assert dates_in_order == sorted(dates_in_order)
    assert len(out) == 6  # 3 shards × 2 rows each


def test_load_chain_range_filters_outside_dates(tmp_path: Path) -> None:
    """超出 [start, end] 的 shard 不應載入."""
    save_chain(_strategy_view_df("2024-01-02"), str(tmp_path), "2024-01-02", layer="strategy_view")
    save_chain(_strategy_view_df("2024-01-15"), str(tmp_path), "2024-01-15", layer="strategy_view")
    save_chain(_strategy_view_df("2024-02-01"), str(tmp_path), "2024-02-01", layer="strategy_view")
    out = load_chain(str(tmp_path), "2024-01-10", "2024-01-31", layer="strategy_view")
    unique_dates = sorted(out["date"].dt.strftime("%Y-%m-%d").unique())
    assert unique_dates == ["2024-01-15"]


def test_load_chain_columns_projection(tmp_path: Path) -> None:
    """columns kwarg → pyarrow column projection (省 IO)."""
    save_chain(_strategy_view_df("2024-01-02"), str(tmp_path), "2024-01-02", layer="strategy_view")
    out = load_chain(
        str(tmp_path),
        "2024-01-01",
        "2024-01-31",
        layer="strategy_view",
        columns=["date", "strike"],
    )
    assert set(out.columns) == {"date", "strike"}


def test_load_chain_skips_non_iso_filenames(tmp_path: Path) -> None:
    """year-folder 內 .parquet 檔名非 YYYY-MM-DD → silent skip (非 raise)."""
    save_chain(_strategy_view_df("2024-01-02"), str(tmp_path), "2024-01-02", layer="strategy_view")
    # Plant a misnamed sibling file inside the year folder (where glob looks)
    (tmp_path / "strategy_view" / "2024" / "garbage.parquet").write_bytes(b"not parquet")
    out = load_chain(str(tmp_path), "2024-01-01", "2024-01-31", layer="strategy_view")
    assert len(out) == 2  # garbage skipped, real shard loaded


# ---------------------------------------------------------------------------
# is_cached / list_cached_dates — layer isolation
# ---------------------------------------------------------------------------


def test_is_cached_per_layer_isolation(tmp_path: Path) -> None:
    """raw layer 有 2024-01-02 不代表 strategy_view 層也有."""
    save_chain(_raw_df(), str(tmp_path), "2024-01-02", layer="raw")
    assert is_cached(str(tmp_path), "2024-01-02", layer="raw") is True
    assert is_cached(str(tmp_path), "2024-01-02", layer="strategy_view") is False


def test_list_cached_dates_returns_sorted(tmp_path: Path) -> None:
    save_chain(_strategy_view_df("2024-03-10"), str(tmp_path), "2024-03-10", layer="strategy_view")
    save_chain(_strategy_view_df("2024-01-05"), str(tmp_path), "2024-01-05", layer="strategy_view")
    save_chain(_strategy_view_df("2024-02-15"), str(tmp_path), "2024-02-15", layer="strategy_view")
    dates = list_cached_dates(str(tmp_path), layer="strategy_view")
    assert dates == ["2024-01-05", "2024-02-15", "2024-03-10"]


def test_list_cached_dates_empty_when_dir_missing(tmp_path: Path) -> None:
    assert list_cached_dates(str(tmp_path), layer="raw") == []
