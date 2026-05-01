"""Tests for src/backtest/monitor.py — Week 6 Day 6.3.

7 tests covering 6 monitor metrics + reject reasons + scenario divergence:
  1. summarise_mark_audit empty → 0/NaN sentinels
  2. summarise_mark_audit schema_drift → raise (missing column)
  3. summarise_mark_audit known values → ≥3-number manual verification
  4. summarise_mark_audit mutation: row n_fallback_surface 0→1 → ratio changes
  5. summarise_rejected_reasons missing getter → empty DataFrame
  6. summarise_scenario_pnl_divergence: identical vs distinct scenarios
  7. summarise_scenario_pnl_divergence: <2 scenarios → empty dict
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.monitor import (
    summarise_mark_audit,
    summarise_rejected_reasons,
    summarise_scenario_pnl_divergence,
)

# ---------------------------------------------------------------------------
# summarise_mark_audit
# ---------------------------------------------------------------------------


def test_summarise_mark_audit_empty_returns_sentinels() -> None:
    """Empty DataFrame → 0 totals + NaN ratios (Pattern 17 hollow PASS detector)."""
    df = pd.DataFrame(
        columns=["fallback_rate", "n_legs_marked", "n_fallback_settle", "n_fallback_surface"]
    )
    out = summarise_mark_audit(df)
    assert out["n_days_observed"] == 0.0
    assert out["n_legs_marked_total"] == 0.0
    assert out["n_fallback_surface_total"] == 0.0
    assert out["fallback_days_count"] == 0.0
    assert np.isnan(out["fallback_legs_ratio"])
    assert np.isnan(out["avg_fallback_rate"])


def test_summarise_mark_audit_schema_drift_raises() -> None:
    df = pd.DataFrame(
        {
            "fallback_rate": [0.0],
            "n_legs_marked": [4],
            # missing n_fallback_settle / n_fallback_surface
        }
    )
    with pytest.raises(ValueError, match="missing columns"):
        summarise_mark_audit(df)


def test_summarise_mark_audit_known_values() -> None:
    """Manual ≥3-number verification (Pattern 12).

    3 days, 4 legs each = 12 total marked legs.
    Day 0: 0 fallback (rate 0/4 = 0.0)
    Day 1: 1 settle + 0 surface = 1/4 = 0.25
    Day 2: 0 settle + 2 surface = 2/4 = 0.50
    Total fallback legs = 1 + 2 = 3; ratio = 3/12 = 0.25
    fallback_days_count = 2 (day 1 and 2)
    avg_fallback_rate = (0.0 + 0.25 + 0.50) / 3 = 0.25
    """
    df = pd.DataFrame(
        {
            "fallback_rate": [0.0, 0.25, 0.50],
            "n_legs_marked": [4, 4, 4],
            "n_fallback_settle": [0, 1, 0],
            "n_fallback_surface": [0, 0, 2],
        }
    )
    out = summarise_mark_audit(df)
    assert out["n_days_observed"] == 3.0
    assert out["n_legs_marked_total"] == 12.0
    assert out["n_fallback_settle_total"] == 1.0
    assert out["n_fallback_surface_total"] == 2.0
    assert out["fallback_days_count"] == 2.0
    np.testing.assert_allclose(out["fallback_legs_ratio"], 0.25, rtol=1e-9)
    np.testing.assert_allclose(out["avg_fallback_rate"], 0.25, rtol=1e-9)


def test_summarise_mark_audit_mutation_changes_ratio() -> None:
    """Mutation (Pattern 11): change one row's surface count → ratio must change."""
    base = pd.DataFrame(
        {
            "fallback_rate": [0.0, 0.0],
            "n_legs_marked": [4, 4],
            "n_fallback_settle": [0, 0],
            "n_fallback_surface": [0, 0],
        }
    )
    base_out = summarise_mark_audit(base)
    assert base_out["fallback_legs_ratio"] == 0.0  # fully zero

    mutated = base.copy()
    mutated.loc[0, "n_fallback_surface"] = 2
    mut_out = summarise_mark_audit(mutated)
    assert mut_out["fallback_legs_ratio"] > 0.0  # 2/8 = 0.25
    assert mut_out["n_fallback_surface_total"] == 2.0


# ---------------------------------------------------------------------------
# summarise_rejected_reasons
# ---------------------------------------------------------------------------


