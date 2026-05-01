"""Parquet cache layer (D-soft Day 3, R10.13 v6).

Two-layer cache (R10.8 P2-#5 修法 + plan v6 §2 schema versioning):

  - layer='raw' (~20/21-col, parse_bulletin output, contains 全 contracts)
  - layer='strategy_view' (10-col TXO regular monthly, to_strategy_view output)

Date-sharded — one parquet per (date, layer):
  <cache_dir>/<layer>/<YYYY-MM-DD>.parquet

Schema versioning per layer (CACHE_LAYOUT_VERSION_RAW / _STRATEGY_VIEW)
allows independent invalidation when one layer's schema evolves (e.g.
2025-12-08 raw schema added contract_date but strategy_view 10-col stayed).

Atomic write: tmp → rename (POSIX atomic) avoids partial-shard corruption
on kill / power loss mid-write.

Schema drift detect: save_chain compares incoming df.columns set against
existing shard (if any) for same date+layer; mismatch → raise (R10.10
P2-#5 三維度 gate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from src.data.schema import (
    RAW_TAIFEX_COLUMNS_OLDEST,
    RAW_TAIFEX_COLUMNS_POST_20251208,
    RAW_TAIFEX_COLUMNS_PRE_20251208,
    STRATEGY_VIEW_COLUMNS,
)

LayerName = Literal["raw", "strategy_view"]

# 預期 columns set per layer (drift gate 用)
_EXPECTED_COLS_PER_LAYER: dict[str, set[frozenset[str]]] = {
    # raw layer 接受 oldest 18 / pre 20 / post 21 col (3-way schema versioning)
    "raw": {
        RAW_TAIFEX_COLUMNS_OLDEST,
        RAW_TAIFEX_COLUMNS_PRE_20251208,
        RAW_TAIFEX_COLUMNS_POST_20251208,
    },
    "strategy_view": {STRATEGY_VIEW_COLUMNS},
}


def _validate_layer(layer: str) -> None:
    if layer not in ("raw", "strategy_view"):
        raise ValueError(f"layer must be 'raw'|'strategy_view', got {layer!r}")


def _shard_path(cache_dir: str, date: str, layer: LayerName) -> Path:
    """Year-sharded path: <cache_dir>/<layer>/<YYYY>/<YYYY-MM-DD>.parquet.

    Year folder split so 7yr × ~245 trading day = ~1700 shards don't collapse
    into one fs directory (file system + glob enumeration both faster).
    """
    year = date[:4]
    return Path(cache_dir) / layer / year / f"{date}.parquet"


def save_chain(
    df: pd.DataFrame,
    cache_dir: str,
    date: str,
    *,
    layer: LayerName,
) -> str:
    """Write one trading day's chain to <cache_dir>/<layer>/<date>.parquet.

    Atomic: writes to .tmp first, then os.rename → avoids partial corruption.
    Drift gate: validates df.columns matches expected schema for layer.

    Args:
        df: DataFrame to persist (non-empty).
        cache_dir: cache root.
        date: ISO 'YYYY-MM-DD'.
        layer: 'raw' (parse_bulletin output) or 'strategy_view'.

    Returns: absolute path string.

    Raises:
        ValueError: empty df / invalid layer / schema drift.
    """
    _validate_layer(layer)
    if df.empty:
        raise ValueError(f"save_chain: df is empty (date={date}, layer={layer})")

    # Schema drift gate (R10.10 P2-#5 set comparison NOT count)
    actual_cols = set(df.columns)
    accepted = _EXPECTED_COLS_PER_LAYER[layer]
    if not any(actual_cols == set(expected) for expected in accepted):
        accepted_lists = [sorted(s) for s in accepted]
        raise ValueError(
            f"save_chain: schema drift (date={date}, layer={layer}). "
            f"actual_cols={sorted(actual_cols)}, accepted_one_of={accepted_lists}"
        )

    # Build path + atomic write
    path = _shard_path(cache_dir, date, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp_path, engine="pyarrow", index=False)
    tmp_path.replace(path)
    return str(path.resolve())


def load_chain(
    cache_dir: str,
    start_date: str,
    end_date: str,
    *,
    layer: LayerName,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Read shards in [start, end] inclusive, concat sorted by date.

    Missing dates: silent skip (caller decides if gap is acceptable;
    use list_cached_dates() to inspect).

    Args:
        cache_dir: cache root.
        start_date / end_date: ISO 'YYYY-MM-DD' inclusive.
        layer: 'raw' or 'strategy_view'.
        columns: optional subset for column projection (pyarrow filter, fast).

    Returns: concatenated DataFrame sorted by date (empty if no shards in range).
    """
    _validate_layer(layer)
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    if start_ts > end_ts:
        raise ValueError(f"load_chain: start_date > end_date ({start_date} > {end_date})")

    layer_dir = Path(cache_dir) / layer
    if not layer_dir.exists():
        return pd.DataFrame()

    shards = []
    # Iterate only year folders overlapping [start, end] (faster than full glob)
    for year in range(start_ts.year, end_ts.year + 1):
        year_dir = layer_dir / str(year)
        if not year_dir.exists():
            continue
        for shard_path in sorted(year_dir.glob("*.parquet")):
            date_str = shard_path.stem
            try:
                shard_ts = pd.Timestamp(date_str)
            except (ValueError, TypeError):
                continue  # not a YYYY-MM-DD parquet shard; skip
            if start_ts <= shard_ts <= end_ts:
                shards.append(pd.read_parquet(shard_path, engine="pyarrow", columns=columns))

    if not shards:
        return pd.DataFrame()
    # Codex R11.2 P (跨 schema concat outer-join NaN 修法):
    # raw layer 接受 OLDEST(18) / PRE(20) / POST(21) 三種 col 數。
    # pd.concat 對不同 schema 預設 outer join → 塞大量 NaN col (e.g. 跨 2018-2026
    # contract_date NaN rate ~99%). 對 caller 透明後接 BSM/IV solver 會炸或污染.
    # → 偵測 col set 不一致 → raise，逼 caller 用 layer='strategy_view' (10-col 不變)
    # 或自行縮 date 範圍對齊單一 schema 版本.
    col_sets = [frozenset(s.columns) for s in shards]
    if len(set(col_sets)) > 1:
        unique_col_counts = sorted({len(s) for s in set(col_sets)})
        raise ValueError(
            f"load_chain: cross-schema concat detected (layer={layer}, "
            f"date_range=[{start_date}, {end_date}]). Shards span {len(set(col_sets))} "
            f"schemas (col counts: {unique_col_counts}). Use layer='strategy_view' for "
            f"cross-version queries (10-col 不變), or narrow date range to single "
            f"schema window. See src/data/schema.py RAW_TAIFEX_COLUMNS_OLDEST/PRE/POST."
        )
    return pd.concat(shards, ignore_index=True).sort_values("date").reset_index(drop=True)


def is_cached(cache_dir: str, date: str, *, layer: LayerName) -> bool:
    """O(1) existence check for cached shard."""
    _validate_layer(layer)
    return _shard_path(cache_dir, date, layer).exists()


def list_cached_dates(cache_dir: str, *, layer: LayerName) -> list[str]:
    """Return sorted ISO dates of cached shards in given layer (year-sharded)."""
    _validate_layer(layer)
    layer_dir = Path(cache_dir) / layer
    if not layer_dir.exists():
        return []
    return sorted(p.stem for p in layer_dir.glob("*/*.parquet"))
