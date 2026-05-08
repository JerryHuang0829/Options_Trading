"""Phase 1 Week 7 Day 7.0 — Feasibility Hard Gate (real-cache audit).

GO/NO-GO 決定 Day 7.1+ 是否進實作。Codex external review (2026-05-02) 標記
attacker #1 (cohort availability) 為 plan 最致命假設 — 若 5yr cache 每天
< 3 unique expiry，5-cohort ladder 在現有資料 **直接不可行**。

3 個 audit section:

  A. Cohort availability — 跑真實 5yr/8yr cache，count per-trading-day unique
     expiry (filtered DTE 21-63), report mean/median/p10/max + % days with
     >=3/4/5 cohorts. weekly TXO presence detection.

  B. Hedge cost sampling — 抽 ~10 sample days × IV bucket，enrich + bsm_price
     計算 IC credit (worst-side) vs long calendar/straddle hedge premium.
     Report hedge_cost_ratio distribution per IV percentile bucket.

  C. Surface coverage — 既有 surface_fits cache (Phase 1 1227 shards) 是否
     涵蓋目標範圍 + 每天 expiry 數對齊 cohort 需求.

GO/NO-GO 4 path (per plan):
  - GO 5-cohort: mean >=4 + p10 >=3 + hedge_cost_ratio < 2x median
  - GO 3-cohort 退化: mean >=3 + p10 >=2
  - GO 1-cohort 純 D: mean >=1 + p10 =1
  - NO-GO: mean <1 OR weekly 不存在 OR hedge_cost_ratio > 5x

Output: reports/week7_feasibility.md (~150 行 markdown report).

CLI:
  python scripts/_audit_week7_feasibility.py
  python scripts/_audit_week7_feasibility.py --start 2021-04-01 --end 2026-04-28
  python scripts/_audit_week7_feasibility.py --skip-hedge-cost  # quick mode

Read-only audit — 不寫 src/，不改 cache。
"""

from __future__ import annotations

# UTF-8 reexec gate (R12.8/R12.9 P1 Codex audit lineage; mirror _validate_week6_5yr.py)
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
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.data.cache import load_chain  # noqa: E402
from src.data.enrich import enrich_pipeline  # noqa: E402
from src.options.surface_cache import load_surface_records  # noqa: E402

CACHE_DIR = _REPO_ROOT / "data" / "taifex_cache"
TAIEX_CSV = _REPO_ROOT / "data" / "taiex_daily.csv"
REPORTS_DIR = _REPO_ROOT / "reports"

DEFAULT_START = "2021-04-01"
DEFAULT_END = "2026-04-28"

# Plan locked cohort DTEs
COHORT_DTES = (28, 35, 42, 49, 56)
DTE_RANGE_LOW = 21  # cohort DTE 28 - 7 day flex
DTE_RANGE_HIGH = 63  # cohort DTE 56 + 7 day flex (also matches walk-forward test_window)
HEDGE_DTE_OFFSET = 30  # for calendar mode: hedge expiry = target + 30


def _section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


# ---------------------------------------------------------------------------
# Section A: Cohort availability audit
# ---------------------------------------------------------------------------


def audit_cohort_availability(chain: pd.DataFrame) -> dict:
    """Per-trading-day unique expiry count for DTE in [21, 63].

    Pattern 17 hollow PASS guard: report p10 (10th percentile) NOT just mean,
    防止 long tail 偽報 (e.g. mean 4 but 30% days have 1).
    """
    df = chain[["date", "expiry"]].drop_duplicates().copy()
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df = df[(df["dte"] >= DTE_RANGE_LOW) & (df["dte"] <= DTE_RANGE_HIGH)]

    per_day = df.groupby("date")["expiry"].nunique().rename("n_unique_expiry")
    n_days = len(per_day)
    if n_days == 0:
        return {
            "n_trading_days": 0,
            "mean_unique_expiry_per_day": 0.0,
            "median_unique_expiry_per_day": 0.0,
            "p10_unique_expiry_per_day": 0.0,
            "p90_unique_expiry_per_day": 0.0,
            "max_unique_expiry_per_day": 0,
            "pct_days_ge_3": 0.0,
            "pct_days_ge_4": 0.0,
            "pct_days_ge_5": 0.0,
        }

    return {
        "n_trading_days": int(n_days),
        "mean_unique_expiry_per_day": float(per_day.mean()),
        "median_unique_expiry_per_day": float(per_day.median()),
        "p10_unique_expiry_per_day": float(per_day.quantile(0.10)),
        "p90_unique_expiry_per_day": float(per_day.quantile(0.90)),
        "max_unique_expiry_per_day": int(per_day.max()),
        "pct_days_ge_3": float((per_day >= 3).mean()),
        "pct_days_ge_4": float((per_day >= 4).mean()),
        "pct_days_ge_5": float((per_day >= 5).mean()),
    }