def test_summarise_rejected_reasons_no_getter_returns_empty() -> None:
    """Strategy without get_rejected_reasons → empty DataFrame (no raise)."""

    class _Bare:
        pass

    df = summarise_rejected_reasons(_Bare())
    assert df.empty
    assert list(df.columns) == ["date", "path", "reason", "leg"]


def test_summarise_rejected_reasons_pass_through() -> None:
    """Strategy with getter → its DataFrame is returned."""

    class _Spy:
        def get_rejected_reasons(self) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "date": pd.Timestamp("2026-01-01"),
                        "path": "open",
                        "reason": "execution_gate_fail",
                        "leg": "TXOC",
                    }
                ]
            )

    df = summarise_rejected_reasons(_Spy())
    assert len(df) == 1
    assert df.iloc[0]["reason"] == "execution_gate_fail"


# ---------------------------------------------------------------------------
# summarise_scenario_pnl_divergence
# ---------------------------------------------------------------------------


def test_scenario_divergence_under_two_scenarios_empty_dict() -> None:
    s = pd.Series([1.0, 2.0])
    assert summarise_scenario_pnl_divergence({"only": s}) == {}


def test_scenario_divergence_identical_vs_distinct() -> None:
    """Identical scenarios → diff_sum = 0; distinct → diff_sum > 0."""
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    a = pd.Series([1.0, 2.0, 3.0], index=idx)
    b = pd.Series([1.0, 2.0, 3.0], index=idx)  # identical to a
    c = pd.Series([1.5, 2.5, 4.0], index=idx)  # distinct

    out = summarise_scenario_pnl_divergence({"a": a, "b": b, "c": c})
    # Names sorted: a, b, c → pairs (a,b), (a,c), (b,c)
    assert out["a_vs_b_abs_diff_sum"] == 0.0
    np.testing.assert_allclose(out["a_vs_c_abs_diff_sum"], 0.5 + 0.5 + 1.0, rtol=1e-9)
    np.testing.assert_allclose(out["b_vs_c_abs_diff_sum"], 0.5 + 0.5 + 1.0, rtol=1e-9)
    assert out["a_vs_b_n_aligned_days"] == 3.0


# ---------------------------------------------------------------------------
# R12.5 P fix (Codex audit): settle_3rd_fallback separate metric
# ---------------------------------------------------------------------------


def test_summarise_mark_audit_r12_5_settle_3rd_separate_metric() -> None:
    """R12.5 P fix: n_fallback_settle_3rd reported separately from n_fallback_settle.

    Codex R12.4 反證: 既有 n_fallback_settle 計數同時含 (a) direct settle policy
    settle_fallback (b) surface degraded to settle (settle_3rd_fallback). Two
    semantically distinct paths混用 → caller cannot distinguish.

    R12.5 fix: monitor returns separate `n_fallback_settle_3rd_total` +
    `settle_3rd_fallback_ratio`. Backward-compat: missing column → 0.
    """
    df = pd.DataFrame(
        {
            "fallback_rate": [0.25, 0.50],
            "n_legs_marked": [4, 4],
            "n_fallback_settle": [1, 2],  # includes settle_3rd
            "n_fallback_surface": [0, 0],
            "n_fallback_settle_3rd": [1, 2],  # all settle came via 3rd-layer route
        }
    )
    out = summarise_mark_audit(df)
    assert out["n_fallback_settle_total"] == 3.0
    assert out["n_fallback_settle_3rd_total"] == 3.0  # NEW R12.5 metric
    np.testing.assert_allclose(out["settle_3rd_fallback_ratio"], 3.0 / 8.0, rtol=1e-9)


def test_summarise_mark_audit_r12_5_backward_compat_no_3rd_col() -> None:
    """R12.5 P fix backward-compat: pre-R12.5 mark_audit (no n_fallback_settle_3rd col)
    → metric defaults to 0 (not raise schema_drift)."""
    df = pd.DataFrame(
        {
            "fallback_rate": [0.0],
            "n_legs_marked": [4],
            "n_fallback_settle": [0],
            "n_fallback_surface": [0],
        }
    )
    out = summarise_mark_audit(df)
    assert out["n_fallback_settle_3rd_total"] == 0.0
    assert np.isnan(out["settle_3rd_fallback_ratio"]) or out["settle_3rd_fallback_ratio"] == 0.0
