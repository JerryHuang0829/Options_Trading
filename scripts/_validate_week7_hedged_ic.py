"""Phase 1 Week 7 Day 7.2 — 5yr × 3 scenario walk-forward (HedgedGatedIC + calendar mode).

Quick A scope per Day 7.0 feasibility audit:
  - 5-cohort multi-expiry ladder NOT viable (mean 1.44 unique expiry/day)
  - 1-cohort hedged IC (calendar mode) IS viable (cost ratio 1.35x median)
  - Straddle mode skipped (8.36x median = NO-GO per audit)

3 scenarios: HedgedGatedIC × {vanilla / IV percentile / HMM 2-state}.

Per scenario walk-forward 252-day train / 63-day disjoint OOS / 15 folds.
Pro stats: Bootstrap CI 95% / sign-flip permutation / Deflated Sharpe / Calmar.

Outputs: reports/week7_*.{json,csv,md,log} (mirrors Phase 1 Week 6 schema +
hedge_attach_rate / hedge_attach_count / hedge_fail_count cols).

Pre-req: surface_fits cache 100% covers 5yr (Phase 1 done; verified Day 7.0).

CLI:
  python scripts/_validate_week7_hedged_ic.py
  python scripts/_validate_week7_hedged_ic.py --start 2021-04-01 --end 2026-04-28
  python scripts/_validate_week7_hedged_ic.py --smoke    # 1-month sub-set
  python scripts/_validate_week7_hedged_ic.py --no-cost-model  # cost-free baseline
"""

from __future__ import annotations

# UTF-8 reexec gate (R12.8/R12.9 P1 lineage)
import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402


def _should_reexec_for_utf8() -> bool:
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
    import subprocess  # noqa: E402

    child_env = os.environ.copy()
    child_env["_R12_8_UTF8_REEXEC"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
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

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts._hedged_gated_ic import HedgedGatedIronCondor  # noqa: E402
from src.backtest.execution import RetailCostModel, WorstSideFillModel  # noqa: E402
from src.backtest.monitor import summarise_mark_audit  # noqa: E402
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

DEFAULT_START = "2021-04-01"
DEFAULT_END = "2026-04-28"

# Phase 1 Week 7 Quick A: 3 scenarios (HedgedGatedIC × 3 regime gates)
SCENARIOS: list[tuple[str, str]] = [
    ("HedgedIC_vanilla", "vanilla"),
    ("HedgedIC_IV_percentile", "IV_percentile"),
    ("HedgedIC_HMM", "HMM"),
]

REGIME_HISTORY_BUFFER_DAYS = 900
SURFACE_COVERAGE_PCT_MIN = 0.95


def _section(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)


def _load_taiex_spot(start: str, end: str) -> pd.Series:
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values), name="spot")


def _load_taiex_returns_with_buffer(start: str, end: str) -> pd.Series:
    df = pd.read_csv(TAIEX_CSV)
    df["date"] = pd.to_datetime(df["date"])
    buffered_start = pd.Timestamp(start) - pd.Timedelta(days=REGIME_HISTORY_BUFFER_DAYS)
    df = df[(df["date"] >= buffered_start) & (df["date"] <= pd.Timestamp(end))]
    if df.empty:
        raise ValueError(f"TAIEX history empty for [{buffered_start.date()}, {end}]")
    spot = pd.Series(df["close"].values, index=pd.DatetimeIndex(df["date"].values))
    log_ret = np.log(spot / spot.shift(1))
    returns = pd.Series(log_ret).dropna()
    pre_start = returns[returns.index < pd.Timestamp(start)]
    if len(pre_start) < 504:
        raise ValueError(
            f"TAIEX pre-{start} history only {len(pre_start)} returns; need >= 504 for HMM"
        )
    return returns