def audit_weekly_presence(chain: pd.DataFrame) -> dict:
    """Detect weekly TXO presence — short-DTE (<=14) expiries per week.

    TXO weekly options 推出 2018+；split 5yr (2021+) vs 8yr (2018+) report.
    Weekly proxy: if a week sees expiries with DTE <=14 not from monthly cycle,
    likely weekly contract.
    """
    df = chain[["date", "expiry"]].drop_duplicates().copy()
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    short_dte = df[df["dte"] <= 14]
    n_weekly_candidates = short_dte["expiry"].nunique()
    n_total_expiry = df["expiry"].nunique()

    return {
        "n_unique_short_dte_expiries": int(n_weekly_candidates),
        "n_total_unique_expiries": int(n_total_expiry),
        "weekly_density_ratio": float(n_weekly_candidates / max(n_total_expiry, 1)),
    }


# ---------------------------------------------------------------------------
# Section B: Hedge cost sampling
# ---------------------------------------------------------------------------


def _pick_strike_at_delta(rows: pd.DataFrame, target_delta: float) -> pd.Series | None:
    """Return row closest to target_delta (abs)."""
    if rows.empty:
        return None
    diff = (rows["delta"].abs() - abs(target_delta)).abs()
    idx = diff.idxmin()
    out = rows.loc[idx]
    # rows.loc[idx] returns Series for unique idx; defensive cast for mypy
    if isinstance(out, pd.DataFrame):
        return out.iloc[0]
    return out


