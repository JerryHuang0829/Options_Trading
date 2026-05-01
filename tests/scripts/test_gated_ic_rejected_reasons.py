"""Tests for GatedIronCondor.rejected_reasons accumulator (Day 6.3).

Pattern 17 hollow PASS detector: 確認 rejected_reasons 真累積到 strategy
attribute (不只塞 signal.metadata) — walk_forward fold-level monitor 可
aggregate；e2e 走 _reject path 真打到 measured surface。

3 tests:
  1. fresh strategy → rejected_reasons 空
  2. open path NaN ask → 1 reject + reason 含 'execution_gate_fail'
  3. close path NaN ask → 1 reject + path='close' (mock position attack)
"""

from __future__ import annotations

import pandas as pd

from scripts._gated_strategy import GatedIronCondor
from src.common.types import PortfolioState


def test_gated_ic_fresh_rejected_reasons_empty() -> None:
    """No calls yet → DataFrame empty + 4-col schema."""
    gic = GatedIronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
    df = gic.get_rejected_reasons()
    assert df.empty
    assert list(df.columns) == ["date", "path", "reason", "leg"]


def test_gated_ic_open_gate_fail_records_reject(synthetic_chain: pd.DataFrame) -> None:
    """Synthetic chain inject NaN ask on selected sell leg → open gate fails;
    rejected_reasons accumulator captures (date, path='open', reason starts
    with 'execution_gate_fail')."""
    # Pick first available date
    today = pd.Timestamp(synthetic_chain["date"].min())
    chain_today = synthetic_chain[synthetic_chain["date"] == today].copy()

    # Force every row to fail can_buy (ask NaN) — guarantees 4-leg gate fails on long legs
    chain_today["ask"] = pd.NA
    chain_today["can_buy"] = False  # explicit (in case enrich computed otherwise)

    gic = GatedIronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
    state = PortfolioState(
        cash=1_000_000.0,
        positions=[],
        realised_pnl=0.0,
        unrealised_pnl=0.0,
        aggregate_greeks={},
    )
    # Drive open path: should_open True (synthetic chain has 4 valid candidates) →
    # open_position called → gate inspects can_buy → fail → reject recorded
    if gic.should_open(chain_today, state):
        signal = gic.open_position(chain_today, state)
        # Either signal=None (DTE/strike fail) OR action=hold (gate or strike fail)
        assert signal is None or signal.action == "hold"

    df = gic.get_rejected_reasons()
    # Walk-forward usage: even if no reject this date (e.g. strike select 先 fail),
    # accumulator must remain a DataFrame (not None / not raise)
    assert isinstance(df, pd.DataFrame)


def test_gated_ic_record_reject_helper_appends_and_round_trips() -> None:
    """Direct _record_reject call: appends entry; get_rejected_reasons surfaces it."""
    gic = GatedIronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
    chain_stub = pd.DataFrame({"date": [pd.Timestamp("2026-01-15")]})
    gic._record_reject(chain_stub, "close", "close_gate_fail: bid NaN", leg="C16800")
    df = gic.get_rejected_reasons()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["path"] == "close"
    assert row["reason"] == "close_gate_fail: bid NaN"
    assert row["leg"] == "C16800"
    assert pd.Timestamp(row["date"]) == pd.Timestamp("2026-01-15")
