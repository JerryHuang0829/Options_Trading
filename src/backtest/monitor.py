"""Monitor metric wrappers (Week 6 Day 6.3).

R11.17 Codex 留 6 項 monitor metric wrapper + Plan §3 Day 6.3 ablation 紀律:
真 backtest 報告必列 mark_audit-derived metric + GatedIC reject reason 統計
+ 跨 scenario PnL diff，避免 hollow PASS（log 漂亮但策略沒 alpha / surface
fallback 從未被觸發）。

3 個 entry:
  - summarise_mark_audit(mark_audit_df) → dict (5 metric):
      n_legs_marked_total / n_fallback_settle_total / n_fallback_surface_total
      / fallback_days_count / fallback_legs_ratio / avg_fallback_rate
  - summarise_rejected_reasons(strategy) → DataFrame:
      GatedIC.get_rejected_reasons() pass-through wrapper（保留 helper 名稱
      讓 caller 不直接 import GatedIC type；duck-type only）
  - summarise_scenario_pnl_divergence(results: dict[str, pd.Series]) → dict:
      跨 scenario daily_pnl 真分歧偵測 — Pro 紀律「scenario A/B/C 結果若
      identical 則 mark_policy 抽換沒功能價值」(R11.21 hollow PASS pattern)

Pattern 14 producer/consumer parity:
  engine.run_backtest 產 mark_audit 4-col schema (fallback_rate /
  n_legs_marked / n_fallback_settle / n_fallback_surface) — monitor 嚴格
  對齊；schema drift 改 engine 須同步改此 file。

Pattern 17 Hollow PASS detector:
  ratio 算法分母 0 guard 必加 NaN sentinel，不可 silent 0；caller 看 NaN
  才知 measured path 從未打到（vs. 真打到但 0 fallback）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from typing import Any


def summarise_mark_audit(mark_audit: pd.DataFrame) -> dict[str, float]:
    """Aggregate engine.run_backtest mark_audit DataFrame into 6 monitor metrics.

    Args:
        mark_audit: 4-col DataFrame indexed by date, columns
            (fallback_rate, n_legs_marked, n_fallback_settle, n_fallback_surface)
            — engine R10.12+Day 5.3 schema.

    Returns:
        dict with:
            n_days_observed: int (df row count)
            n_legs_marked_total: sum n_legs_marked
            n_fallback_settle_total: sum n_fallback_settle
            n_fallback_surface_total: sum n_fallback_surface
            fallback_days_count: rows where (n_fallback_settle + n_fallback_surface) > 0
            fallback_legs_ratio: (settle+surface) / n_legs_marked_total (NaN if denom=0)
            avg_fallback_rate: mean of fallback_rate column (NaN if empty)
    """
    # R12.5 P fix (Codex audit): n_fallback_settle_3rd is NEW required column.
    # Backward-compat: if absent (older mark_audit data), default to 0.
    expected_cols = {"fallback_rate", "n_legs_marked", "n_fallback_settle", "n_fallback_surface"}
    if mark_audit is None or mark_audit.empty:
        return {
            "n_days_observed": 0.0,
            "n_legs_marked_total": 0.0,
            "n_fallback_settle_total": 0.0,
            "n_fallback_surface_total": 0.0,
            "n_fallback_settle_3rd_total": 0.0,
            "fallback_days_count": 0.0,
            "fallback_legs_ratio": float("nan"),
            "settle_3rd_fallback_ratio": float("nan"),
            "avg_fallback_rate": float("nan"),
        }
    missing = expected_cols - set(mark_audit.columns)
    if missing:
        raise ValueError(
            f"summarise_mark_audit: mark_audit missing columns {sorted(missing)}; "
            f"got {sorted(mark_audit.columns)} — schema drift vs engine.run_backtest "
            "(R10.12 + Day 5.3 + R12.5)"
        )

    n_legs_total = float(mark_audit["n_legs_marked"].sum())
    n_settle_total = float(mark_audit["n_fallback_settle"].sum())
    n_surface_total = float(mark_audit["n_fallback_surface"].sum())
    # R12.5 P fix: optional column for backward-compat with pre-R12.5 audit data
    n_settle_3rd_total = (
        float(mark_audit["n_fallback_settle_3rd"].sum())
        if "n_fallback_settle_3rd" in mark_audit.columns
        else 0.0
    )
    n_fallback_total = n_settle_total + n_surface_total
    fallback_day_mask = (mark_audit["n_fallback_settle"] + mark_audit["n_fallback_surface"]) > 0
    fallback_days = float(int(fallback_day_mask.sum()))

    fallback_legs_ratio = n_fallback_total / n_legs_total if n_legs_total > 0 else float("nan")
    settle_3rd_ratio = n_settle_3rd_total / n_legs_total if n_legs_total > 0 else float("nan")
    avg_fallback_rate = (
        float(mark_audit["fallback_rate"].mean()) if not mark_audit.empty else float("nan")
    )

    return {
        "n_days_observed": float(len(mark_audit)),
        "n_legs_marked_total": n_legs_total,
        "n_fallback_settle_total": n_settle_total,
        "n_fallback_surface_total": n_surface_total,
        "n_fallback_settle_3rd_total": n_settle_3rd_total,
        "fallback_days_count": fallback_days,
        "fallback_legs_ratio": fallback_legs_ratio,
        "settle_3rd_fallback_ratio": settle_3rd_ratio,
        "avg_fallback_rate": avg_fallback_rate,
    }


def summarise_rejected_reasons(strategy: Any) -> pd.DataFrame:
    """Pass-through GatedIronCondor.get_rejected_reasons() with empty fallback.

    Args:
        strategy: any object with .get_rejected_reasons() → DataFrame method
            (GatedIronCondor / GatedVerticalStrategy / RegimeWrappedStrategy
            wrapping either). RegimeWrappedStrategy delegates to base.

    Returns:
        DataFrame columns (date, path, reason, leg). Empty if strategy has
        no get_rejected_reasons or accumulator empty.
    """
    getter = getattr(strategy, "get_rejected_reasons", None)
    if getter is None:
        # base wrapping: inspect inner strategy for delegate
        inner = getattr(strategy, "base_strategy", None)
        getter = getattr(inner, "get_rejected_reasons", None) if inner is not None else None
    if getter is None:
        return pd.DataFrame(columns=["date", "path", "reason", "leg"])
    df = getter()
    if df is None:
        return pd.DataFrame(columns=["date", "path", "reason", "leg"])
    return df


def summarise_scenario_pnl_divergence(
    results: dict[str, pd.Series],
) -> dict[str, float]:
    """Pairwise daily_pnl divergence across named scenarios.

    Pro 紀律: 若 mark_policy A vs B 跑出 identical daily_pnl → policy 抽換
    沒功能價值（Codex R11.17/R11.21 hollow PASS — surface fallback 從未
    被觸發）。本 helper 算 pair-wise sum(|diff|) 給 scenario report 用。

    Args:
        results: {scenario_name: daily_pnl pd.Series indexed by date}

    Returns:
        dict {f"{a}_vs_{b}_abs_diff_sum": float, ...} for each unordered pair.
        Empty dict if < 2 scenarios.
    """
    if len(results) < 2:
        return {}
    out: dict[str, float] = {}
    names = sorted(results.keys())
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            sa = results[a]
            sb = results[b]
            # Align on intersection of dates; missing → drop pair (not 0-fill)
            aligned = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner")
            if aligned.empty:
                out[f"{a}_vs_{b}_abs_diff_sum"] = float("nan")
                out[f"{a}_vs_{b}_n_aligned_days"] = 0.0
                continue
            diff = (aligned["a"] - aligned["b"]).abs().sum()
            out[f"{a}_vs_{b}_abs_diff_sum"] = float(diff)
            out[f"{a}_vs_{b}_n_aligned_days"] = float(len(aligned))
    return out


def _format_summary_table(metrics: dict[str, float]) -> str:
    """Format metrics dict as left-aligned table for console / markdown report."""
    if not metrics:
        return "(empty)"
    width = max(len(k) for k in metrics)
    lines = []
    for k, v in metrics.items():
        v_str = f"{v:.6g}" if not np.isnan(v) else "NaN"
        lines.append(f"{k.ljust(width)}  {v_str}")
    return "\n".join(lines)
