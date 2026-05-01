"""Week 6 Day 6.5 — 5yr × 6 scenario walk-forward backtest 主 script.

User R12 拍板 + R12.0/R12.1 Codex audit 5 件 critical P 修法後，本 script
跑 6 scenario × walk-forward (~16 fold each, 5yr disjoint quarterly OOS):

| # | strategy           | regime gate           |
|---|--------------------|-----------------------|
| 1 | GatedIronCondor    | vanilla               |
| 2 | GatedIronCondor    | IV percentile         |
| 3 | GatedIronCondor    | HMM 2-state           |
| 4 | GatedVerticalStrategy | vanilla            |
| 5 | GatedVerticalStrategy | IV percentile      |
| 6 | GatedVerticalStrategy | HMM 2-state        |

Pro 統計 per scenario (R12.0 P2 sign-flip 紀律, P3 disjoint OOS):
  - bootstrap_ci(95%) on aggregated OOS daily_pnl
  - permutation_test (sign-flip; caveat per Codex R12.1: H0 假設 IC 不對稱
    可能違反 — p_value 只能輔助, 不能當唯一 alpha 顯著性結論)
  - deflated_sharpe(n_trials=6) — 6 scenario ablation 修正
  - calmar_ratio

Outputs (reports/week6_5yr_*) — CSV row 自帶 metadata schema (每 row 含
strategy/regime_gate/backtest_scope_yr/fold_index 等 → pandas slice 不靠
檔名):

  reports/week6_5yr_run_meta.json       # run config + durations
  reports/week6_5yr_folds.csv           # per-fold metrics × 6 scenario
  reports/week6_5yr_scenarios.csv       # 6 scenario aggregate metrics
  reports/week6_5yr_daily_pnl.csv       # long-format daily PnL (pivot-ready)
  reports/week6_5yr_rejected_reasons.csv # GatedIC/Vertical reject 統計
  reports/week6_5yr_ablation_matrix.csv  # regime gate ablation 對比
  reports/week6_5yr_monitor_metrics.json # 6 mark_audit metrics × 6 scenario
  reports/week6_5yr_summary.md           # Pro 報告 narrative
  reports/week6_5yr_console.log          # full stdout

Pre-req: surface_fits cache 必須 covers 5yr 範圍 (2021-04→2026-04). 若缺
caller 先跑 `_validate_surface_mark_5_4a.py --start ... --end ... --save-cache`
分批補齊 (見 Plan §Day 6.5 Step 1).

CLI:
  python scripts/_validate_week6_5yr.py
  python scripts/_validate_week6_5yr.py --start 2021-04-01 --end 2026-04-28
  python scripts/_validate_week6_5yr.py --smoke   # 1-month sub-set for debug
"""

from __future__ import annotations

# R12.5/R12.6/R12.7/R12.8 P fix (Codex audit): cp950/non-UTF8 Windows env protection.
#
# Lineage:
#   R12.5 P1: io.TextIOWrapper for main process stdout/stderr (NOT enough — child crash)
#   R12.6 P1: os.environ.setdefault PYTHONIOENCODING/PYTHONUTF8 (NOT enough — setdefault preserves user cp950)
#   R12.7 P1: force overwrite os.environ[...] = 'utf-8' (NOT enough —
#             PYTHONUTF8=1 must be set BEFORE interpreter starts to enable
#             PEP 540 UTF-8 mode; mid-run set is no-op for sys.flags.utf8_mode
#             AND locale.getpreferredencoding stays cp950 → subprocess
#             _readerthread still decodes child stdout as cp950 → crash)
#   R12.8 P1 (本輪): re-exec self with `-X utf8` flag at earliest possible
#             entry — this restarts interpreter with UTF-8 mode enabled, which
#             changes locale.getpreferredencoding to utf-8 → subprocess Popen's
#             reader thread correctly decodes child UTF-8 output. Codex R12.7
#             反證 showed locale stays cp950 even with PYTHONUTF8 set mid-run;
#             re-exec is the only Python-level fix.
import os  # noqa: E402  - need before re-exec block
import sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402  - for re-exec gate only


def _should_reexec_for_utf8() -> bool:
    """R12.8/R12.9 P1 gate: only re-exec when run AS SCRIPT (not when imported).

    Conditions:
      - PEP 540 UTF-8 mode is OFF (sys.flags.utf8_mode == 0)
      - Not already re-execed (env sentinel absent)
      - sys.argv[0] resolves to this very file (i.e. CLI invocation, not import)
    """
    if sys.flags.utf8_mode != 0:
        return False
    if os.environ.get("_R12_8_UTF8_REEXEC"):
        return False
    if not sys.argv or not sys.argv[0]:
        return False
    try:
        invoked = _Path(sys.argv[0]).resolve()
        this_file = _Path(__file__).resolve()
    except (OSError, ValueError):
        return False
    return invoked == this_file


if _should_reexec_for_utf8():
    # R12.9 P1 fix (Codex audit): use subprocess.run + sys.exit(returncode)
    # instead of os.execv. On Windows, os.execv is spawn-and-detach: parent
    # exits with code 0 immediately, child's exit code is LOST. Codex R12.8
    # 反證: 任何 ValueError / argparse error 都被吞掉, $LASTEXITCODE=0 → CI
    # 誤判 success.
    #
    # subprocess.run blocks parent until child exits, then we propagate exit
    # code via sys.exit. PEP 540 UTF-8 mode is enabled in child via -X utf8
    # (R12.7/R12.8 lesson: must be set at interpreter startup, not mid-run).
    import subprocess  # noqa: E402  - only needed for re-exec path

    child_env = os.environ.copy()
    child_env["_R12_8_UTF8_REEXEC"] = "1"  # sentinel prevents infinite re-exec
    child_env["PYTHONIOENCODING"] = "utf-8"  # force overwrite (R12.7)
    child_env["PYTHONUTF8"] = "1"  # PEP 540 (defense-in-depth alongside -X utf8)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", *sys.argv],
        env=child_env,
        check=False,
    )
    sys.exit(proc.returncode)

import argparse  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from io import StringIO  # noqa: E402
from pathlib import Path  # noqa: E402

# Defense-in-depth: even after re-exec with -X utf8, wrap main stdout/stderr
# explicitly so user's terminal encoding doesn't fight Python's UTF-8 mode.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# Bootstrap repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts._gated_strategy import GatedIronCondor  # noqa: E402
from scripts._gated_vertical import GatedVerticalStrategy  # noqa: E402
from src.backtest.execution import RetailCostModel, WorstSideFillModel  # noqa: E402
from src.backtest.monitor import (  # noqa: E402
    summarise_mark_audit,
    summarise_scenario_pnl_divergence,
)
from src.backtest.stats import (  # noqa: E402
    bootstrap_ci,
    calmar_ratio,
    deflated_sharpe,
    permutation_test,
)
from src.backtest.walk_forward import (  # noqa: E402
    AggregatedResult,
    WalkForwardConfig,
    walk_forward_backtest,
)
from src.data.cache import load_chain  # noqa: E402
from src.data.enrich import add_model_price, enrich_pipeline  # noqa: E402
from src.options.regime_gate import HMMRegimeGate, IVPercentileGate  # noqa: E402
from src.options.surface_cache import load_surface_records  # noqa: E402
from src.strategies.regime_wrapped import RegimeWrappedStrategy  # noqa: E402

