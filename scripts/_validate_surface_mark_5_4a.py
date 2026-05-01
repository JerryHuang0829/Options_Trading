"""Week 5 Day 5.4a — 1-year sub-set 真資料 fit + cache + add_model_price 統計.

D-soft pivot 的核心驗證日 — 是 R11.10/11/12/13/14 留下 5 件 prerequisite 中
4 件 (SVI 收斂率 / arb-free 收斂率 / multiprocessing 真規模 / Trend Micro AV)
首次 1-year 真資料實證.

Day 5.4a Steps (Pattern 0 攻擊預期):
  1. Load 真 cache 2024-04-01 → 2025-04-01 (~245 trading days)
  2. enrich_pipeline (Phase 1: dte/underlying/q_pit; Phase 2: iv/delta/can_*)
  3. batch_fit_surface(n_workers=4) — 首次 multiprocessing 真規模
  4. save_surface_fits per date (持久化)
  5. add_model_price + 填補率統計 → CSV

Day 5.4b 留 (5 步驗): 3 scenario backtest 對比 + Pro 矩陣 6 項.

紀錄產出:
  - reports/day_5_4a_run_meta.json (start/end/n_workers/git/AV 行為)
  - reports/day_5_4a_convergence.csv (per-day per-expiry model_type / converged / RMSE)
  - reports/day_5_4a_model_price_fill_rate.csv (NaN bid/ask 行 model_price 填補率)
  - reports/day_5_4a_summary.md (Pro 矩陣 #1-4 計算; #5-6 留 Day 5.4b)
  - data/taifex_cache/surface_fits/ (per-date parquet shards)

CLI:
  python scripts/_validate_surface_mark_5_4a.py
  python scripts/_validate_surface_mark_5_4a.py --start 2024-04-01 --end 2024-05-01  # smoke
  python scripts/_validate_surface_mark_5_4a.py --n-workers 1  # AV fallback

Pattern 0 攻擊向量 acknowledged:
  #1 真 cache 完整: ✅ 2024 242 / 2025 243 shards (>= 245 needed)
  #3 multiprocessing AV: 預設 n_workers=4; 提供 --n-workers=1 fallback
  #4 SVI 收斂率 < 60%: 不 abort, 紀錄進 CSV + summary 標 Pattern 13 trigger
  #5 add_model_price 真規模時間: 估 5-15 min; 紀錄進 run_meta
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Bootstrap repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from src.data.cache import load_chain  # noqa: E402
from src.data.enrich import add_model_price, enrich_pipeline  # noqa: E402
from src.options.surface_batch import batch_fit_surface  # noqa: E402
from src.options.surface_cache import save_surface_fits  # noqa: E402

CACHE_DIR = _REPO_ROOT / "data" / "taifex_cache"
TAIEX_CSV = _REPO_ROOT / "data" / "taiex_daily.csv"
REPORTS_DIR = _REPO_ROOT / "reports"
SURFACE_CACHE_DIR = str(CACHE_DIR)


def _load_taiex_spot(start: str, end: str) -> pd.Series:
    """Load TAIEX close as Series indexed by date for spot_series argument."""
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values), name="spot")


def _section(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Day 5.4a — 1-year sub-set surface validation")
    parser.add_argument("--start", default="2024-04-01", help="ISO start date")
    parser.add_argument("--end", default="2025-04-01", help="ISO end date")
    parser.add_argument(
        "--n-workers", type=int, default=4, help="batch_fit_surface workers (1 for AV fallback)"
    )
    parser.add_argument(
        "--save-cache",
        action="store_true",
        help="Persist surface fits to data/taifex_cache/surface_fits/",
    )
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_start = time.perf_counter()
    run_meta: dict = {
        "script": "_validate_surface_mark_5_4a.py",
        "start_date": args.start,
        "end_date": args.end,
        "n_workers": args.n_workers,
        "save_cache": args.save_cache,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "phase_durations_sec": {},
    }

    # ----------------------------------------------------------------
    # Step 1: Load real chain from cache
    # ----------------------------------------------------------------
    _section(f"Step 1: load_chain {args.start} → {args.end}")
    t0 = time.perf_counter()
    chain = load_chain(SURFACE_CACHE_DIR, args.start, args.end, layer="strategy_view")
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step1_load_chain"] = round(t1 - t0, 2)
    n_days = chain["date"].nunique() if len(chain) else 0
    print(f"  loaded {len(chain):,} rows / {n_days} unique dates / {t1 - t0:.1f}s", flush=True)
    if chain.empty:
        print(f"FAIL: no shards in [{args.start}, {args.end}]", flush=True)
        return 1

    # R12.3 P fix (Codex audit during 5yr cache extend): real TAIFEX cache 在
    # post-expiry 後仍可能含已過期合約 settlement record (e.g. 2023-01-30 cache
    # 含 expiry=2023-01-18 row, 12-day post-expiry). enrich_pipeline.add_dte
    # 對 dte<0 raise. 5yr 範圍會碰到多次過期合約 — 先 filter (expiry >= date)
    # 再 enrich. 1-yr clean range (Day 5.4a 2024-04→2025-04) 沒打到此 path
    # (Pattern 9 fixture 真資料邊界沒覆蓋).
    pre_filter_rows = len(chain)
    chain = chain[chain["expiry"] >= chain["date"]].reset_index(drop=True)
    n_filtered = pre_filter_rows - len(chain)
    if n_filtered > 0:
        print(
            f"  filtered {n_filtered:,} post-expiry rows (expiry < date); "
            f"remaining {len(chain):,} rows",
            flush=True,
        )
    run_meta["chain_n_rows"] = int(len(chain))
    run_meta["chain_n_rows_filtered_postexpiry"] = int(n_filtered)
    run_meta["chain_n_dates"] = int(n_days)
    run_meta["chain_n_expiries"] = int(chain["expiry"].nunique())

    # ----------------------------------------------------------------
    # Step 2: enrich_pipeline (Phase 1 + Phase 2)
    # ----------------------------------------------------------------
    _section("Step 2: enrich_pipeline (Phase 1: dte/underlying/q_pit; Phase 2: iv/delta)")
    t0 = time.perf_counter()
    spot_series = _load_taiex_spot(args.start, args.end)
    print(f"  TAIEX spot: {len(spot_series)} days", flush=True)
    enriched, q_pit_audit = enrich_pipeline(
        chain,
        spot_series,
        spot_missing_policy="forward_fill",  # R11.6 P1: 3 days gap exist (TXO ahead of TAIEX)
        on_iv_solver_fail="nan",
    )
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step2_enrich"] = round(t1 - t0, 2)
    iv_fill_rate = float(enriched["iv"].notna().mean())
    print(
        f"  enriched: {len(enriched):,} rows / IV fill rate {iv_fill_rate:.1%} / {t1 - t0:.1f}s",
        flush=True,
    )
    run_meta["enriched_iv_fill_rate"] = round(iv_fill_rate, 4)
    run_meta["q_pit_audit_n_days"] = int(len(q_pit_audit))

    # ----------------------------------------------------------------
    # Step 3: batch_fit_surface
    # ----------------------------------------------------------------
    _section(f"Step 3: batch_fit_surface(n_workers={args.n_workers})")
    t0 = time.perf_counter()
    fit_records = batch_fit_surface(
        chain=enriched,
        n_workers=args.n_workers,
        min_strikes=5,
        arb_free_svi=True,
    )
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step3_batch_fit"] = round(t1 - t0, 2)
    n_groups = len(fit_records)
    n_converged = sum(1 for r in fit_records if r.converged)
    print(
        f"  {n_groups} (date, expiry) groups / {n_converged} converged / "
        f"{t1 - t0:.1f}s ({(t1 - t0) / max(n_groups, 1) * 1000:.1f} ms/group)",
        flush=True,
    )
    run_meta["fit_n_groups"] = n_groups
    run_meta["fit_n_converged"] = n_converged

    # Convergence per model_type breakdown
    convergence_rows = []
    for r in fit_records:
        convergence_rows.append(
            {
                "date": r.date,
                "expiry": r.expiry,
                "model_type": r.model_type,
                "converged": r.converged,
                "n_points": r.n_points,
                "in_sample_rmse": r.in_sample_rmse,
                "fit_time_ms": r.fit_time_ms,
                "T": r.T,
                "forward": r.forward,
                "error": r.error,
            }
        )
    convergence_df = pd.DataFrame(convergence_rows)
    conv_csv = REPORTS_DIR / "day_5_4a_convergence.csv"
    convergence_df.to_csv(conv_csv, index=False)
    print(f"  → {conv_csv}", flush=True)

    # Pro 矩陣 #1: per-model_type 收斂率
    # R11.15 P1 fix (Codex): 報雙指標避免混淆 —
    #   svi_share = SVI groups / total groups (true group-level coverage)
    #   svi_internal_rate = converged SVI / total SVI groups (always 100% per dispatch)
    # 之前只報後者 → 永遠 100% 是 silent metric definition 錯位.
    model_type_stats = (
        convergence_df.groupby("model_type")
        .agg(
            n=("converged", "size"),
            n_converged=("converged", "sum"),
            rmse_median=("in_sample_rmse", "median"),
        )
        .reset_index()
    )
    model_type_stats["rate"] = model_type_stats["n_converged"] / model_type_stats["n"]
    print("\n  Pro 矩陣 #1 — model_type breakdown:", flush=True)
    print(model_type_stats.to_string(index=False), flush=True)
    n_total_groups = int(len(convergence_df))
    n_svi_groups = int((convergence_df["model_type"] == "svi").sum())
    n_svi_converged = int(
        ((convergence_df["model_type"] == "svi") & convergence_df["converged"]).sum()
    )
    svi_share = n_svi_groups / n_total_groups if n_total_groups > 0 else 0.0
    svi_internal_rate = n_svi_converged / n_svi_groups if n_svi_groups > 0 else 0.0
    # Pro 標準: SVI share 是 group-level dispatch coverage; ≥ 60% 達標 (Codex R11.10 prereq #1)
    svi_rate = svi_share  # 對齊 plan 定義「SVI 收斂率 ≥ 60%」 — group-level share
    print(
        f"\n  Pro 矩陣 #1 — SVI share (groups dispatched to SVI): "
        f"{n_svi_groups}/{n_total_groups} = {svi_share:.4f}",
        flush=True,
    )
    print(
        f"  Pro 矩陣 #1 — SVI internal convergence rate (informational): "
        f"{n_svi_converged}/{n_svi_groups} = {svi_internal_rate:.4f}",
        flush=True,
    )
    run_meta["pro_matrix_1_svi_share"] = round(svi_share, 4)  # group-level coverage (R11.15 P1)
    run_meta["pro_matrix_1_svi_internal_rate"] = round(svi_internal_rate, 4)  # converged within SVI
    run_meta["pro_matrix_1_svi_rate"] = round(svi_rate, 4)  # alias for svi_share (back-compat key)
    run_meta["pro_matrix_1_target"] = 0.60
    run_meta["pro_matrix_1_pass"] = bool(svi_share >= 0.60)

    # Pro 矩陣 #3: in-sample IV RMSE 中位數 (converged 才算)
    converged_rmse = convergence_df.loc[
        convergence_df["converged"] & convergence_df["in_sample_rmse"].notna(), "in_sample_rmse"
    ]
    rmse_median = float(converged_rmse.median()) if not converged_rmse.empty else float("nan")
    print(
        f"\n  Pro 矩陣 #3 — converged in-sample RMSE median: {rmse_median:.4f} (target < 0.02)",
        flush=True,
    )
    run_meta["pro_matrix_3_rmse_median"] = round(rmse_median, 6) if pd.notna(rmse_median) else None
    run_meta["pro_matrix_3_pass"] = bool(rmse_median < 0.02) if pd.notna(rmse_median) else None

    # ----------------------------------------------------------------
    # Step 4: save_surface_fits per date (optional)
    # ----------------------------------------------------------------
    if args.save_cache:
        _section("Step 4: save_surface_fits per date")
        t0 = time.perf_counter()
        # Group records by date and save per-date shard
        records_by_date: dict[str, list] = {}
        for r in fit_records:
            records_by_date.setdefault(r.date, []).append(r)
        for date_str, recs in records_by_date.items():
            save_surface_fits(recs, SURFACE_CACHE_DIR, date_str)
        t1 = time.perf_counter()
        run_meta["phase_durations_sec"]["step4_save_cache"] = round(t1 - t0, 2)
        print(f"  saved {len(records_by_date)} date shards / {t1 - t0:.1f}s", flush=True)
        run_meta["surface_cache_dates_saved"] = len(records_by_date)
    else:
        print("Step 4: --save-cache not set; skipping persist", flush=True)

    # ----------------------------------------------------------------
    # Step 5: add_model_price + 填補率統計
    # ----------------------------------------------------------------
    _section("Step 5: add_model_price + 填補率統計")
    t0 = time.perf_counter()
    enriched_with_mp = add_model_price(enriched, fit_records)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step5_add_model_price"] = round(t1 - t0, 2)

    # 填補率: NaN bid/ask 行被 model_price 填的比例
    nan_quote = enriched_with_mp["bid"].isna() | enriched_with_mp["ask"].isna()
    n_nan_quote = int(nan_quote.sum())
    n_nan_quote_with_mp = int((nan_quote & enriched_with_mp["model_price"].notna()).sum())
    fill_rate = n_nan_quote_with_mp / n_nan_quote if n_nan_quote > 0 else 0.0
    print(f"  rows with NaN bid/ask: {n_nan_quote:,}", flush=True)
    print(f"  of which model_price filled: {n_nan_quote_with_mp:,} ({fill_rate:.1%})", flush=True)
    print(
        f"  add_model_price total time: {t1 - t0:.1f}s ("
        f"{(t1 - t0) / max(len(enriched_with_mp), 1) * 1e6:.1f} μs/row)",
        flush=True,
    )

    fill_csv = REPORTS_DIR / "day_5_4a_model_price_fill_rate.csv"
    fill_summary = pd.DataFrame(
        [
            {
                "n_total_rows": len(enriched_with_mp),
                "n_rows_with_nan_quote": n_nan_quote,
                "n_rows_with_nan_quote_filled_by_model": n_nan_quote_with_mp,
                "fill_rate": fill_rate,
                "n_total_with_model_price": int(enriched_with_mp["model_price"].notna().sum()),
            }
        ]
    )
    fill_summary.to_csv(fill_csv, index=False)
    print(f"  → {fill_csv}", flush=True)
    run_meta["model_price_fill_rate"] = round(fill_rate, 4)
    run_meta["n_rows_with_nan_quote"] = n_nan_quote
    run_meta["n_rows_filled_by_model_price"] = n_nan_quote_with_mp

    # ----------------------------------------------------------------
    # Run metadata + summary
    # ----------------------------------------------------------------
    run_meta["total_duration_sec"] = round(time.perf_counter() - run_start, 2)
    meta_path = REPORTS_DIR / "day_5_4a_run_meta.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)
    print(f"\n→ {meta_path}", flush=True)

    # Markdown summary
    summary_md = REPORTS_DIR / "day_5_4a_summary.md"
    with summary_md.open("w", encoding="utf-8") as f:
        f.write("# Day 5.4a — Surface Mark Validation Summary\n\n")
        f.write(f"**Run**: {args.start} → {args.end} ({n_days} days, {len(chain):,} rows)\n\n")
        f.write(f"**n_workers**: {args.n_workers}\n\n")
        f.write(f"**Total duration**: {run_meta['total_duration_sec']:.1f}s\n\n")
        f.write("## Pro 驗收矩陣 #1-4 (Day 5.4a 階段)\n\n")
        f.write("| # | Item | Value | Target | Pass |\n")
        f.write("|---|------|-------|--------|------|\n")
        f.write(
            f"| 1a | SVI share (groups dispatched to SVI / total) | "
            f"{svi_share:.1%} ({n_svi_groups}/{n_total_groups}) | >= 60% | "
            f"{'PASS' if svi_share >= 0.60 else 'FAIL Pattern 13 trigger'} |\n"
        )
        f.write(
            f"| 1b | SVI internal convergence (informational) | "
            f"{svi_internal_rate:.1%} ({n_svi_converged}/{n_svi_groups}) | (always 100% per dispatch) | - |\n"
        )
        f.write(
            f"| 3 | converged in-sample RMSE median | "
            f"{rmse_median:.4f} | < 0.02 | "
            f"{'✅' if pd.notna(rmse_median) and rmse_median < 0.02 else '❌'} |\n"
        )
        f.write(
            f"| - | model_price 填補率 (NaN bid/ask 行) | {fill_rate:.1%} | (informational) | - |\n"
        )
        f.write("\n## Phase durations (sec)\n\n")
        for k, v in run_meta["phase_durations_sec"].items():
            f.write(f"- `{k}`: {v}s\n")
        f.write("\n## model_type breakdown\n\n")
        f.write("```\n")
        f.write(model_type_stats.to_string(index=False) + "\n")
        f.write("```\n")
        f.write("\n## Day 5.4b 留:\n")
        f.write("- Pro 矩陣 #2 butterfly arb-free 通過率\n")
        f.write(
            "- Pro 矩陣 #4 temporal drift RMSE (R11.15 P4 fix: OOS 改名 — 非嚴格 OOS validation)\n"
        )
        f.write("- Pro 矩陣 #5 3 scenario cum_pnl Sharpe 對比 (strict / settle / surface)\n")
        f.write("- Pro 矩陣 #6 mark_audit fallback_rate 分布\n")
    print(f"→ {summary_md}", flush=True)

    print(f"\nDay 5.4a complete. Total: {run_meta['total_duration_sec']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