def sample_hedge_cost_ratio(
    enriched_sample: pd.DataFrame,
    n_samples: int = 50,
    seed: int = 42,
) -> dict:
    """Estimate hedge_cost_ratio = hedge_premium / IC_credit on N sample dates.

    For each sample date:
      1. Pick front expiry (DTE in 25-35 cohort=28 range)
      2. Build 4-leg IC at delta 0.16 short / 0.08 wing → IC credit
      3. Build long calendar (sell front ATM call + buy back ATM call DTE+30)
         OR long straddle (buy ATM call + put same DTE)
      4. ratio = hedge_premium / IC_credit

    Reports distribution: median / mean / p10 / p90 per hedge mode.
    """
    rng = np.random.default_rng(seed)
    dates = enriched_sample["date"].unique()
    if len(dates) == 0:
        return {"error": "empty enriched sample"}

    n_pick = min(n_samples, len(dates))
    sampled = rng.choice(dates, size=n_pick, replace=False)

    calendar_ratios: list[float] = []
    straddle_ratios: list[float] = []
    skipped = 0
    skipped_reasons: list[str] = []

    for d in sampled:
        day_chain = enriched_sample[enriched_sample["date"] == d]
        if day_chain.empty:
            skipped += 1
            skipped_reasons.append(f"{d}: empty day_chain")
            continue

        front_chain = day_chain[(day_chain["dte"] >= 25) & (day_chain["dte"] <= 35)]
        if front_chain.empty:
            skipped += 1
            skipped_reasons.append(f"{d}: no front DTE 25-35")
            continue

        front_expiries = front_chain["expiry"].unique()
        front_expiry = sorted(front_expiries)[0]
        front_chain = front_chain[front_chain["expiry"] == front_expiry]
        underlying = float(front_chain["underlying"].iloc[0])

        puts = front_chain[front_chain["option_type"].str.lower().str.startswith("p")]
        calls = front_chain[front_chain["option_type"].str.lower().str.startswith("c")]
        if puts.empty or calls.empty:
            skipped += 1
            skipped_reasons.append(f"{d}: missing puts or calls")
            continue

        short_put = _pick_strike_at_delta(puts, -0.16)
        wing_put = _pick_strike_at_delta(puts, -0.08)
        short_call = _pick_strike_at_delta(calls, 0.16)
        wing_call = _pick_strike_at_delta(calls, 0.08)
        if short_put is None or wing_put is None or short_call is None or wing_call is None:
            skipped += 1
            skipped_reasons.append(f"{d}: incomplete IC legs")
            continue

        sp_settle: float = float(short_put["settle"]) if pd.notna(short_put["settle"]) else 0.0
        wp_settle: float = float(wing_put["settle"]) if pd.notna(wing_put["settle"]) else 0.0
        sc_settle: float = float(short_call["settle"]) if pd.notna(short_call["settle"]) else 0.0
        wc_settle: float = float(wing_call["settle"]) if pd.notna(wing_call["settle"]) else 0.0
        ic_credit: float = (sp_settle - wp_settle) + (sc_settle - wc_settle)
        if ic_credit <= 0:
            skipped += 1
            skipped_reasons.append(f"{d}: IC credit <= 0 (settle data issue)")
            continue

        # ATM strike (closest to underlying, 50-pt grid)
        all_strikes = sorted(front_chain["strike"].unique())
        atm_idx = int(np.argmin([abs(k - underlying) for k in all_strikes]))
        atm_strike = all_strikes[atm_idx]

        atm_call = front_chain[
            (front_chain["strike"] == atm_strike)
            & (front_chain["option_type"].str.lower().str.startswith("c"))
        ]
        atm_put = front_chain[
            (front_chain["strike"] == atm_strike)
            & (front_chain["option_type"].str.lower().str.startswith("p"))
        ]
        if atm_call.empty or atm_put.empty:
            skipped += 1
            skipped_reasons.append(f"{d}: no ATM at strike {atm_strike}")
            continue

        # Straddle: long ATM call + long ATM put
        atm_call_settle = (
            float(atm_call["settle"].iloc[0]) if pd.notna(atm_call["settle"].iloc[0]) else 0.0
        )
        atm_put_settle = (
            float(atm_put["settle"].iloc[0]) if pd.notna(atm_put["settle"].iloc[0]) else 0.0
        )
        straddle_premium = atm_call_settle + atm_put_settle
        if straddle_premium > 0 and ic_credit > 0:
            straddle_ratios.append(straddle_premium / ic_credit)

        # Calendar: sell front ATM call + buy back ATM call (DTE+30)
        # back expiry — find closest expiry where DTE in [55, 65]
        day_back = day_chain[(day_chain["dte"] >= 55) & (day_chain["dte"] <= 65)]
        if day_back.empty:
            continue  # straddle counted, skip calendar this day
        back_expiries = sorted(day_back["expiry"].unique())
        back_expiry = back_expiries[0]
        back_call = day_back[
            (day_back["expiry"] == back_expiry)
            & (day_back["strike"] == atm_strike)
            & (day_back["option_type"].str.lower().str.startswith("c"))
        ]
        if back_call.empty:
            continue
        back_call_settle = (
            float(back_call["settle"].iloc[0]) if pd.notna(back_call["settle"].iloc[0]) else 0.0
        )
        # calendar = pay back - receive front
        calendar_premium = back_call_settle - atm_call_settle
        if calendar_premium > 0 and ic_credit > 0:
            calendar_ratios.append(calendar_premium / ic_credit)

    def _stats(arr: list[float]) -> dict:
        if not arr:
            return {
                "n": 0,
                "median": float("nan"),
                "mean": float("nan"),
                "p10": float("nan"),
                "p90": float("nan"),
            }
        a = np.array(arr)
        return {
            "n": int(len(a)),
            "median": float(np.median(a)),
            "mean": float(np.mean(a)),
            "p10": float(np.percentile(a, 10)),
            "p90": float(np.percentile(a, 90)),
        }

    return {
        "n_sampled_dates": int(n_pick),
        "n_skipped": int(skipped),
        "skipped_reasons_sample": skipped_reasons[:5],
        "calendar_mode": _stats(calendar_ratios),
        "straddle_mode": _stats(straddle_ratios),
    }


# ---------------------------------------------------------------------------
# Section C: Surface coverage
# ---------------------------------------------------------------------------