def _validate_surface_coverage(enriched, surface_records, args) -> None:
    if not surface_records:
        raise ValueError("Surface cache empty; run _validate_surface_mark_5_4a.py first")
    chain_dates = pd.DatetimeIndex(pd.to_datetime(enriched["date"]).unique()).sort_values()
    surface_dates = pd.DatetimeIndex(sorted({pd.Timestamp(r.date) for r in surface_records}))
    coverage_pct = surface_dates.intersection(chain_dates).size / max(len(chain_dates), 1)

    missing_bidask = enriched["bid"].isna() | enriched["ask"].isna()
    missing_mp = enriched["model_price"].isna()
    missing_settle = enriched["settle"].isna()
    danger_rows = int((missing_bidask & missing_mp).sum())
    truly_unmarkable = int((missing_bidask & missing_mp & missing_settle).sum())

    print(
        f"  surface coverage: {coverage_pct:.1%} ({surface_dates.size}/{chain_dates.size} dates)",
        flush=True,
    )
    print(f"  danger_rows: {danger_rows:,} / truly_unmarkable: {truly_unmarkable:,}", flush=True)

    if coverage_pct < SURFACE_COVERAGE_PCT_MIN:
        raise ValueError(
            f"surface coverage {coverage_pct:.1%} < {SURFACE_COVERAGE_PCT_MIN:.0%} minimum"
        )
    if truly_unmarkable > 0:
        raise ValueError(f"{truly_unmarkable:,} truly_unmarkable rows; engine will raise")


def _make_hedged_strategy_factory(regime_gate_name: str, underlying_returns: pd.Series):
    """Build callable(train_returns) -> HedgedGatedIC wrapped by regime gate."""

    def factory(train_returns: pd.Series) -> RegimeWrappedStrategy:
        _ = train_returns  # PIT slice already done by walk_forward
        # HedgedGatedIC w/ Phase 1 IC defaults (target_dte=30, exit_dte=14 for week6 parity)
        base = HedgedGatedIronCondor(
            short_delta=0.16,
            wing_delta=0.08,
            target_dte=30,
            exit_dte=14,
            hedge_dte_offset=30,  # back leg DTE = front + 30
        )
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
        return RegimeWrappedStrategy(
            base=base,
            regime_gate=gate,
            returns_history=underlying_returns if gate is not None else None,
        )

    return factory


def _scenario_metadata(
    scenario_id: str,
    regime_gate_name: str,
    scope_yr: str,
    backtest_start: str,
    backtest_end: str,
) -> dict:
    return {
        "scenario": scenario_id,
        "strategy": "HedgedIC",
        "regime_gate": regime_gate_name,
        "hedge_mode": "calendar",
        "backtest_scope_yr": scope_yr,
        "backtest_start": backtest_start,
        "backtest_end": backtest_end,
    }