CACHE_DIR = _REPO_ROOT / "data" / "taifex_cache"
TAIEX_CSV = _REPO_ROOT / "data" / "taiex_daily.csv"
REPORTS_DIR = _REPO_ROOT / "reports"

# Default 5yr range per User R12 拍板
DEFAULT_START = "2021-04-01"
DEFAULT_END = "2026-04-28"

# Scenarios: (id, strategy_name, regime_gate_name)
SCENARIOS: list[tuple[str, str, str]] = [
    ("IC_vanilla", "IronCondor", "vanilla"),
    ("IC_IV_percentile", "IronCondor", "IV_percentile"),
    ("IC_HMM", "IronCondor", "HMM"),
    ("Vertical_vanilla", "Vertical", "vanilla"),
    ("Vertical_IV_percentile", "Vertical", "IV_percentile"),
    ("Vertical_HMM", "Vertical", "HMM"),
]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_taiex_spot(start: str, end: str) -> pd.Series:
    """Load TAIEX close as Series indexed by date."""
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values), name="spot")


# R12.2 P1 fix (Codex audit): regime gate lookback prerequisite buffer.
# IV percentile gate: 30 (vol_lookback) + 252 (percentile_lookback) = 282 days
# HMM gate: 504 days
# Plus 30-day safety margin → load REGIME_HISTORY_BUFFER_DAYS days BEFORE
# backtest start so that even fold 0 test_start has enough returns_history
# for HMM gate (largest lookback). Empirically: 504 + ~30 buffer = 534 ~ 1 year.
REGIME_HISTORY_BUFFER_DAYS = 900  # 504 trading days ~= 730 calendar; +170 safety


def _load_taiex_returns_with_buffer(
    start: str, end: str, buffer_days: int = REGIME_HISTORY_BUFFER_DAYS
) -> pd.Series:
    """Load TAIEX log returns covering [start - buffer, end].

    R12.2 P1 fix: regime gate (HMM lookback=504) needs returns BEFORE backtest
    start. Original `_load_taiex_spot(start, end)` only loaded the window →
    fold 0 test_start had ~250 returns history (< 282 IV / < 504 HMM
    requirement) → IV/HMM gates silent fail-closed for entire 5yr backtest.

    Returns: pd.Series of log returns indexed by date, range
    [start - buffer, end]. Caller can index slice for backtest.
    """
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    buffered_start = pd.Timestamp(start) - pd.Timedelta(days=buffer_days)
    df = df[(df["date"] >= buffered_start) & (df["date"] <= pd.Timestamp(end))]
    if df.empty:
        raise ValueError(
            f"TAIEX history empty for [{buffered_start.date()}, {end}] — "
            f"check data/taiex_daily.csv coverage"
        )
    spot = pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values))
    log_ret = np.log(spot / spot.shift(1))
    returns = pd.Series(log_ret).dropna()
    # Hard gate: assert sufficient pre-start history for largest gate lookback
    pre_start_returns = returns[returns.index < pd.Timestamp(start)]
    if len(pre_start_returns) < 504:
        raise ValueError(
            f"TAIEX pre-{start} history only {len(pre_start_returns)} returns; "
            f"need >= 504 for HMM lookback. Extend buffer_days or TAIEX CSV."
        )
    return returns


def _taiex_log_returns(spot_series: pd.Series) -> pd.Series:
    """Convert TAIEX spot close to log returns (PIT: r_t = log(S_t / S_{t-1}))."""
    log_ret = np.log(spot_series / spot_series.shift(1))
    return pd.Series(log_ret).dropna()


SURFACE_COVERAGE_PCT_MIN = 0.95  # R12.5 P fix: institutional minimum date coverage


