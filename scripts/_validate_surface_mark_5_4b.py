"""Week 5 Day 5.4b — 3 scenario backtest 對比 + Pro 驗收矩陣 #2/#4/#5/#6.

D-soft pivot 完工驗證: 同 strategy / 同 chain / 同 risk_config 跑 3 個
mark_policy scenario, 對比 cum_pnl / Sharpe / max_DD / fallback_rate.

Pre-req:
  - Day 5.4a 已完工: surface_cache 242 days fits 持久化進
    data/taifex_cache/surface_fits/2024/ + 2025/
  - 1-year 真資料 cache 完整 (2024-04 → 2025-04)

Day 5.4b Pro 矩陣 #2/#4/#5/#6 (Day 5.4a 補完 #1/#3):
  #2: butterfly arb-free post-fit grid 通過率
      per converged SVI → 100-point grid k ∈ [-0.3, 0.3] → g(k) >= 0 比例
  #4: temporal drift RMSE (R11.15 P4 改名 from OOS — **非嚴格 OOS validation**)
      時間順序 80/20 split (前 80% IS / 後 20% late period); 嚴格 OOS
      (refit on train + validate on test quotes) 留 Week 6+ 真 backtest
  #5: cross-scenario Sharpe 差距 < 30% (B vs C)
      surface mark vs settle mark 的 Sharpe 差距太大 → mark 鏈未 calibrate
  #6: mark_audit fallback_rate 分布
      surface 應降 fallback rate vs settle (BSM-Merton 反算 model_price 應更接近真 mid)

紀錄產出:
  - reports/day_5_4b_scenarios.csv (4 scenario × {cum_pnl, sharpe, max_dd, win_rate, n_trades})
  - reports/day_5_4b_arb_free_grid.csv (per converged fit 的 grid 通過率)
  - reports/day_5_4b_temporal_drift_rmse.csv (per (date, expiry) early vs late RMSE; R11.15 P4 改名 from oos_rmse)
  - reports/day_5_4b_summary.md (Pro 矩陣 #2/#4/#5/#6 計算表)
  - reports/day_5_4b_run_meta.json

CLI:
    python scripts/_validate_surface_mark_5_4b.py
    python scripts/_validate_surface_mark_5_4b.py --start 2024-04-01 --end 2024-05-01  # smoke
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts._gated_strategy import GatedIronCondor  # noqa: E402
from src.backtest.engine import run_backtest  # noqa: E402
from src.common.types import RiskConfig  # noqa: E402
from src.data.cache import load_chain  # noqa: E402
from src.data.enrich import add_can_buy_can_sell, add_model_price, enrich_pipeline  # noqa: E402
from src.options.surface_cache import load_surface_records  # noqa: E402
from src.options.vol_surface import butterfly_arb_indicator  # noqa: E402

CACHE_DIR = _REPO_ROOT / "data" / "taifex_cache"
TAIEX_CSV = _REPO_ROOT / "data" / "taiex_daily.csv"
REPORTS_DIR = _REPO_ROOT / "reports"


def _section(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)


def _load_taiex_spot(start: str, end: str) -> pd.Series:
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values), name="spot")


def _run_one_scenario(
    label: str,
    enriched: pd.DataFrame,
    mark_policy: str,
    initial_capital: float,
    risk_config: RiskConfig,
    start_date: str,
    end_date: str,
) -> dict:
    """Run a single backtest scenario; capture exception if strict_mid raises.

    R11.15 P3 fix (Codex): 改用 GatedIronCondor (含 R10.10 3ii can_buy/can_sell
    side-specific execution gate) — 普通 IronCondor 不啟用 gate, 對真資料 IC
    1-year 1 trade 是否是 gate 過嚴 silent skip 無法回答.
    """
    print(f"\n  -> Scenario {label}: mark_policy={mark_policy!r}", flush=True)
    t0 = time.perf_counter()
    ic = GatedIronCondor(
        short_delta=0.16,
        wing_delta=0.08,
        target_dte=30,
        exit_dte=14,
        profit_target_pct=0.50,
        risk_config=risk_config,
    )
    try:
        result = run_backtest(
            ic,
            enriched,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            mark_policy=mark_policy,
        )
        elapsed = time.perf_counter() - t0
        audit = result["mark_audit"]
        cum_pnl = float(result["daily_pnl"].sum()) if not result["daily_pnl"].empty else 0.0
        n_trades = int(len(result["trades"]))
        # Aggregate fallback stats
        n_legs_total = int(audit["n_legs_marked"].sum()) if not audit.empty else 0
        n_settle = int(audit["n_fallback_settle"].sum()) if not audit.empty else 0
        n_surface = int(audit["n_fallback_surface"].sum()) if not audit.empty else 0
        avg_fb_rate = float(audit["fallback_rate"].mean()) if not audit.empty else 0.0
        return {
            "scenario": label,
            "mark_policy": mark_policy,
            "status": "completed",
            "duration_sec": round(elapsed, 1),
            "n_trades": n_trades,
            "cum_pnl_twd": round(cum_pnl, 2),
            "sharpe": round(float(result["metrics"].get("sharpe", float("nan"))), 4),
            "max_drawdown_twd": round(
                float(result["metrics"].get("max_drawdown", float("nan"))), 2
            ),
            "win_rate": round(float(result["metrics"].get("win_rate", float("nan"))), 4),
            "final_cash": round(float(result["final_cash"]), 2),
            "final_unrealised": round(float(result["final_unrealised"]), 2),
            "n_legs_marked_total": n_legs_total,
            "n_fallback_settle_total": n_settle,
            "n_fallback_surface_total": n_surface,
            "avg_fallback_rate": round(avg_fb_rate, 4),
            "error": None,
        }
    except (ValueError, RuntimeError) as e:
        elapsed = time.perf_counter() - t0
        print(f"    raised {type(e).__name__}: {str(e)[:120]}", flush=True)
        return {
            "scenario": label,
            "mark_policy": mark_policy,
            "status": "raised",
            "duration_sec": round(elapsed, 1),
            "n_trades": 0,
            "cum_pnl_twd": float("nan"),
            "sharpe": float("nan"),
            "max_drawdown_twd": float("nan"),
            "win_rate": float("nan"),
            "final_cash": float("nan"),
            "final_unrealised": float("nan"),
            "n_legs_marked_total": 0,
            "n_fallback_settle_total": 0,
            "n_fallback_surface_total": 0,
            "avg_fallback_rate": float("nan"),
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def _arb_free_grid_stats(surface_records: list, k_grid: np.ndarray | None = None) -> pd.DataFrame:
    """Pro 矩陣 #2: per converged SVI fit → grid arb-free 通過率.

    grid k ∈ [-0.3, 0.3] 100 點; counter g(k) >= 0 比例.
    """
    if k_grid is None:
        k_grid = np.linspace(-0.3, 0.3, 100)
    rows: list[dict] = []
    for r in surface_records:
        if r.model_type != "svi" or not r.converged:
            continue
        try:
            g = butterfly_arb_indicator(
                k_grid,
                a=r.params["a"],
                b=r.params["b"],
                rho=r.params["rho"],
                m=r.params["m"],
                sigma=r.params["sigma"],
            )
            n_pass = int((g >= 0).sum())
            n_total = int(len(g))
        except (ValueError, KeyError):
            n_pass, n_total = 0, int(len(k_grid))
        rows.append(
            {
                "date": r.date,
                "expiry": r.expiry,
                "n_grid": n_total,
                "n_pass": n_pass,
                "pass_rate": n_pass / n_total if n_total > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _temporal_drift_rmse_stats(enriched: pd.DataFrame, surface_records: list) -> pd.DataFrame:
    """Pro 矩陣 #4 — R11.15 P4 fix (Codex): **改名 OOS → temporal drift**.

    這 *不是* 嚴格 OOS validation. 嚴格 OOS = 訓練期 fit surface, 拿測試期
    quotes 重新算 pricing error. 本函式只做時間順序 80/20 切, 比較前後段
    in_sample_rmse 中位數 — 這只是 *temporal drift check* (fit 品質隨時間
    是否退化), 不能宣稱嚴格 OOS.

    嚴格 OOS validation 留 Week 6+ 真 backtest 階段 (refit cost 高需 separate
    pipeline).
    """
    df = pd.DataFrame(
        [
            {"date": r.date, "expiry": r.expiry, "rmse": r.in_sample_rmse}
            for r in surface_records
            if r.converged and r.in_sample_rmse is not None and pd.notna(r.in_sample_rmse)
        ]
    )
    if df.empty:
        return df
    df["date_ts"] = pd.to_datetime(df["date"])
    sorted_dates = sorted(df["date_ts"].unique())
    if len(sorted_dates) < 5:
        return df
    split_idx = int(len(sorted_dates) * 0.80)
    is_dates = set(sorted_dates[:split_idx])
    oos_dates = set(sorted_dates[split_idx:])
    df["split"] = df["date_ts"].apply(
        lambda d: "IS" if d in is_dates else ("OOS" if d in oos_dates else "skip")
    )
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Day 5.4b — 3 scenario backtest + Pro 矩陣")
    parser.add_argument("--start", default="2024-04-01")
    parser.add_argument("--end", default="2025-04-01")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_start = time.perf_counter()
    run_meta: dict = {
        "script": "_validate_surface_mark_5_4b.py",
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": args.initial_capital,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "phase_durations_sec": {},
    }

    # ----------------------------------------------------------------
    # Step 1: load chain + enrich + add_model_price (re-use Day 5.4a surface_cache)
    # ----------------------------------------------------------------
    _section(f"Step 1: load_chain + enrich + add_model_price {args.start} → {args.end}")
    t0 = time.perf_counter()
    chain = load_chain(str(CACHE_DIR), args.start, args.end, layer="strategy_view")
    spot_series = _load_taiex_spot(args.start, args.end)
    enriched, _q_pit_audit = enrich_pipeline(
        chain,
        spot_series,
        spot_missing_policy="forward_fill",
        on_iv_solver_fail="nan",
    )
    enriched = add_can_buy_can_sell(enriched)
    print(
        f"  enriched: {len(enriched):,} rows, IV fill rate {enriched['iv'].notna().mean():.1%}",
        flush=True,
    )

    # Re-use Day 5.4a surface_cache (don't refit)
    surface_records = load_surface_records(str(CACHE_DIR), args.start, args.end)
    print(f"  loaded {len(surface_records):,} surface fit records", flush=True)
    enriched = add_model_price(enriched, surface_records)
    mp_fill = (enriched["model_price"].notna()).mean()
    print(f"  model_price fill rate: {mp_fill:.1%}", flush=True)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step1_load_enrich_inject"] = round(t1 - t0, 2)

    run_meta["chain_n_rows"] = int(len(enriched))
    run_meta["chain_n_dates"] = int(enriched["date"].nunique())
    run_meta["surface_records_loaded"] = len(surface_records)
    run_meta["model_price_fill_rate"] = round(float(mp_fill), 4)

    # ----------------------------------------------------------------
    # Step 2: 4 scenario backtest (R11.15 P2/P3 fix: 加 D_forced_missing)
    # ----------------------------------------------------------------
    _section("Step 2: 4 scenario backtest (strict / settle / surface / surface_forced_missing)")
    risk_cfg = RiskConfig(
        max_loss_per_trade_twd=200_000.0,
        max_capital_at_risk_twd=400_000.0,
        max_concurrent_positions=3,
        stop_loss_multiple=2.0,
        portfolio_loss_cap_pct=0.05,
    )

    # R11.15 P2 fix + R11.16 P2 fix (Codex): 故意 mask 部分行 bid/ask 強制
    # surface fallback 觸發。
    # **R11.16 P2 修法**: 之前 mask 後**沒重算** `can_buy/can_sell` →
    # 27,130 stale flags 讓 strategy 誤判「該 leg 仍可成交」放行進 fill,
    # 結果在 execution._assert_executable raise — 導致 D scenario 是
    # *stale-flag bug* 不是 *清乾淨的 execution gate 攔截*.
    # 修法: mask 後 drop can_buy/can_sell 重新跑 add_can_buy_can_sell 對齊.
    enriched_forced = enriched.copy()
    rng = np.random.default_rng(seed=42)
    mask = rng.random(len(enriched_forced)) < 0.30
    has_quote = enriched_forced["bid"].notna() & enriched_forced["ask"].notna()
    mask = mask & has_quote
    enriched_forced.loc[mask, "bid"] = float("nan")
    enriched_forced.loc[mask, "ask"] = float("nan")
    # R11.16 P2 fix: drop stale can_buy/can_sell 並重算對齊 mask 後 quote
    enriched_forced = enriched_forced.drop(columns=["can_buy", "can_sell"])
    enriched_forced = add_can_buy_can_sell(enriched_forced)
    n_force_masked = int(mask.sum())
    print(
        f"  forced_missing scenario: masked {n_force_masked:,} rows bid/ask "
        f"({n_force_masked / len(enriched_forced):.1%}); "
        f"can_buy/can_sell 重算對齊 (R11.16 P2 fix)",
        flush=True,
    )
    run_meta["force_masked_rows"] = n_force_masked

    scenarios = []
    for label, mark_policy, chain_for_scenario in [
        ("A_strict_mid", "strict_mid", enriched),
        ("B_settle_fallback", "mid_with_settle_fallback", enriched),
        ("C_surface_fallback", "mid_with_surface_fallback", enriched),
        ("D_surface_forced_missing", "mid_with_surface_fallback", enriched_forced),
    ]:
        scenarios.append(
            _run_one_scenario(
                label,
                chain_for_scenario,
                mark_policy,
                args.initial_capital,
                risk_cfg,
                args.start,
                args.end,
            )
        )
    scenarios_df = pd.DataFrame(scenarios)
    scen_csv = REPORTS_DIR / "day_5_4b_scenarios.csv"
    scenarios_df.to_csv(scen_csv, index=False)
    print(f"\n  -> {scen_csv}", flush=True)
    print(scenarios_df.to_string(index=False), flush=True)
    run_meta["scenarios"] = scenarios

    # Pro 矩陣 #5: cross-scenario Sharpe 差距 (B vs C)
    b_row = next((s for s in scenarios if s["scenario"] == "B_settle_fallback"), None)
    c_row = next((s for s in scenarios if s["scenario"] == "C_surface_fallback"), None)
    if b_row and c_row and b_row["status"] == "completed" and c_row["status"] == "completed":
        b_sharpe = b_row["sharpe"]
        c_sharpe = c_row["sharpe"]
        if b_sharpe and not pd.isna(b_sharpe) and abs(b_sharpe) > 1e-9:
            sharpe_diff_pct = abs(c_sharpe - b_sharpe) / abs(b_sharpe)
        else:
            sharpe_diff_pct = float("nan")
        run_meta["pro_matrix_5_sharpe_diff_pct"] = (
            round(sharpe_diff_pct, 4) if pd.notna(sharpe_diff_pct) else None
        )
        run_meta["pro_matrix_5_target"] = 0.30
        run_meta["pro_matrix_5_pass"] = bool(pd.notna(sharpe_diff_pct) and sharpe_diff_pct < 0.30)

    # ----------------------------------------------------------------
    # Step 3: Pro 矩陣 #2 butterfly arb-free post-fit grid
    # ----------------------------------------------------------------
    _section("Step 3: Pro 矩陣 #2 butterfly arb-free grid (per converged SVI)")
    t0 = time.perf_counter()
    arb_df = _arb_free_grid_stats(surface_records)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step3_arb_grid"] = round(t1 - t0, 2)
    if not arb_df.empty:
        median_pass_rate = float(arb_df["pass_rate"].median())
        full_pass_rate = float((arb_df["pass_rate"] >= 0.99).mean())
        print(
            f"  arb-free grid: {len(arb_df)} SVI fits, median pass rate {median_pass_rate:.2%}, "
            f">=99% pass: {full_pass_rate:.1%} of fits",
            flush=True,
        )
        run_meta["pro_matrix_2_median_grid_pass_rate"] = round(median_pass_rate, 4)
        run_meta["pro_matrix_2_target"] = 0.95
        run_meta["pro_matrix_2_pass"] = bool(median_pass_rate >= 0.95)
        arb_csv = REPORTS_DIR / "day_5_4b_arb_free_grid.csv"
        arb_df.to_csv(arb_csv, index=False)
        print(f"-> {arb_csv}", flush=True)
    else:
        print("  no SVI fits to grid-check", flush=True)
        run_meta["pro_matrix_2_pass"] = False

    # ----------------------------------------------------------------
    # Step 4: Pro 矩陣 #4 temporal drift RMSE (R11.15 P4 fix: 改名 from OOS)
    # ----------------------------------------------------------------
    _section("Step 4: Pro 矩陣 #4 temporal drift RMSE (時間順序 80/20; not strict OOS)")
    drift_df = _temporal_drift_rmse_stats(enriched, surface_records)
    if not drift_df.empty:
        is_rmse = drift_df.loc[drift_df["split"] == "IS", "rmse"]
        late_rmse = drift_df.loc[drift_df["split"] == "OOS", "rmse"]  # late period (not true OOS)
        is_median = float(is_rmse.median()) if not is_rmse.empty else float("nan")
        late_median = float(late_rmse.median()) if not late_rmse.empty else float("nan")
        ratio = late_median / is_median if pd.notna(is_median) and is_median > 0 else float("nan")
        print(f"  early-80% IS RMSE median: {is_median:.4f}", flush=True)
        print(f"  late-20% RMSE median: {late_median:.4f}", flush=True)
        print(
            f"  late/early ratio: {ratio:.2f} (target < 1.5; temporal drift not strict OOS)",
            flush=True,
        )
        run_meta["pro_matrix_4_temporal_drift_early_rmse_median"] = (
            round(is_median, 6) if pd.notna(is_median) else None
        )
        run_meta["pro_matrix_4_temporal_drift_late_rmse_median"] = (
            round(late_median, 6) if pd.notna(late_median) else None
        )
        run_meta["pro_matrix_4_temporal_drift_ratio"] = round(ratio, 4) if pd.notna(ratio) else None
        run_meta["pro_matrix_4_target"] = 1.5
        run_meta["pro_matrix_4_pass"] = bool(pd.notna(ratio) and ratio < 1.5)
        run_meta["pro_matrix_4_NOTE"] = (
            "Temporal drift only — not strict OOS. Strict OOS (refit on train, "
            "validate on test quotes) deferred to Week 6+ real backtest."
        )
        drift_csv = REPORTS_DIR / "day_5_4b_temporal_drift_rmse.csv"
        drift_df.to_csv(drift_csv, index=False)
        print(f"-> {drift_csv}", flush=True)
    else:
        run_meta["pro_matrix_4_pass"] = False

    # ----------------------------------------------------------------
    # Pro 矩陣 #6: mark_audit fallback_rate 分布
    # ----------------------------------------------------------------
    if c_row and c_row["status"] == "completed":
        run_meta["pro_matrix_6_avg_surface_fallback_rate"] = c_row["avg_fallback_rate"]
        run_meta["pro_matrix_6_n_legs_via_surface"] = c_row["n_fallback_surface_total"]
    if b_row and b_row["status"] == "completed":
        run_meta["pro_matrix_6_avg_settle_fallback_rate"] = b_row["avg_fallback_rate"]
        run_meta["pro_matrix_6_n_legs_via_settle"] = b_row["n_fallback_settle_total"]

    # ----------------------------------------------------------------
    # Run metadata + summary
    # ----------------------------------------------------------------
    run_meta["total_duration_sec"] = round(time.perf_counter() - run_start, 2)
    meta_path = REPORTS_DIR / "day_5_4b_run_meta.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)
    print(f"\n→ {meta_path}", flush=True)

    # Markdown summary
    summary_md = REPORTS_DIR / "day_5_4b_summary.md"
    with summary_md.open("w", encoding="utf-8") as f:
        f.write("# Day 5.4b — 3 Scenario Backtest + Pro 驗收矩陣 #2/#4/#5/#6\n\n")
        f.write(f"**Range**: {args.start} → {args.end}\n\n")
        f.write(f"**Total duration**: {run_meta['total_duration_sec']:.1f}s\n\n")
        f.write("## 3 Scenario Backtest 對比\n\n")
        f.write("```\n")
        f.write(scenarios_df.to_string(index=False) + "\n")
        f.write("```\n")
        f.write("\n## Pro 驗收矩陣 #2/#4/#5/#6\n\n")
        f.write("| # | Item | Value | Target | Pass |\n")
        f.write("|---|------|-------|--------|------|\n")
        f.write(
            f"| 2 | butterfly arb-free grid median pass rate | "
            f"{run_meta.get('pro_matrix_2_median_grid_pass_rate', 'N/A')} | >= 0.95 | "
            f"{'✅' if run_meta.get('pro_matrix_2_pass') else '❌'} |\n"
        )
        f.write(
            f"| 4 | temporal drift ratio (late/early; **not strict OOS**) | "
            f"{run_meta.get('pro_matrix_4_temporal_drift_ratio', 'N/A')} | < 1.5 | "
            f"{'✅' if run_meta.get('pro_matrix_4_pass') else '❌'} |\n"
        )
        f.write(
            f"| 5 | Sharpe diff (B vs C) | "
            f"{run_meta.get('pro_matrix_5_sharpe_diff_pct', 'N/A')} | < 0.30 | "
            f"{'✅' if run_meta.get('pro_matrix_5_pass') else '❌'} |\n"
        )
        f.write(
            f"| 6 | surface fallback n_legs | "
            f"{run_meta.get('pro_matrix_6_n_legs_via_surface', 'N/A')} | (informational) | - |\n"
        )
        f.write("\n## Phase durations\n\n")
        for k, v in run_meta["phase_durations_sec"].items():
            f.write(f"- `{k}`: {v}s\n")
    print(f"-> {summary_md}", flush=True)

    print(f"\nDay 5.4b complete. Total: {run_meta['total_duration_sec']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
