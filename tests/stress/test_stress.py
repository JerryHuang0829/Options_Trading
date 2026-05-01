"""Tests for scripts/stress_test.py — Week 2 Day 6.

Validates the stress framework on synthetic data:
  - IV crush: IC (short vega) profits.
  - IV expand: IC (short vega) loses.
  - spot gap through short strike: large loss; should hit stop in extreme cases.
  - Output table has 4 rows with correct columns.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Bootstrap: scripts/ is not on sys.path by default.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.stress_test import (  # noqa: E402
    SCENARIOS,
    StressScenario,
    _open_ic_at_date,
    run_stress_test,
    shock_chain,
)
from src.backtest.execution import WorstSideFillModel  # noqa: E402


def test_stress_test_outputs_4_scenarios() -> None:
    """run_stress_test produces a 4-row DataFrame with required columns."""
    result = run_stress_test()
    table = result["stress_table"]
    assert isinstance(table, pd.DataFrame)
    assert len(table) == 4
    required_cols = {
        "scenario",
        "sigma_shock",
        "spot_shock",
        "pnl_twd",
        "pnl_pct_of_max_risk",
        "delta_change",
        "vega_change",
        "theta_change",
        "hit_stop_loss",
    }
    assert required_cols.issubset(set(table.columns))
    # Scenario names must match the canonical 4.
    assert set(table["scenario"]) == {s.name for s in SCENARIOS}


def test_iv_crush_short_premium_profits() -> None:
    """IC is short vega; -30% IV crush should produce positive PnL."""
    result = run_stress_test()
    crush = result["stress_table"][result["stress_table"]["scenario"] == "iv_crush"].iloc[0]
    assert crush["pnl_twd"] > 0, f"iv_crush should profit; got {crush['pnl_twd']}"


def test_iv_expand_short_premium_loses() -> None:
    """IC is short vega; +50% IV expand should produce negative PnL."""
    result = run_stress_test()
    expand = result["stress_table"][result["stress_table"]["scenario"] == "iv_expand"].iloc[0]
    assert expand["pnl_twd"] < 0, f"iv_expand should lose; got {expand['pnl_twd']}"


def test_spot_gap_through_short_strike_large_loss() -> None:
    """spot_gap_dn (-7%) should lose more than iv_expand (no spot move)."""
    result = run_stress_test()
    table = result["stress_table"]
    expand = table[table["scenario"] == "iv_expand"].iloc[0]
    gap_dn = table[table["scenario"] == "spot_gap_dn"].iloc[0]
    assert gap_dn["pnl_twd"] < expand["pnl_twd"], (
        f"spot_gap_dn ({gap_dn['pnl_twd']}) should lose more than iv_expand ({expand['pnl_twd']})"
    )


def test_shock_chain_increases_iv() -> None:
    """sigma_shock=+0.5 should multiply iv column by 1.5."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(SyntheticChainConfig(start_date="2026-01-01", end_date="2026-02-15"))
    today = chain[chain["date"] == chain["date"].unique()[10]].copy()
    scenario = StressScenario(name="test_iv_up", sigma_shock=+0.5, spot_shock=0.0)
    shocked = shock_chain(today, scenario)
    # Every row's iv should be ~1.5x the original (within float epsilon).
    ratio = (shocked["iv"] / today["iv"]).mean()
    assert ratio == pytest.approx(1.5, rel=1e-9)


def test_shock_chain_spot_shock_changes_underlying() -> None:
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(SyntheticChainConfig(start_date="2026-01-01", end_date="2026-02-15"))
    today = chain[chain["date"] == chain["date"].unique()[10]].copy()
    scenario = StressScenario(name="test_spot_up", sigma_shock=0.0, spot_shock=+0.05)
    shocked = shock_chain(today, scenario)
    ratio = (shocked["underlying"] / today["underlying"]).iloc[0]
    assert ratio == pytest.approx(1.05, rel=1e-9)


def test_shock_chain_multi_date_raises() -> None:
    """R5 P2: shock_chain on multi-date chain must raise (no silent misprice)."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(SyntheticChainConfig(start_date="2026-01-05", end_date="2026-01-09"))
    # Two-day slice → must raise.
    two_days = chain[chain["date"].isin(chain["date"].unique()[:2])].copy()
    scenario = StressScenario(name="x", sigma_shock=0.0, spot_shock=0.0)
    with pytest.raises(ValueError, match="single-date"):
        shock_chain(two_days, scenario)


def test_open_ic_returns_4_legs() -> None:
    """_open_ic_at_date opens a 4-leg position with proper signs."""
    from src.data.synthetic import SyntheticChainConfig, generate_chain

    chain = generate_chain(SyntheticChainConfig(start_date="2026-01-01", end_date="2026-03-31"))
    # Use mid-month entry so next-month 3rd-Wed expiry sits in 21-45 DTE band.
    today = chain[chain["date"] == chain["date"].unique()[10]].copy()
    portfolio, meta = _open_ic_at_date(today, WorstSideFillModel())
    assert len(portfolio.positions) == 1
    legs = portfolio.positions[0].legs
    assert len(legs) == 4
    # 2 short (qty<0) + 2 long (qty>0).
    n_short = sum(1 for leg in legs if leg.qty < 0)
    n_long = sum(1 for leg in legs if leg.qty > 0)
    assert n_short == 2 and n_long == 2
    assert meta["entry_credit_mid"] > 0
    assert meta["max_defined_risk_twd"] > 0