def _validate_surface_coverage(
    enriched: pd.DataFrame,
    surface_records: list,
    args: argparse.Namespace,
) -> None:
    """R12.2 P2 + R12.3 + R12.4 + R12.5 P fix (Codex audit): institutional gate.

    mid_with_surface_fallback policy 3-layer fallback (R12.4):
      mid -> surface -> settle -> raise

    R12.5 升級 (Codex audit): two-tier hard gate
      Tier 1: surface date coverage_pct >= SURFACE_COVERAGE_PCT_MIN (0.95) —
              防止「surface gate 退化成 settle gate」silent (Codex R12.4 toy
              反證 50% coverage + settle 100% → silent PASS, surface fallback
              幾乎沒打到只剩 settle 在撐).
      Tier 2: truly_unmarkable == 0 (R12.4) — bid/ask + model_price + settle
              三層都 NaN → engine 真 raise.

    R12.3 1% threshold removed — hollow PASS (Codex toy 反證 individual rows raise).

    Real TAIFEX 5yr 實證: cache 100% coverage / 8073 danger_rows fall to settle /
    truly_unmarkable = 0.
    """
    if not surface_records:
        raise ValueError(
            "Surface cache empty (0 records). Run scripts/_validate_surface_mark_5_4a.py "
            f"--start {args.start} --end {args.end} --save-cache before this script."
        )
    chain_dates = pd.DatetimeIndex(pd.to_datetime(enriched["date"]).unique()).sort_values()
    surface_dates = pd.DatetimeIndex(sorted({pd.Timestamp(r.date) for r in surface_records}))
    coverage_pct = surface_dates.intersection(chain_dates).size / max(len(chain_dates), 1)

    missing_bidask = enriched["bid"].isna() | enriched["ask"].isna()
    missing_mp = enriched["model_price"].isna()
    missing_settle = enriched["settle"].isna()
    danger_rows = int((missing_bidask & missing_mp).sum())
    truly_unmarkable = int((missing_bidask & missing_mp & missing_settle).sum())
    settle_3rd_fallback_rows = danger_rows - truly_unmarkable

    print(
        f"  surface coverage: {coverage_pct:.1%} ({surface_dates.size}/{chain_dates.size} dates)",
        flush=True,
    )
    print(
        f"  danger_rows (NaN bid/ask AND NaN model_price): {danger_rows:,} / {len(enriched):,}",
        flush=True,
    )
    print(
        f"  truly_unmarkable (also NaN settle, R12.4 Tier 2 gate): {truly_unmarkable:,}",
        flush=True,
    )
    print(
        f"  settle_3rd_fallback rows (R12.5 separate metric): {settle_3rd_fallback_rows:,} "
        f"= {settle_3rd_fallback_rows / max(len(enriched), 1):.2%}",
        flush=True,
    )

    # R12.5 Tier 1: surface coverage hard gate
    if coverage_pct < SURFACE_COVERAGE_PCT_MIN:
        raise ValueError(
            f"R12.5 Tier 1 gate FAIL: surface date coverage {coverage_pct:.1%} < "
            f"{SURFACE_COVERAGE_PCT_MIN:.0%} minimum. Surface fallback would degrade "
            f"to settle fallback (Codex R12.4 P2). Run scripts/_validate_surface_mark_5_4a.py "
            f"to extend cache, or pass --skip-surface-coverage-gate to bypass."
        )
    # R12.4 Tier 2: truly_unmarkable hard gate
    if truly_unmarkable > 0:
        raise ValueError(
            f"R12.4 Tier 2 gate FAIL: {truly_unmarkable:,} chain rows have NaN "
            f"bid/ask AND NaN model_price AND NaN settle. Engine will truly raise "
            f"mid-run. Investigate raw cache integrity, or pass "
            f"--skip-surface-coverage-gate to bypass."
        )
    if settle_3rd_fallback_rows > 0:
        print(
            f"  [INFO] {settle_3rd_fallback_rows:,} 2-layer-miss rows (mid+surface NaN) "
            f"will fall back to settle (3rd layer; R12.4). Settle ~= 0 for far-OTM "
            f"worthless strikes is institutionally correct mark.",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Strategy factories
# ---------------------------------------------------------------------------


def _make_strategy_factory(
    strategy_name: str,
    regime_gate_name: str,
    underlying_returns: pd.Series,
):
    """Build callable(train_returns) -> Strategy for walk_forward_backtest.

    R10.5 PIT: walk_forward 切 train_returns 後傳給 factory; factory 內用此 train
    slice fit IV percentile 或 HMM gate; gate 不可看 test 期資料.
    """

    def factory(train_returns: pd.Series) -> RegimeWrappedStrategy:
        _ = train_returns  # PIT slice already done by walk_forward; passed for compat
        # 1. Build base strategy (Gated wrapper around IC / Vertical)
        base: GatedIronCondor | GatedVerticalStrategy
        if strategy_name == "IronCondor":
            base = GatedIronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
        elif strategy_name == "Vertical":
            base = GatedVerticalStrategy(
                short_delta=0.25, wing_delta=0.10, target_dte=45, exit_dte=21
            )
        else:
            raise ValueError(f"unknown strategy_name: {strategy_name}")

        # 2. Build regime gate (None=vanilla, IV pct, or HMM)
        gate: IVPercentileGate | HMMRegimeGate | None
        if regime_gate_name == "vanilla":
            gate = None
        elif regime_gate_name == "IV_percentile":
            gate = IVPercentileGate(
                vol_lookback_days=30, percentile_lookback_days=252, threshold_pct=0.30
            )
        elif regime_gate_name == "HMM":
            gate = HMMRegimeGate(lookback_days=504, n_iter=500, active_state="high_vol")
        else:
            raise ValueError(f"unknown regime_gate_name: {regime_gate_name}")

        # 3. Wrap with RegimeWrappedStrategy. Vanilla pass-through if gate=None.
        # train_returns 已 PIT slice 完，但 RegimeWrappedStrategy 在 should_open
        # 時用 self.returns_history; 為了 test 期內 PIT, 傳整個 underlying_returns
        # (gate 內 is_active 自會 filter `<= today`).
        return RegimeWrappedStrategy(
            base=base,
            regime_gate=gate,
            returns_history=underlying_returns if gate is not None else None,
        )

    return factory


# ---------------------------------------------------------------------------
# Per-scenario row builders (CSV with metadata)
# ---------------------------------------------------------------------------


def _scenario_metadata(
    scenario_id: str,
    strategy_name: str,
    regime_gate_name: str,
    scope_yr: str,
    backtest_start: str,
    backtest_end: str,
) -> dict:
    return {
        "scenario": scenario_id,
        "strategy": strategy_name,
        "regime_gate": regime_gate_name,
        "backtest_scope_yr": scope_yr,
        "backtest_start": backtest_start,
        "backtest_end": backtest_end,
    }


def _folds_to_rows(
    result: AggregatedResult,
    metadata: dict,
    cfg: WalkForwardConfig,
) -> list[dict]:
    """Per-fold rows with metadata + train/test windows + metrics."""
    rows: list[dict] = []
    for fold in result.folds:
        row = {
            **metadata,
            "train_window_days": cfg.train_window_days,
            "test_window_days": cfg.test_window_days,
            "step_days": cfg.step_days,
            "fold_index": fold.fold_index,
            "train_start": fold.train_start.strftime("%Y-%m-%d"),
            "train_end": fold.train_end.strftime("%Y-%m-%d"),
            "test_start": fold.test_start.strftime("%Y-%m-%d"),
            "test_end": fold.test_end.strftime("%Y-%m-%d"),
            "n_trades": int(len(fold.trades)),
            "metric_sharpe": fold.metrics.get("sharpe", float("nan")),
            "metric_max_drawdown": fold.metrics.get("max_drawdown", float("nan")),
            "metric_win_rate": fold.metrics.get("win_rate", float("nan")),
            "final_cash": fold.final_cash,
            "final_unrealised": fold.final_unrealised,
            "error": fold.error or "",
        }
        rows.append(row)
    return rows


def _daily_pnl_to_rows(
    result: AggregatedResult,
    metadata: dict,
) -> list[dict]:
    """Long-format daily PnL rows: one row per (fold, date)."""
    rows: list[dict] = []
    for fold in result.folds:
        if fold.daily_pnl.empty:
            continue
        for date, pnl in fold.daily_pnl.items():
            rows.append(
                {
                    **metadata,
                    "fold_index": fold.fold_index,
                    "date": pd.Timestamp(str(date)).strftime("%Y-%m-%d"),
                    "daily_pnl_twd": float(pnl),
                }
            )
    return rows


def _rejected_reasons_to_rows(
    result: AggregatedResult,
    metadata: dict,
) -> list[dict]:
    """R12.2 P4 fix (Codex audit): consume FoldResult.rejected_reasons accumulator.

    walk_forward_backtest now captures strategy.get_rejected_reasons() per fold
    via _extract_rejected_reasons before strategy goes out of scope. This
    function flattens those per-fold DataFrames into long-format rows with
    metadata.
    """
    rows: list[dict] = []
    for fold in result.folds:
        # Fold-level error (e.g. surface mark raise) — single row marker
        if fold.error:
            rows.append(
                {
                    **metadata,
                    "fold_index": fold.fold_index,
                    "date": fold.test_start.strftime("%Y-%m-%d"),
                    "path": "fold_error",
                    "reason": fold.error,
                    "leg": "",
                }
            )
            continue
        # Per-day reject reasons from strategy accumulator
        if fold.rejected_reasons is not None and not fold.rejected_reasons.empty:
            for _, r in fold.rejected_reasons.iterrows():
                date_val = r.get("date", pd.NaT)
                date_str = pd.Timestamp(date_val).strftime("%Y-%m-%d") if pd.notna(date_val) else ""
                rows.append(
                    {
                        **metadata,
                        "fold_index": fold.fold_index,
                        "date": date_str,
                        "path": str(r.get("path", "")),
                        "reason": str(r.get("reason", "")),
                        "leg": str(r.get("leg", "")),
                    }
                )
    return rows


def _scenario_aggregate_row_with_cost_flag(
    result: AggregatedResult,
    metadata: dict,
    cfg: WalkForwardConfig,
    cost_model_disabled: bool,
) -> dict:
    """R12.13 P caveat fix (Codex audit): add cost_model_disabled metadata col.

    Wraps _scenario_aggregate_row + injects cost_model_disabled boolean so
    scenarios.csv reader can detect cost-free baseline runs (Sharpe is upper
    bound, not realistic). Pre-R12.13: caller could only check JSON run_meta;
    CSV-only callers (notebooks / dashboards) couldn't distinguish.
    """
    row = _scenario_aggregate_row(result, metadata, cfg)
    row["cost_model_disabled"] = bool(cost_model_disabled)
    return row


def _scenario_aggregate_row(
    result: AggregatedResult,
    metadata: dict,
    cfg: WalkForwardConfig,
) -> dict:
    """One row per scenario: aggregate metrics + Pro stats + monitor."""
    initial_capital = cfg.initial_capital
    daily_pnl_arr = (
        np.asarray(result.daily_pnl.to_numpy(), dtype=np.float64)
        if not result.daily_pnl.empty
        else np.array([], dtype=np.float64)
    )

    # Pro stats (skip on too-few observations)
    if len(daily_pnl_arr) >= 2:
        ci_low, ci_high = bootstrap_ci(
            daily_pnl_arr,
            statistic="sharpe",
            n_iter=1000,
            ci=0.95,
            seed=42,
        )
        try:
            _obs_sharpe, _null, p_value = permutation_test(daily_pnl_arr, n_iter=1000, seed=42)
        except (ValueError, RuntimeError) as e:
            p_value = float("nan")
            print(f"  [warn] permutation_test fail for {metadata['scenario']}: {e}", flush=True)
        try:
            dsr = deflated_sharpe(
                observed_sharpe=result.metrics.get("sharpe", float("nan")),
                n_trials=6,  # 6 scenario ablation
                T=len(daily_pnl_arr),
            )
        except (ValueError, RuntimeError):
            dsr = float("nan")
        try:
            cal = calmar_ratio(daily_pnl_arr, initial_capital=initial_capital)
        except (ValueError, RuntimeError):
            cal = float("nan")
    else:
        ci_low = ci_high = float("nan")
        p_value = float("nan")
        dsr = float("nan")
        cal = float("nan")

    # Monitor: aggregate mark_audit across folds
    mark_audit_concat = (
        pd.concat([f.mark_audit for f in result.folds if not f.mark_audit.empty])
        if any(not f.mark_audit.empty for f in result.folds)
        else pd.DataFrame(
            columns=["fallback_rate", "n_legs_marked", "n_fallback_settle", "n_fallback_surface"]
        )
    )
    monitor = summarise_mark_audit(mark_audit_concat)

    total_trades = sum(int(len(f.trades)) for f in result.folds)
    return {
        **metadata,
        "train_window_days": cfg.train_window_days,
        "test_window_days": cfg.test_window_days,
        "step_days": cfg.step_days,
        "n_folds_total": result.n_folds,
        "n_folds_failed": result.n_failed_folds,
        "agg_sharpe": result.metrics.get("sharpe", float("nan")),
        "bootstrap_ci_low": ci_low,
        "bootstrap_ci_high": ci_high,
        "permutation_p_value": p_value,
        "deflated_sharpe": dsr,
        "agg_max_drawdown": result.metrics.get("max_drawdown", float("nan")),
        "calmar_ratio": cal,
        "total_trades": total_trades,
        "n_observations": int(result.metrics.get("n_observations", 0)),
        "n_fallback_surface_total": monitor["n_fallback_surface_total"],
        "n_fallback_settle_total": monitor["n_fallback_settle_total"],
        # R12.6 P2 fix (Codex audit): settle_3rd_fallback metric must surface
        # in primary CSV (was JSON-only in R12.5; user couldn't distinguish
        # surface-degraded-to-settle vs direct settle policy fallback).
        "n_fallback_settle_3rd_total": monitor["n_fallback_settle_3rd_total"],
        "settle_3rd_fallback_ratio": monitor["settle_3rd_fallback_ratio"],
        "fallback_legs_ratio": monitor["fallback_legs_ratio"],
        "avg_fallback_rate": monitor["avg_fallback_rate"],
    }


def _ablation_matrix_rows(scenarios_df: pd.DataFrame, scope_yr: str) -> list[dict]:
    """Per-strategy regime gate ablation: vanilla vs IV vs HMM Sharpe + CI."""
    rows: list[dict] = []
    for strategy_name in ("IronCondor", "Vertical"):
        sub = scenarios_df[scenarios_df["strategy"] == strategy_name]
        row: dict = {"strategy": strategy_name, "backtest_scope_yr": scope_yr}
        for gate in ("vanilla", "IV_percentile", "HMM"):
            sub_g = sub[sub["regime_gate"] == gate]
            if sub_g.empty:
                continue
            r = sub_g.iloc[0]
            row[f"{gate}_sharpe"] = r["agg_sharpe"]
            row[f"{gate}_ci_low"] = r["bootstrap_ci_low"]
            row[f"{gate}_ci_high"] = r["bootstrap_ci_high"]
        # Naive evidence note (exact decision per Pro thresholds in summary.md)
        row["gate_alpha_evidence"] = _ablation_evidence_note(row)
        rows.append(row)
    return rows


def _ablation_evidence_note(row: dict) -> str:
    """Quick eyeball: HMM > vanilla AND CI 不重疊 → alpha; else 'inconclusive'."""
    try:
        v_high = row.get("vanilla_ci_high", float("nan"))
        h_low = row.get("HMM_ci_low", float("nan"))
        if np.isfinite(v_high) and np.isfinite(h_low) and h_low > v_high:
            return "HMM > vanilla CI 不重疊 → 確認 alpha"
        return "inconclusive (CI 重疊或數據不足)"
    except (KeyError, TypeError):
        return "inconclusive"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _ensure_utf8_runtime_for_embedded_caller() -> None:
    """R12.13 P2 fix (Codex R12.12 反證): embedded library use-case UTF-8 guard.

    Script entry (line 91) re-execs with `-X utf8` if invoked as CLI script.
    But if caller does:
        import scripts._validate_week6_5yr as v
        v.main(["--smoke", ...])
    then re-exec gate skips (sys.argv[0] != __file__), and `sys.flags.utf8_mode`
    stays 0, `locale.getpreferredencoding` stays cp950 → subprocess
    `_readerthread` decodes child UTF-8 as cp950 → UnicodeDecodeError traceback.

    R12.13 fix: at main() entry, if utf8_mode=0 AND PowerShell-style cp950
    locale active, log a clear warning so embedded callers know the limitation.
    Cannot re-exec from within main() (caller's process state already stuck);
    user must invoke via CLI or set env BEFORE Python startup.
    """
    import locale as _locale

    if sys.flags.utf8_mode == 0:
        pref = _locale.getpreferredencoding(False).lower()
        if pref not in ("utf-8", "utf8"):
            _msg = (
                f"[R12.13 P2 WARNING] Embedded main() invocation under "
                f"locale={pref!r} (utf8_mode=0). subprocess _readerthread may "
                f"emit UnicodeDecodeError on child UTF-8 output. For clean "
                f"runs, invoke via CLI: `python -X utf8 scripts/_validate_week6_5yr.py ...` "
                f"or set PYTHONUTF8=1 BEFORE Python starts."
            )
            print(_msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_runtime_for_embedded_caller()
    parser = argparse.ArgumentParser(description="Week 6 Day 6.5 — 5yr × 6 scenario walk-forward")
    parser.add_argument("--start", default=DEFAULT_START, help="ISO start date")
    parser.add_argument("--end", default=DEFAULT_END, help="ISO end date")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Smoke run with reduced walk-forward window so logic actually "
            "exercises (R12.2 P3 fix: was 1-month / 0-fold hollow pass). "
            "Uses train=63 / test=21 / step=21 over 2024-04→2025-04 cache range."
        ),
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=1_000_000.0,
        help="per-fold reset capital (NTD)",
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default="all",
        help="comma-separated scenario ids or 'all' (default)",
    )
    parser.add_argument(
        "--skip-surface-coverage-gate",
        action="store_true",
        help=(
            "Bypass R12.2 P2 surface cache coverage hard gate. Diagnostic only — "
            "5yr real run MUST NOT skip; mid_with_surface_fallback will raise "
            "if model_price NaN coincides with NaN bid/ask."
        ),
    )
    parser.add_argument(
        "--no-cost-model",
        action="store_true",
        help=(
            "R12.12 P fix (Codex audit): disable RetailCostModel (commission / "
            "tax / slippage = 0). Diagnostic only — separates 'strategy has no "
            "alpha' from 'retail friction kills alpha'. NOT for paper trading "
            "decisions; cost-free Sharpe is upper bound, not realistic estimate."
        ),
    )
    args = parser.parse_args(argv)

    if args.smoke:
        # R12.2 P3 fix (Codex audit): smoke must exercise logic, not just plumbing.
        # Use cache range (2024-04→2025-04, 242 days) with reduced walk-forward
        # window so >=1 fold runs through engine + walk_forward + monitor + stats.
        # train=63 + test=21 + step=21 → ~7 folds in 242 days → smoke真驗 strategy
        # + regime gate + cost model + Pro stats end-to-end.
        args.start = "2024-04-01"
        args.end = "2025-04-01"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    scope_yr = "5yr" if not args.smoke else "smoke"

    # R12.6 P3 fix (Codex audit): --skip-surface-coverage-gate is diagnostic
    # only. Full 5yr launch (scope_yr='5yr') must reject the flag at parse-time
    # BEFORE 15-20 min enrich runs — otherwise user wastes compute then hits
    # a late raise. Smoke / diagnostic runs may bypass.
    if args.skip_surface_coverage_gate and scope_yr == "5yr":
        raise ValueError(
            "R12.6 P3 fix (Codex audit): --skip-surface-coverage-gate is for "
            "smoke / diagnostic only. Full 5yr launch must NOT skip the gate "
            "(institutional discipline; pre-flight prevents wasting 30+ min "
            "compute on mid-run raise). Drop --skip-surface-coverage-gate or "
            "use --smoke."
        )

    log_path = REPORTS_DIR / f"week6_{scope_yr}_console.log"
    log_buf = StringIO()

    # Mirror stdout to both terminal + buffer (then write to file at end)
    class _Tee:
        def __init__(self, *streams) -> None:
            self.streams = streams

        def write(self, data: str) -> None:
            for s in self.streams:
                s.write(data)

        def flush(self) -> None:
            for s in self.streams:
                s.flush()

    real_stdout = sys.stdout
    tee = _Tee(real_stdout, log_buf)
    sys.stdout = tee
    try:
        return _run(args, scope_yr)
    finally:
        sys.stdout = real_stdout
        log_path.write_text(log_buf.getvalue(), encoding="utf-8")


