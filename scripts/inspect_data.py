"""Quick local sanity-check of the cached TAIFEX / TAIEX data.

對本機 cache 跑一個簡短的人工巡檢：TAIEX spot CSV 範圍、單日 TXO chain 形狀、
某月份 batch 讀取的 row 數。所有路徑相對 repo root 解析；缺檔時印提示而非 traceback
（raw / strategy_view / taiex_daily.csv 皆為 gitignored，需先用 loader 重建）。

用法：
    python scripts/inspect_data.py
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
TAIEX_CSV = REPO_ROOT / "data" / "taiex_daily.csv"
STRATEGY_VIEW = REPO_ROOT / "data" / "taifex_cache" / "strategy_view"


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def inspect_taiex() -> pd.DataFrame | None:
    _hr("TAIEX Spot Data")
    if not TAIEX_CSV.exists():
        print(f"(missing {TAIEX_CSV.relative_to(REPO_ROOT)} — run scripts/fetch_taiex.py)")
        return None
    taiex = pd.read_csv(TAIEX_CSV, parse_dates=["date"]).set_index("date")
    print(f"Date range: {taiex.index.min().date()} ~ {taiex.index.max().date()}")
    print(f"Total rows: {len(taiex)}")
    print("\nLast 5 days:")
    print(taiex.tail().to_string())
    return taiex


def _latest_shard() -> Path | None:
    if not STRATEGY_VIEW.exists():
        return None
    shards = sorted(STRATEGY_VIEW.rglob("*.parquet"))
    return shards[-1] if shards else None


def inspect_chain(taiex: pd.DataFrame | None) -> None:
    shard = _latest_shard()
    if shard is None:
        _hr("TXO Chain")
        print(
            f"(no parquet under {STRATEGY_VIEW.relative_to(REPO_ROOT)} — "
            "rebuild via taifex_loader.backfill_range)"
        )
        return

    day = shard.stem
    _hr(f"TXO Chain ({day})")
    chain = pd.read_parquet(shard)
    print(f"Contracts: {len(chain)}")
    print(f"Columns: {chain.columns.tolist()}")

    spot: float | None = None
    if taiex is not None and pd.Timestamp(day) in taiex.index:
        spot = float(taiex.loc[day, "close"])  # type: ignore[arg-type]
        print(f"\nTAIEX = {spot:.2f}")

    liquid = chain[chain["volume"] > 0] if "volume" in chain.columns else chain
    if spot is not None:
        lo, hi = round(spot * 0.92, -2), round(spot * 1.08, -2)
        liquid = liquid[(liquid["strike"] >= lo) & (liquid["strike"] <= hi)]
    liquid = liquid.sort_values(["expiry", "option_type", "strike"])
    print(f"\nLiquid contracts shown: {len(liquid)} rows")
    print(liquid.head(20).to_string())

    # Batch read the shard's own month.
    year_month = day[:7]  # YYYY-MM
    _hr(f"Batch Read — {year_month}")
    files = sorted(glob.glob(str(shard.parent / f"{year_month}-*.parquet")))
    print(f"Found {len(files)} trading days")
    if files:
        month_chain = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
        print(f"Total rows merged: {len(month_chain)}")
        if "date" in month_chain.columns:
            print(
                f"Date span: {month_chain['date'].min().date()} ~ "
                f"{month_chain['date'].max().date()}"
            )


def main() -> None:
    taiex = inspect_taiex()
    inspect_chain(taiex)


if __name__ == "__main__":
    main()