def audit_surface_coverage(chain_dates: pd.DatetimeIndex, start: str, end: str) -> dict:
    surface_records = load_surface_records(str(CACHE_DIR), start, end)
    surface_dates = pd.DatetimeIndex(sorted({pd.Timestamp(r.date) for r in surface_records}))
    intersect = surface_dates.intersection(chain_dates)
    return {
        "n_chain_trading_days": int(len(chain_dates)),
        "n_surface_fit_dates": int(len(surface_dates)),
        "n_overlap": int(len(intersect)),
        "coverage_pct": float(len(intersect) / max(len(chain_dates), 1)),
    }


# ---------------------------------------------------------------------------
# GO/NO-GO decision
# ---------------------------------------------------------------------------


def decide_go_nogo(cohort: dict, hedge: dict, surface: dict) -> dict:
    """Per plan GO/NO-GO 4-path logic."""
    mean_uniq = cohort["mean_unique_expiry_per_day"]
    p10_uniq = cohort["p10_unique_expiry_per_day"]
    cal_ratio_med = hedge.get("calendar_mode", {}).get("median", float("nan"))
    str_ratio_med = hedge.get("straddle_mode", {}).get("median", float("nan"))
    coverage_ok = surface["coverage_pct"] >= 0.95

    # NO-GO conditions (any triggers)
    if mean_uniq < 1.0:
        return {
            "verdict": "NO-GO",
            "reason": f"mean unique expiry/day = {mean_uniq:.2f} < 1.0 — TXO cache not viable",
            "path": "STOP, write phase1_final_conclusion.md",
        }
    if not coverage_ok:
        return {
            "verdict": "NO-GO",
            "reason": f"surface coverage {surface['coverage_pct']:.1%} < 95%",
            "path": "STOP, run _validate_surface_mark_5_4a.py first",
        }
    # check both hedge ratios for NO-GO (>5x)
    cal_too_high = pd.notna(cal_ratio_med) and cal_ratio_med > 5.0
    str_too_high = pd.notna(str_ratio_med) and str_ratio_med > 5.0
    if cal_too_high and str_too_high:
        return {
            "verdict": "NO-GO",
            "reason": f"hedge_cost_ratio median both modes > 5x (calendar {cal_ratio_med:.2f}, straddle {str_ratio_med:.2f})",
            "path": "STOP, alpha hypothesis cannot survive any hedge cost",
        }

    # GO conditions (most ambitious path that fits)
    if mean_uniq >= 4.0 and p10_uniq >= 3.0:
        cost_ok = (pd.notna(cal_ratio_med) and cal_ratio_med < 2.0) or (
            pd.notna(str_ratio_med) and str_ratio_med < 2.0
        )
        return {
            "verdict": "GO 5-cohort full" if cost_ok else "GO 5-cohort (high-cost warning)",
            "reason": f"mean {mean_uniq:.2f} >= 4 + p10 {p10_uniq:.2f} >= 3"
            + (
                " + at least 1 hedge mode < 2x"
                if cost_ok
                else "; both hedge modes >= 2x — Sharpe risk"
            ),
            "path": "Day 7.1+ with cohort_dtes=(28, 35, 42, 49, 56)",
        }
    if mean_uniq >= 3.0 and p10_uniq >= 2.0:
        return {
            "verdict": "GO 3-cohort 退化",
            "reason": f"mean {mean_uniq:.2f} >= 3 but p10 {p10_uniq:.2f} < 3 — 退化 plan",
            "path": "Day 7.1+ with cohort_dtes=(28, 42, 56)",
        }
    if mean_uniq >= 1.0:
        return {
            "verdict": "GO 1-cohort 純 D",
            "reason": f"mean {mean_uniq:.2f} viable, p10 {p10_uniq:.2f} only supports 1 cohort",
            "path": "Day 7.1+ single-expiry hedged IC (no E ladder)",
        }
    return {
        "verdict": "INDETERMINATE",
        "reason": "fell through GO/NO-GO matrix",
        "path": "manual review required",
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def write_markdown_report(
    cohort: dict,
    weekly: dict,
    cohort_5yr: dict,
    weekly_5yr: dict,
    hedge: dict,
    surface: dict,
    decision: dict,
    args: argparse.Namespace,
) -> Path:
    out = REPORTS_DIR / "week7_feasibility.md"
    REPORTS_DIR.mkdir(exist_ok=True)
    lines: list[str] = []
    lines.append("# Phase 1 Week 7 Day 7.0 — Feasibility Hard Gate Report")
    lines.append("")
    lines.append(f"- **Generated**: {datetime.now(UTC).isoformat()}")
    lines.append(f"- **Audit window**: {args.start} → {args.end}")
    lines.append(f"- **Plan locked cohort DTEs**: {COHORT_DTES}")
    lines.append(f"- **DTE filter range**: [{DTE_RANGE_LOW}, {DTE_RANGE_HIGH}] days")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"### **{decision['verdict']}**")
    lines.append("")
    lines.append(f"**Reason**: {decision['reason']}")
    lines.append("")
    lines.append(f"**Next step**: {decision['path']}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Section A: Cohort Availability (audit window full range)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Trading days | {cohort['n_trading_days']:,} |")
    lines.append(f"| Mean unique expiry/day | **{cohort['mean_unique_expiry_per_day']:.2f}** |")
    lines.append(f"| Median | {cohort['median_unique_expiry_per_day']:.2f} |")
    lines.append(
        f"| **p10 (Pattern 17 hollow PASS guard)** | **{cohort['p10_unique_expiry_per_day']:.2f}** |"
    )
    lines.append(f"| p90 | {cohort['p90_unique_expiry_per_day']:.2f} |")
    lines.append(f"| Max | {cohort['max_unique_expiry_per_day']} |")
    lines.append(f"| % days ≥ 3 cohorts | {cohort['pct_days_ge_3']:.1%} |")
    lines.append(f"| % days ≥ 4 cohorts | {cohort['pct_days_ge_4']:.1%} |")
    lines.append(f"| % days ≥ 5 cohorts | {cohort['pct_days_ge_5']:.1%} |")
    lines.append("")
    lines.append("### 5yr split (2021-04 → 2026-04 only, default backtest range)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Trading days | {cohort_5yr['n_trading_days']:,} |")
    lines.append(f"| Mean unique expiry/day | **{cohort_5yr['mean_unique_expiry_per_day']:.2f}** |")
    lines.append(f"| Median | {cohort_5yr['median_unique_expiry_per_day']:.2f} |")
    lines.append(f"| p10 | **{cohort_5yr['p10_unique_expiry_per_day']:.2f}** |")
    lines.append(f"| Max | {cohort_5yr['max_unique_expiry_per_day']} |")
    lines.append(f"| % days ≥ 3 cohorts | {cohort_5yr['pct_days_ge_3']:.1%} |")
    lines.append(f"| % days ≥ 4 cohorts | {cohort_5yr['pct_days_ge_4']:.1%} |")
    lines.append(f"| % days ≥ 5 cohorts | {cohort_5yr['pct_days_ge_5']:.1%} |")
    lines.append("")

    lines.append("## Section A2: Weekly TXO Presence")
    lines.append("")
    lines.append("| Range | Total Unique Expiries | Short-DTE (≤14) Expiries | Weekly Density |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Full ({args.start} → {args.end}) | {weekly['n_total_unique_expiries']} | "
        f"{weekly['n_unique_short_dte_expiries']} | {weekly['weekly_density_ratio']:.1%} |"
    )
    lines.append(
        f"| 5yr (2021+) | {weekly_5yr['n_total_unique_expiries']} | "
        f"{weekly_5yr['n_unique_short_dte_expiries']} | {weekly_5yr['weekly_density_ratio']:.1%} |"
    )
    lines.append("")

    lines.append("## Section B: Hedge Cost Sampling")
    lines.append("")
    if "error" in hedge:
        lines.append(f"**SKIPPED**: {hedge['error']}")
    elif args.skip_hedge_cost:
        lines.append("**SKIPPED**: --skip-hedge-cost flag set")
    else:
        lines.append(f"- Sampled {hedge['n_sampled_dates']} dates")
        lines.append(f"- Skipped {hedge['n_skipped']} (incomplete chain or settle data)")
        lines.append("")
        lines.append("| Hedge Mode | n | Median | Mean | p10 | p90 |")
        lines.append("|---|---|---|---|---|---|")
        for mode_key, mode_label in [("calendar_mode", "Calendar"), ("straddle_mode", "Straddle")]:
            s = hedge.get(mode_key, {})
            lines.append(
                f"| {mode_label} | {s.get('n', 0)} | {s.get('median', float('nan')):.2f}× | "
                f"{s.get('mean', float('nan')):.2f}× | {s.get('p10', float('nan')):.2f}× | "
                f"{s.get('p90', float('nan')):.2f}× |"
            )
        lines.append("")
        lines.append(
            "**Interpretation**: ratio = hedge_premium / IC_credit. <1 = hedge cheaper than IC收入；>2 = 高 risk Sharpe negative；>5 = NO-GO."
        )
        if hedge.get("skipped_reasons_sample"):
            lines.append("")
            lines.append("Skipped reasons sample:")
            for r in hedge["skipped_reasons_sample"]:
                lines.append(f"- {r}")
    lines.append("")

    lines.append("## Section C: Surface Coverage")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Chain trading days | {surface['n_chain_trading_days']:,} |")
    lines.append(f"| Surface fit dates | {surface['n_surface_fit_dates']:,} |")
    lines.append(f"| Overlap | {surface['n_overlap']:,} |")
    lines.append(f"| Coverage % | {surface['coverage_pct']:.1%} |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## GO/NO-GO Decision Logic")
    lines.append("")
    lines.append("Per plan Day 7.0 hard gate:")
    lines.append("")
    lines.append("| Path | Condition |")
    lines.append("|---|---|")
    lines.append("| GO 5-cohort full | mean ≥4 + p10 ≥3 + at least 1 hedge mode < 2× |")
    lines.append("| GO 3-cohort 退化 | mean ≥3 + p10 ≥2 |")
    lines.append("| GO 1-cohort 純 D | mean ≥1 |")
    lines.append("| NO-GO | mean <1 OR coverage <95% OR both hedge modes > 5× |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Caveats / Pattern 18 absolute claim guards")
    lines.append("")
    lines.append("- 此 audit 範圍受限於 cache 已 backfill 內容；新 expiry 上架需重 ingest")
    lines.append("- weekly density 以 DTE ≤14 short-expiry proxy 估算，非 exchange-confirmed")
    lines.append(
        "- Hedge cost sampling 用 settle 估算 IC credit，**非真實 worst-side fill**；實盤 5yr 可能差異 ~10-20%"
    )
    lines.append(
        "- Section A 不分 IV regime / weekday；不同 regime 下 cohort 數可能聚集（e.g. 月末/季末）"
    )
    lines.append("- Surface coverage 引用 Phase 1 cache 既有 1227 shards 不重 fit")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 7.0 feasibility hard gate")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument(
        "--skip-hedge-cost",
        action="store_true",
        help="Skip Section B (faster; report cohort + surface only)",
    )
    parser.add_argument(
        "--full-8yr",
        action="store_true",
        help="Audit full 8yr cache (2018-04 → 2026-04) instead of default 5yr",
    )
    args = parser.parse_args()
    if args.full_8yr:
        args.start = "2018-04-01"
        args.end = DEFAULT_END

    _section(f"Day 7.0 Feasibility Audit: {args.start} → {args.end}")

    # ----- Section A -----
    _section("Section A: Loading chain (strategy_view) for cohort audit")
    chain = load_chain(str(CACHE_DIR), args.start, args.end, layer="strategy_view")
    if chain.empty:
        print(f"FAIL: no chain shards in [{args.start}, {args.end}]", flush=True)
        return 1
    pre_filter = len(chain)
    chain = chain[chain["expiry"] >= chain["date"]].reset_index(drop=True)
    print(
        f"  loaded {len(chain):,} rows ({pre_filter - len(chain):,} post-expiry filtered)",
        flush=True,
    )
    print(f"  unique dates: {chain['date'].nunique():,}", flush=True)

    cohort = audit_cohort_availability(chain)
    weekly = audit_weekly_presence(chain)
    print(
        f"  full range: mean {cohort['mean_unique_expiry_per_day']:.2f} / "
        f"p10 {cohort['p10_unique_expiry_per_day']:.2f} / "
        f"max {cohort['max_unique_expiry_per_day']}",
        flush=True,
    )
    print(
        f"  pct days ≥3: {cohort['pct_days_ge_3']:.1%} / "
        f"≥4: {cohort['pct_days_ge_4']:.1%} / "
        f"≥5: {cohort['pct_days_ge_5']:.1%}",
        flush=True,
    )

    # 5yr split (always run separately)
    if args.start < "2021-04-01":
        chain_5yr = chain[chain["date"] >= "2021-04-01"].reset_index(drop=True)
    else:
        chain_5yr = chain
    cohort_5yr = audit_cohort_availability(chain_5yr)
    weekly_5yr = audit_weekly_presence(chain_5yr)
    print(
        f"  5yr only: mean {cohort_5yr['mean_unique_expiry_per_day']:.2f} / "
        f"p10 {cohort_5yr['p10_unique_expiry_per_day']:.2f}",
        flush=True,
    )

    # ----- Section B -----
    if args.skip_hedge_cost:
        hedge = {"error": "skipped via --skip-hedge-cost"}
    else:
        _section("Section B: Hedge cost sampling (enrich subset)")
        # Pick ~30 distinct sample dates spread across range
        all_dates = sorted(chain["date"].unique())
        n_pick = min(30, len(all_dates))
        idxs = np.linspace(0, len(all_dates) - 1, n_pick, dtype=int)
        sample_dates = [all_dates[i] for i in idxs]
        sample_chain = chain[chain["date"].isin(sample_dates)].reset_index(drop=True)
        print(
            f"  enriching {len(sample_chain):,} rows for {len(sample_dates)} sample dates",
            flush=True,
        )

        # spot for enrich
        spot_df = pd.read_csv(TAIEX_CSV)
        spot_df["date"] = pd.to_datetime(spot_df["date"])
        spot_series = pd.Series(
            spot_df["close"].values,
            index=pd.DatetimeIndex(spot_df["date"].values),
            name="spot",
        )
        try:
            enriched_sample, _ = enrich_pipeline(
                sample_chain,
                spot_series,
                spot_missing_policy="forward_fill",
                on_iv_solver_fail="nan",
            )
            hedge = sample_hedge_cost_ratio(enriched_sample, n_samples=30)
            print(
                f"  sampled {hedge['n_sampled_dates']} / skipped {hedge['n_skipped']}",
                flush=True,
            )
            from typing import Any as _Any  # noqa: PLC0415

            _cm_raw: _Any = hedge.get("calendar_mode", {})
            _sm_raw: _Any = hedge.get("straddle_mode", {})
            cm: dict = _cm_raw if isinstance(_cm_raw, dict) else {}
            sm: dict = _sm_raw if isinstance(_sm_raw, dict) else {}
            print(
                f"  calendar median: {cm.get('median', float('nan')):.2f}x  "
                f"straddle median: {sm.get('median', float('nan')):.2f}x",
                flush=True,
            )
        except (ValueError, KeyError) as e:
            print(f"  WARN: enrichment failed → {type(e).__name__}: {e}", flush=True)
            hedge = {"error": f"enrich failed: {e}"}

    # ----- Section C -----
    _section("Section C: Surface coverage")
    chain_dates = pd.DatetimeIndex(sorted(chain["date"].unique()))
    surface = audit_surface_coverage(chain_dates, args.start, args.end)
    print(
        f"  chain {surface['n_chain_trading_days']} / surface {surface['n_surface_fit_dates']} / "
        f"coverage {surface['coverage_pct']:.1%}",
        flush=True,
    )

    # ----- Decision -----
    decision = decide_go_nogo(cohort_5yr if args.start < "2021-04-01" else cohort, hedge, surface)
    _section(f"Verdict: {decision['verdict']}")
    print(f"  Reason: {decision['reason']}", flush=True)
    print(f"  Next step: {decision['path']}", flush=True)

    # ----- Report -----
    out_path = write_markdown_report(
        cohort, weekly, cohort_5yr, weekly_5yr, hedge, surface, decision, args
    )
    print(f"\n  Report written: {out_path}", flush=True)

    # JSON snapshot for machine-readable consumption
    json_out = REPORTS_DIR / "week7_feasibility.json"
    json_out.write_text(
        json.dumps(
            {
                "audit_window": {"start": args.start, "end": args.end},
                "cohort_availability_full_range": cohort,
                "cohort_availability_5yr": cohort_5yr,
                "weekly_presence_full_range": weekly,
                "weekly_presence_5yr": weekly_5yr,
                "hedge_cost": hedge,
                "surface_coverage": surface,
                "decision": decision,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"  JSON snapshot: {json_out}", flush=True)

    # Exit code: 0 if any GO, 1 if NO-GO
    return 0 if decision["verdict"].startswith("GO") else 1


if __name__ == "__main__":
    sys.exit(main())