def _section(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)


def _run(args: argparse.Namespace, scope_yr: str) -> int:
    # R12.10/R12.11 P fix (Codex audit caveat): capture HMM "Model is not
    # converging" warnings count for run_meta + summary.md. Codex flagged
    # smoke 45+ HMM warnings → 5yr full run will have many more; user needs
    # visibility.
    #
    # R12.11 P1 fix (Codex R12.10 反證): R12.10 only added Handler without
    # ensuring `propagate=True`; if hmmlearn's logger has no other handler,
    # warning count works BUT stderr also stays clean (no original warning
    # text). Codex evidence chain ("stderr count == counter count") was
    # therefore broken — both were 0 in stderr. R12.11 fix:
    #   1. Force `propagate=True` on hmmlearn.base logger so warning still
    #      reaches root logger / stderr (default behavior)
    #   2. Ensure root logger has at least one handler (StreamHandler stderr)
    #      so the warning actually shows up
    # This way: both stderr (human-readable) AND counter (machine-parseable
    # for run_meta) capture the same event.
    import logging as _logging

    class _HMMWarningCounter(_logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def emit(self, record: _logging.LogRecord) -> None:
            if "Model is not converging" in record.getMessage():
                self.count += 1

    hmm_warning_counter = _HMMWarningCounter()
    _hmm_logger = _logging.getLogger("hmmlearn.base")
    _hmm_logger.addHandler(hmm_warning_counter)
    _hmm_logger.propagate = True  # R12.11: ensure root/stderr still receives

    # R12.12 P1 fix (Codex R12.11 反證): R12.11 用 `isinstance(h, StreamHandler)`
    # 判斷 root 是否已有 stderr handler. BUT: `FileHandler` IS a `StreamHandler`
    # subclass — caller 已 set file-only logging (e.g. logging.basicConfig(
    # filename=...)) 時, isinstance check 為 True, script 誤以為 stderr 已掛,
    # warning 仍走不到 stderr → silent regression for embedded callers.
    #
    # Fix: explicit check for handler whose `.stream` attribute IS sys.stderr
    # (or sys.stdout). This excludes FileHandler / NullHandler / custom handlers.
    _root = _logging.getLogger()
    _has_stderr_handler = any(
        isinstance(h, _logging.StreamHandler)
        and not isinstance(h, _logging.FileHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
        for h in _root.handlers
    )
    if not _has_stderr_handler:
        _root.addHandler(_logging.StreamHandler())  # default = stderr

    run_start = time.perf_counter()
    run_meta: dict = {
        "script": "_validate_week6_5yr.py",
        "scope": scope_yr,
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": args.initial_capital,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "phase_durations_sec": {},
        "scenarios_run": [],
        "codex_audits_applied": [
            "R12.0 P1-P4b",
            "R12.1 caveats 1-4",
            "R12.2-R12.9 P all closed",
        ],
    }

    # --------------------------------------------------------------
    # Step 1: Load chain + spot
    # --------------------------------------------------------------
    _section(f"Step 1: load chain + TAIEX spot {args.start} → {args.end}")
    t0 = time.perf_counter()
    chain = load_chain(str(CACHE_DIR), args.start, args.end, layer="strategy_view")
    if chain.empty:
        print(f"FAIL: no shards in [{args.start}, {args.end}]", flush=True)
        return 1
    # R12.3 P fix: TAIFEX cache 偶含已過期合約 settlement records (Pattern 9
    # 真資料邊界). enrich_pipeline.add_dte 對 dte<0 raise. Pre-filter
    # (expiry >= date) 跨 5yr/7yr 範圍可能 filter 數百~上千 rows.
    pre_filter_rows = len(chain)
    chain = chain[chain["expiry"] >= chain["date"]].reset_index(drop=True)
    n_filtered = pre_filter_rows - len(chain)
    if n_filtered > 0:
        print(f"  filtered {n_filtered:,} post-expiry rows", flush=True)
    run_meta["chain_n_rows_filtered_postexpiry"] = int(n_filtered)
    # spot_series for chain enrich (must cover backtest window only — under
    # forward_fill policy enrich rejects mismatched dates beyond ~3-day gap).
    spot_series = _load_taiex_spot(args.start, args.end)
    # underlying_returns for regime gate (R12.2 P1 fix Codex audit): MUST
    # extend pre-start by REGIME_HISTORY_BUFFER_DAYS so that fold 0 test_start
    # has >= 504 returns history for HMM gate. _load_taiex_returns_with_buffer
    # asserts >= 504 pre-start returns or raises (fail-fast prereq gate).
    underlying_returns = _load_taiex_returns_with_buffer(args.start, args.end)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step1_load"] = round(t1 - t0, 2)
    n_dates = chain["date"].nunique()
    print(f"  chain: {len(chain):,} rows / {n_dates} dates / {t1 - t0:.1f}s", flush=True)
    print(f"  TAIEX spot (window): {len(spot_series)} days", flush=True)
    print(
        f"  TAIEX returns (with {REGIME_HISTORY_BUFFER_DAYS}-day buffer): "
        f"{len(underlying_returns)} returns",
        flush=True,
    )
    run_meta["chain_n_rows"] = int(len(chain))
    run_meta["chain_n_dates"] = int(n_dates)
    run_meta["underlying_returns_with_buffer"] = int(len(underlying_returns))

    # --------------------------------------------------------------
    # Step 2: enrich + add_model_price + surface cache coverage hard gate
    # --------------------------------------------------------------
    _section("Step 2: enrich_pipeline + add_model_price + surface coverage gate")
    t0 = time.perf_counter()
    enriched, _q_audit = enrich_pipeline(
        chain,
        spot_series,
        spot_missing_policy="forward_fill",
        on_iv_solver_fail="nan",
    )
    surface_records = load_surface_records(str(CACHE_DIR), args.start, args.end)
    print(f"  surface_records loaded: {len(surface_records)}", flush=True)
    enriched = add_model_price(enriched, surface_records)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step2_enrich"] = round(t1 - t0, 2)
    iv_fill = float(enriched["iv"].notna().mean())
    mp_fill = float(enriched["model_price"].notna().mean())
    print(
        f"  enriched {len(enriched):,} rows / IV fill {iv_fill:.1%} / "
        f"model_price fill {mp_fill:.1%} / {t1 - t0:.1f}s",
        flush=True,
    )
    run_meta["iv_fill_rate"] = iv_fill
    run_meta["model_price_fill_rate"] = mp_fill

    # R12.2 P2 fix (Codex audit): surface cache coverage hard gate.
    # R12.6 P3 fix moved to main() entry — reject 5yr+skip combination at
    # parse-time before enrich runs (was: post-enrich late raise wasted compute).
    if not args.skip_surface_coverage_gate:
        _validate_surface_coverage(enriched, surface_records, args)

    # --------------------------------------------------------------
    # Step 4: 6 scenario walk-forward loop
    # --------------------------------------------------------------
    requested = [s.strip() for s in args.scenarios.split(",")] if args.scenarios != "all" else None
    # R12.12 P fix (Codex audit): --no-cost-model flag for diagnostic baseline.
    # Default: RetailCostModel(commission=12 NTD / tax=10 bps / slippage=15 bps).
    # Cost-free runs help disambiguate「策略本身沒 alpha」vs「retail friction 壓死」.
    if args.no_cost_model:
        cost_model = None
        print("  [diagnostic] --no-cost-model: cost_model=None (Sharpe is upper bound)", flush=True)
    else:
        cost_model = RetailCostModel()  # R12.0 P4a defaults: 12 NTD / 10 bps / 15 bps
    run_meta["cost_model_disabled"] = bool(args.no_cost_model)
    fill_model = WorstSideFillModel(cost_model=cost_model)
    # R12.2 P3 fix: smoke uses reduced walk-forward window so >=1 fold runs.
    if scope_yr == "smoke":
        train_w, test_w, step_w = 63, 21, 21
    else:
        train_w, test_w, step_w = 252, 63, 63  # R12 plan disjoint OOS
    cfg = WalkForwardConfig(
        train_window_days=train_w,
        test_window_days=test_w,
        step_days=step_w,
        expanding=False,
        mark_policy="mid_with_surface_fallback",
        initial_capital=args.initial_capital,
    )

    all_folds_rows: list[dict] = []
    all_scenarios_rows: list[dict] = []
    all_daily_pnl_rows: list[dict] = []
    all_rejected_rows: list[dict] = []
    monitor_metrics_per_scenario: dict[str, dict] = {}
    daily_pnl_per_scenario: dict[str, pd.Series] = {}

    for scenario_id, strategy_name, regime_gate_name in SCENARIOS:
        if requested is not None and scenario_id not in requested:
            continue
        _section(f"Scenario {scenario_id}  ({strategy_name} × {regime_gate_name})")
        t_s = time.perf_counter()
        factory = _make_strategy_factory(strategy_name, regime_gate_name, underlying_returns)
        try:
            result = walk_forward_backtest(
                factory,
                enriched,
                cfg,
                underlying_returns=underlying_returns,
                fill_model=fill_model,
            )
        except (ValueError, RuntimeError) as e:
            print(f"  ERROR: walk_forward fail — {type(e).__name__}: {e}", flush=True)
            run_meta["scenarios_run"].append({"scenario": scenario_id, "error": str(e)})
            continue
        t_e = time.perf_counter()
        dur = t_e - t_s
        n_ok = result.n_folds - result.n_failed_folds
        print(
            f"  done {n_ok}/{result.n_folds} folds OK / "
            f"agg_sharpe={result.metrics.get('sharpe', float('nan')):.3f} / {dur:.1f}s",
            flush=True,
        )
        run_meta["scenarios_run"].append(
            {"scenario": scenario_id, "duration_sec": round(dur, 2), "n_folds": result.n_folds}
        )

        meta = _scenario_metadata(
            scenario_id, strategy_name, regime_gate_name, scope_yr, args.start, args.end
        )
        all_folds_rows.extend(_folds_to_rows(result, meta, cfg))
        all_scenarios_rows.append(
            _scenario_aggregate_row_with_cost_flag(
                result, meta, cfg, cost_model_disabled=bool(args.no_cost_model)
            )
        )
        all_daily_pnl_rows.extend(_daily_pnl_to_rows(result, meta))
        all_rejected_rows.extend(_rejected_reasons_to_rows(result, meta))
        daily_pnl_per_scenario[scenario_id] = result.daily_pnl

        # mark_audit aggregate per scenario
        mark_audit_concat = (
            pd.concat([f.mark_audit for f in result.folds if not f.mark_audit.empty])
            if any(not f.mark_audit.empty for f in result.folds)
            else pd.DataFrame(
                columns=[
                    "fallback_rate",
                    "n_legs_marked",
                    "n_fallback_settle",
                    "n_fallback_surface",
                ]
            )
        )
        monitor_metrics_per_scenario[scenario_id] = summarise_mark_audit(mark_audit_concat)
        # rejected_reasons summary (currently empty — see _rejected_reasons_to_rows note)
        monitor_metrics_per_scenario[scenario_id]["rejected_reasons_n"] = sum(
            1 for r in all_rejected_rows if r["scenario"] == scenario_id
        )

    # --------------------------------------------------------------
    # Step 5: cross-scenario divergence + ablation
    # --------------------------------------------------------------
    _section("Step 5: cross-scenario divergence + regime ablation")
    divergence = summarise_scenario_pnl_divergence(daily_pnl_per_scenario)
    print(f"  scenario divergence keys: {len(divergence)}", flush=True)

    scenarios_df = pd.DataFrame(all_scenarios_rows)
    ablation_rows = _ablation_matrix_rows(scenarios_df, scope_yr)

    # --------------------------------------------------------------
    # Step 6: write outputs (CSV / JSON / MD)
    # --------------------------------------------------------------
    _section("Step 6: write reports/")

    # R12.2 P4 fix (Codex audit): stable CSV schema even when 0 rows.
    # pandas.read_csv on a zero-byte file raises EmptyDataError. Always write
    # the header line so downstream pandas/Excel/notebooks can load.
    metadata_cols = [
        "scenario",
        "strategy",
        "regime_gate",
        "backtest_scope_yr",
        "backtest_start",
        "backtest_end",
    ]
    folds_cols = metadata_cols + [
        "train_window_days",
        "test_window_days",
        "step_days",
        "fold_index",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "n_trades",
        "metric_sharpe",
        "metric_max_drawdown",
        "metric_win_rate",
        "final_cash",
        "final_unrealised",
        "error",
    ]
    daily_pnl_cols = metadata_cols + ["fold_index", "date", "daily_pnl_twd"]
    rejected_cols = metadata_cols + ["fold_index", "date", "path", "reason", "leg"]

    def _write_csv(path: Path, rows: list[dict], schema_cols: list[str]) -> None:
        df = pd.DataFrame(rows, columns=schema_cols) if rows else pd.DataFrame(columns=schema_cols)
        df.to_csv(path, index=False)

    folds_csv = REPORTS_DIR / f"week6_{scope_yr}_folds.csv"
    _write_csv(folds_csv, all_folds_rows, folds_cols)
    print(f"  {folds_csv.name}: {len(all_folds_rows)} rows", flush=True)

    scenarios_csv = REPORTS_DIR / f"week6_{scope_yr}_scenarios.csv"
    scenarios_df.to_csv(scenarios_csv, index=False)
    print(f"  {scenarios_csv.name}: {len(scenarios_df)} rows", flush=True)

    daily_pnl_csv = REPORTS_DIR / f"week6_{scope_yr}_daily_pnl.csv"
    _write_csv(daily_pnl_csv, all_daily_pnl_rows, daily_pnl_cols)
    print(f"  {daily_pnl_csv.name}: {len(all_daily_pnl_rows)} rows", flush=True)

    rejected_csv = REPORTS_DIR / f"week6_{scope_yr}_rejected_reasons.csv"
    _write_csv(rejected_csv, all_rejected_rows, rejected_cols)
    print(f"  {rejected_csv.name}: {len(all_rejected_rows)} rows", flush=True)

    ablation_csv = REPORTS_DIR / f"week6_{scope_yr}_ablation_matrix.csv"
    pd.DataFrame(ablation_rows).to_csv(ablation_csv, index=False)
    print(f"  {ablation_csv.name}: {len(ablation_rows)} rows", flush=True)

    monitor_json = REPORTS_DIR / f"week6_{scope_yr}_monitor_metrics.json"
    monitor_payload = {
        "metrics_per_scenario": monitor_metrics_per_scenario,
        "scenario_pnl_divergence": divergence,
    }
    monitor_json.write_text(json.dumps(monitor_payload, indent=2, default=str), encoding="utf-8")
    print(f"  {monitor_json.name}: written", flush=True)

    # Run meta + summary
    run_meta["total_duration_sec"] = round(time.perf_counter() - run_start, 2)
    # R12.10 P fix (Codex audit caveat): HMM convergence warnings count
    run_meta["hmm_convergence_warnings_count"] = int(hmm_warning_counter.count)
    print(
        f"  HMM convergence warnings (R12.10 caveat tracking): {hmm_warning_counter.count}",
        flush=True,
    )
    run_meta_path = REPORTS_DIR / f"week6_{scope_yr}_run_meta.json"
    run_meta_path.write_text(json.dumps(run_meta, indent=2, default=str), encoding="utf-8")
    print(f"  {run_meta_path.name}: written", flush=True)

    summary_md = _build_summary_md(scenarios_df, ablation_rows, run_meta, monitor_payload)
    summary_path = REPORTS_DIR / f"week6_{scope_yr}_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    print(f"  {summary_path.name}: written", flush=True)

    print(f"\nDONE. Total {run_meta['total_duration_sec']:.1f}s.", flush=True)
    return 0


def _df_to_markdown(df: pd.DataFrame) -> str:
    """Manual markdown table writer (avoids tabulate optional dependency)."""
    if df.empty:
        return "(empty)\n"
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"

    def _fmt(v: object) -> str:
        if isinstance(v, float):
            if not np.isfinite(v):
                return "NaN" if np.isnan(v) else str(v)
            return f"{v:.4f}"
        return str(v)

    body = "\n".join("| " + " | ".join(_fmt(v) for v in row) + " |" for row in df.values)
    return f"{header}\n{sep}\n{body}\n"


def _build_summary_md(
    scenarios_df: pd.DataFrame,
    ablation_rows: list[dict],
    run_meta: dict,
    monitor_payload: dict,
) -> str:
    """Pro 報告 narrative — 對齊 Phase 1 出口條件硬閾值."""
    lines: list[str] = []
    lines.append(f"# Week 6 {run_meta['scope']} Backtest Summary\n")
    lines.append(f"- Range: {run_meta['start_date']} → {run_meta['end_date']}\n")
    lines.append(f"- Initial capital per fold: NT${run_meta['initial_capital']:,.0f}\n")
    lines.append(f"- Total wall time: {run_meta['total_duration_sec']:.1f}s\n")
    lines.append(f"- Codex audits applied: {', '.join(run_meta['codex_audits_applied'])}\n\n")

    lines.append("## Pro 出口條件閾值\n")
    lines.append("| 指標 | 閾值 |\n|---|---|\n")
    lines.append("| Sharpe | ≥ 1.0 |\n")
    lines.append("| Max DD | < 20% |\n")
    lines.append("| Calmar | > 0.5 |\n")
    lines.append("| Bootstrap 95% CI | 不跨零 |\n\n")

    lines.append("## 6 Scenario 結果\n")
    if not scenarios_df.empty:
        cols = [
            "scenario",
            "agg_sharpe",
            "bootstrap_ci_low",
            "bootstrap_ci_high",
            "permutation_p_value",
            "deflated_sharpe",
            "agg_max_drawdown",
            "calmar_ratio",
            "total_trades",
            "n_folds_total",
        ]
        existing = [c for c in cols if c in scenarios_df.columns]
        lines.append(_df_to_markdown(scenarios_df[existing]))
        lines.append("\n")

    # R12.6 P2 fix (Codex audit): surface fallback metrics 對 caller 可見
    # (settle_3rd_fallback 之前只在 JSON, primary report 漏接).
    lines.append("## Surface Fallback Metrics\n")
    if not scenarios_df.empty:
        fallback_cols = [
            "scenario",
            "n_fallback_surface_total",
            "n_fallback_settle_total",
            "n_fallback_settle_3rd_total",
            "settle_3rd_fallback_ratio",
            "fallback_legs_ratio",
            "avg_fallback_rate",
        ]
        existing = [c for c in fallback_cols if c in scenarios_df.columns]
        lines.append(_df_to_markdown(scenarios_df[existing]))
        lines.append("\n")

    lines.append("## Regime Gate Ablation\n")
    if ablation_rows:
        lines.append(_df_to_markdown(pd.DataFrame(ablation_rows)))
        lines.append("\n")

    lines.append("## Monitor Metrics\n")
    lines.append("```json\n")
    lines.append(json.dumps(monitor_payload, indent=2, default=str))
    lines.append("\n```\n\n")

    lines.append("## Caveats (R12.1 Codex)\n")
    lines.append(
        "- permutation_test 用 sign-flip (Politis & Romano 2010); H0「對稱、零 drift」對 IC "
        "short-premium negative-skew PnL 違反假設, p_value 只能輔助, 不能當唯一 alpha 顯著性結論. "
        "Bootstrap 95% CI 不跨零仍是 Phase 1 出口主硬條件.\n"
    )
    lines.append(
        "- walk-forward step=test=63 (disjoint OOS, R12.0 P3); 5yr ~= 16 folds, 7yr ~= 24 folds. "
        "原 plan 預設 1mo step (~48 folds) 會 OOS 重疊 → aggregate metric inflate, 已禁.\n"
    )
    lines.append(
        "- TXO retail 摩擦 default: NT$12 commission/口 + 10 bps tax (TAIFEX 0.001) + 15 bps slippage "
        "(R12.0 P4a; pre-fix tax 2 bps 5x 低估).\n"
    )
    # R12.10 P fix + R12.12 P fix (Codex audit caveat): HMM convergence warning
    # count visibility + strategy NO-GO honest wording.
    hmm_warn = run_meta.get("hmm_convergence_warnings_count", 0)
    lines.append(
        f"- HMM convergence warnings: {hmm_warn}. hmmlearn 對短窗 / noisy regime "
        "period 偶發 'Model is not converging'; high count signals regime-detection "
        "unreliability.\n"
    )
    # R12.12 strategy NO-GO note (Codex R12.10/R12.11 5yr 真跑 verdict)
    if run_meta.get("scope") == "5yr":
        lines.append("\n## Phase 1 Strategy Verdict (R12.12 honest report)\n")
        lines.append(
            "- 5yr OOS Sharpe (all scenarios): negative; HMM gate 0-1 trades / "
            "15 folds → **strategy NO-GO for paper trading**. Phase 1 alpha "
            "hypothesis (IC + Vertical with regime gate on 5yr TXO) **falsified**. "
            "Pro 紀律: 不反向改 strategy chasing positive Sharpe (= data snooping); "
            "honest 接受結論. 詳 [docs/phase1_conclusion.md](../docs/phase1_conclusion.md).\n"
        )
    if run_meta.get("cost_model_disabled"):
        lines.append(
            "- **--no-cost-model 模式**: cost_model=None (commission/tax/slippage 全 0). "
            "Sharpe 是 upper bound, NOT realistic — 不可作 paper trading 依據.\n"
        )
    return "".join(lines)


if __name__ == "__main__":
    sys.exit(main())