def _folds_to_rows(result: AggregatedResult, metadata: dict, cfg: WalkForwardConfig) -> list[dict]:
    rows: list[dict] = []
    for fold in result.folds:
        rows.append(
            {
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
        )
    return rows


def _daily_pnl_to_rows(result: AggregatedResult, metadata: dict) -> list[dict]:
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


def _rejected_reasons_to_rows(result: AggregatedResult, metadata: dict) -> list[dict]:
    rows: list[dict] = []
    for fold in result.folds:
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


def _scenario_aggregate_row(
    result: AggregatedResult,
    metadata: dict,
    cfg: WalkForwardConfig,
    initial_capital: float,
    cost_model_disabled: bool,
) -> dict:
    daily_pnl_arr = result.daily_pnl.to_numpy() if not result.daily_pnl.empty else np.array([])
    if len(daily_pnl_arr) > 30:
        try:
            ci_low, ci_high = bootstrap_ci(
                daily_pnl_arr, statistic="sharpe", n_iter=1000, ci=0.95, seed=42
            )
        except (ValueError, RuntimeError):
            ci_low = ci_high = float("nan")
        try:
            _obs, _null, p_value = permutation_test(daily_pnl_arr, n_iter=1000, seed=42)
        except (ValueError, RuntimeError):
            p_value = float("nan")
        try:
            dsr = deflated_sharpe(
                observed_sharpe=result.metrics.get("sharpe", float("nan")),
                n_trials=3,  # 3 scenarios in week 7
                T=len(daily_pnl_arr),
            )
        except (ValueError, RuntimeError):
            dsr = float("nan")
        try:
            cal = calmar_ratio(daily_pnl_arr, initial_capital=initial_capital)
        except (ValueError, RuntimeError):
            cal = float("nan")
    else:
        ci_low = ci_high = p_value = dsr = cal = float("nan")

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
        "n_fallback_settle_3rd_total": monitor["n_fallback_settle_3rd_total"],
        "settle_3rd_fallback_ratio": monitor["settle_3rd_fallback_ratio"],
        "fallback_legs_ratio": monitor["fallback_legs_ratio"],
        "avg_fallback_rate": monitor["avg_fallback_rate"],
        "cost_model_disabled": cost_model_disabled,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Week 7 — 5yr × 3 scenario hedged IC walk-forward")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--skip-surface-coverage-gate", action="store_true")
    parser.add_argument("--no-cost-model", action="store_true")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument(
        "--scenarios",
        default="all",
        help="Comma-separated subset (e.g. HedgedIC_vanilla); 'all' for full run",
    )
    args = parser.parse_args(argv)

    if args.smoke:
        args.start = "2024-04-01"
        args.end = "2025-04-01"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    scope_yr = "5yr" if not args.smoke else "smoke"

    if args.skip_surface_coverage_gate and scope_yr == "5yr":
        raise ValueError("skip-surface-coverage-gate is for smoke only; full 5yr must NOT skip")

    log_path = REPORTS_DIR / f"week7_{scope_yr}_console.log"
    log_buf = StringIO()

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


def _run(args: argparse.Namespace, scope_yr: str) -> int:
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
    _hmm_logger.propagate = True
    _root = _logging.getLogger()
    _has_stderr = any(
        isinstance(h, _logging.StreamHandler)
        and not isinstance(h, _logging.FileHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
        for h in _root.handlers
    )
    if not _has_stderr:
        _root.addHandler(_logging.StreamHandler())

    run_start = time.perf_counter()
    run_meta: dict = {
        "script": "_validate_week7_hedged_ic.py",
        "scope": scope_yr,
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": args.initial_capital,
        "cost_model_disabled": bool(args.no_cost_model),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "phase_durations_sec": {},
        "scenarios_run": [],
        "phase1_week7_quick_a": True,
        "feasibility_audit_ref": "reports/week7_feasibility.md (Day 7.0)",
        "hedge_mode": "calendar",
    }

    # Step 1: load chain + spot
    _section(f"Step 1: load chain + TAIEX spot {args.start} → {args.end}")
    t0 = time.perf_counter()
    chain = load_chain(str(CACHE_DIR), args.start, args.end, layer="strategy_view")
    if chain.empty:
        print(f"FAIL: no shards in [{args.start}, {args.end}]", flush=True)
        return 1
    pre_filter = len(chain)
    chain = chain[chain["expiry"] >= chain["date"]].reset_index(drop=True)
    n_filtered = pre_filter - len(chain)
    if n_filtered > 0:
        print(f"  filtered {n_filtered:,} post-expiry rows", flush=True)
    spot_series = _load_taiex_spot(args.start, args.end)
    underlying_returns = _load_taiex_returns_with_buffer(args.start, args.end)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step1_load"] = round(t1 - t0, 2)
    print(
        f"  chain: {len(chain):,} rows / {chain['date'].nunique()} dates / {t1 - t0:.1f}s",
        flush=True,
    )
    print(f"  TAIEX spot (window): {len(spot_series)} days", flush=True)

    # Step 2: enrich + add_model_price + surface gate
    _section("Step 2: enrich_pipeline + add_model_price + surface gate")
    t0 = time.perf_counter()
    enriched, _q_audit = enrich_pipeline(
        chain,
        spot_series,
        spot_missing_policy="forward_fill",
        on_iv_solver_fail="nan",
    )
    surface_records = load_surface_records(str(CACHE_DIR), args.start, args.end)
    enriched = add_model_price(enriched, surface_records)
    t1 = time.perf_counter()
    run_meta["phase_durations_sec"]["step2_enrich"] = round(t1 - t0, 2)
    iv_fill = float(enriched["iv"].notna().mean())
    print(
        f"  enriched {len(enriched):,} rows / IV fill {iv_fill:.1%} / {t1 - t0:.1f}s",
        flush=True,
    )
    if not args.skip_surface_coverage_gate:
        _validate_surface_coverage(enriched, surface_records, args)

    # Step 3: cost model + walk-forward config
    if args.no_cost_model:
        cost_model = None
        print("  [diagnostic] --no-cost-model: cost_model=None", flush=True)
    else:
        cost_model = RetailCostModel()
    fill_model = WorstSideFillModel(cost_model=cost_model)
    if scope_yr == "smoke":
        train_w, test_w, step_w = 63, 21, 21
    else:
        train_w, test_w, step_w = 252, 63, 63
    cfg = WalkForwardConfig(
        train_window_days=train_w,
        test_window_days=test_w,
        step_days=step_w,
        expanding=False,
        mark_policy="mid_with_surface_fallback",
        initial_capital=args.initial_capital,
    )

    # Step 4: 3 scenario walk-forward loop
    requested = [s.strip() for s in args.scenarios.split(",")] if args.scenarios != "all" else None
    all_folds_rows: list[dict] = []
    all_scenarios_rows: list[dict] = []
    all_daily_pnl_rows: list[dict] = []
    all_rejected_rows: list[dict] = []
    monitor_metrics_per_scenario: dict[str, dict] = {}

    for scenario_id, regime_gate_name in SCENARIOS:
        if requested is not None and scenario_id not in requested:
            continue
        _section(f"Scenario {scenario_id}  (HedgedIC × {regime_gate_name} × calendar)")
        t_s = time.perf_counter()
        factory = _make_hedged_strategy_factory(regime_gate_name, underlying_returns)
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
        meta = _scenario_metadata(scenario_id, regime_gate_name, scope_yr, args.start, args.end)
        all_folds_rows.extend(_folds_to_rows(result, meta, cfg))
        all_scenarios_rows.append(
            _scenario_aggregate_row(
                result, meta, cfg, args.initial_capital, bool(args.no_cost_model)
            )
        )
        all_daily_pnl_rows.extend(_daily_pnl_to_rows(result, meta))
        all_rejected_rows.extend(_rejected_reasons_to_rows(result, meta))
        # Hedge attach metrics from rejected_reasons (path == 'hedge_attach')
        hedge_rej = [
            r
            for r in all_rejected_rows
            if r["scenario"] == scenario_id and r.get("path") == "hedge_attach"
        ]
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
        monitor_metrics_per_scenario[scenario_id] = {
            **summarise_mark_audit(mark_audit_concat),
            "hedge_attach_fail_count": len(hedge_rej),
            "hedge_attach_fail_reasons_sample": [r["reason"] for r in hedge_rej[:5]],
        }

    # Step 5: write reports/
    _section("Step 5: write reports/")
    metadata_cols = [
        "scenario",
        "strategy",
        "regime_gate",
        "hedge_mode",
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

    folds_csv = REPORTS_DIR / f"week7_{scope_yr}_folds.csv"
    _write_csv(folds_csv, all_folds_rows, folds_cols)
    print(f"  {folds_csv.name}: {len(all_folds_rows)} rows", flush=True)

    scenarios_df = pd.DataFrame(all_scenarios_rows)
    scenarios_csv = REPORTS_DIR / f"week7_{scope_yr}_scenarios.csv"
    scenarios_df.to_csv(scenarios_csv, index=False)
    print(f"  {scenarios_csv.name}: {len(scenarios_df)} rows", flush=True)

    daily_csv = REPORTS_DIR / f"week7_{scope_yr}_daily_pnl.csv"
    _write_csv(daily_csv, all_daily_pnl_rows, daily_pnl_cols)
    print(f"  {daily_csv.name}: {len(all_daily_pnl_rows)} rows", flush=True)

    rejected_csv = REPORTS_DIR / f"week7_{scope_yr}_rejected_reasons.csv"
    _write_csv(rejected_csv, all_rejected_rows, rejected_cols)
    print(f"  {rejected_csv.name}: {len(all_rejected_rows)} rows", flush=True)

    monitor_json = REPORTS_DIR / f"week7_{scope_yr}_monitor_metrics.json"
    monitor_json.write_text(
        json.dumps(monitor_metrics_per_scenario, indent=2, default=str), encoding="utf-8"
    )
    print(f"  {monitor_json.name}", flush=True)

    run_meta["phase_durations_sec"]["total"] = round(time.perf_counter() - run_start, 2)
    run_meta["hmm_warning_count"] = hmm_warning_counter.count
    run_meta_json = REPORTS_DIR / f"week7_{scope_yr}_run_meta.json"
    run_meta_json.write_text(json.dumps(run_meta, indent=2, default=str), encoding="utf-8")
    print(f"  {run_meta_json.name}", flush=True)

    # Summary md
    summary_md_lines: list[str] = []
    summary_md_lines.append(f"# Phase 1 Week 7 — Hedged IC {scope_yr.upper()} Walk-Forward Summary")
    summary_md_lines.append("")
    summary_md_lines.append(f"- **Generated**: {datetime.now(UTC).isoformat()}")
    summary_md_lines.append(f"- **Window**: {args.start} → {args.end}")
    summary_md_lines.append(f"- **Initial capital**: NT${args.initial_capital:,.0f}")
    summary_md_lines.append(f"- **Cost model disabled**: {args.no_cost_model}")
    summary_md_lines.append("- **Hedge mode**: calendar (call-only)")
    summary_md_lines.append(f"- **Scenarios run**: {len(all_scenarios_rows)}")
    summary_md_lines.append("")
    summary_md_lines.append("## Per-Scenario Aggregate")
    summary_md_lines.append("")
    summary_md_lines.append(
        "| Scenario | Sharpe | CI low | CI high | p-value | Max DD | Calmar | Trades |"
    )
    summary_md_lines.append("|---|---|---|---|---|---|---|---|")
    for r in all_scenarios_rows:
        summary_md_lines.append(
            f"| {r['scenario']} | {r['agg_sharpe']:.3f} | "
            f"{r['bootstrap_ci_low']:.3f} | {r['bootstrap_ci_high']:.3f} | "
            f"{r['permutation_p_value']:.3f} | {r['agg_max_drawdown']:.4f} | "
            f"{r['calmar_ratio']:.3f} | {r['total_trades']} |"
        )
    summary_md_lines.append("")
    summary_md_lines.append("## Pro 出口條件 evaluation")
    summary_md_lines.append("")
    summary_md_lines.append(
        "| Scenario | Sharpe ≥1.0? | CI 不跨零? | Max DD <20%? | Calmar >0.5? |"
    )
    summary_md_lines.append("|---|---|---|---|---|")
    for r in all_scenarios_rows:
        sharpe_ok = "✅" if r["agg_sharpe"] >= 1.0 else "❌"
        ci_ok = "✅" if r["bootstrap_ci_low"] > 0 else "❌"
        dd_ok = "✅" if abs(r["agg_max_drawdown"]) < 0.20 else "❌"
        cal_ok = "✅" if r["calmar_ratio"] > 0.5 else "❌"
        summary_md_lines.append(
            f"| {r['scenario']} | {sharpe_ok} ({r['agg_sharpe']:.2f}) | "
            f"{ci_ok} | {dd_ok} ({r['agg_max_drawdown']:.2%}) | "
            f"{cal_ok} ({r['calmar_ratio']:.2f}) |"
        )
    summary_md = REPORTS_DIR / f"week7_{scope_yr}_summary.md"
    summary_md.write_text("\n".join(summary_md_lines), encoding="utf-8")
    print(f"  {summary_md.name}", flush=True)

    print(
        f"\n  Total: {time.perf_counter() - run_start:.1f}s  /  HMM warnings: {hmm_warning_counter.count}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
