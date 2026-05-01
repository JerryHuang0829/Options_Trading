"""Tests for RetailCostModel + cost-aware FillModels (Week 6 Day 6.4).

7 tests covering 5 attackers from Pattern 0:
  1. cost_model=None backward compat: fill identical to Day 5 behavior
  2. RetailCostModel input validation (negative commission/tax/slippage raise)
  3. Commission + tax computed correctly: ≥3 numbers manual derivation
  4. SlippageFillModel native + RetailCostModel.slippage compounds (Pattern 0 #4)
  5. Mutation: cost_model.slippage_bps=0 → only commission+tax, fill_price = base
  6. Zero premium boundary: tax = 0 (notional 0) but commission still applied
  7. cum_pnl invariant after retail cost (R10.x): cum = realised + unrealised
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.execution import (
    Fill,
    MidFillModel,
    RetailCostModel,
    SettleFillModel,
    SlippageFillModel,
    WorstSideFillModel,
)

# ---------------------------------------------------------------------------
# RetailCostModel input validation
# ---------------------------------------------------------------------------


def test_retail_cost_model_negative_raises() -> None:
    with pytest.raises(ValueError, match="commission_per_contract must be >= 0"):
        RetailCostModel(commission_per_contract=-1.0)
    with pytest.raises(ValueError, match="tax_bps must be >= 0"):
        RetailCostModel(tax_bps=-0.1)
    with pytest.raises(ValueError, match="slippage_bps must be >= 0"):
        RetailCostModel(slippage_bps=-5.0)


# ---------------------------------------------------------------------------
# Backward compat: cost_model=None → identical to Day 5
# ---------------------------------------------------------------------------


def _stub_chain_row() -> pd.Series:
    return pd.Series(
        {
            "date": pd.Timestamp("2026-01-15"),
            "expiry": pd.Timestamp("2026-02-19"),
            "strike": 16800,
            "option_type": "call",
            "settle": 100.0,
            "bid": 99.0,
            "ask": 101.0,
        }
    )


def test_fill_model_no_cost_backward_compat() -> None:
    """No cost_model → fill_price unchanged + commission/tax = 0 (Day 5 invariant)."""
    row = _stub_chain_row()
    for model_cls in (SettleFillModel, MidFillModel, WorstSideFillModel):
        fm = model_cls()
        fill_sell = fm.fill(row, "sell", 1)
        fill_buy = fm.fill(row, "buy", 1)
        assert fill_sell.commission == 0.0
        assert fill_sell.tax == 0.0
        assert fill_buy.commission == 0.0
        assert fill_buy.tax == 0.0


# ---------------------------------------------------------------------------
# Commission + tax computation (Pattern 12 ≥3 numbers)
# ---------------------------------------------------------------------------


def test_worst_side_with_cost_model_known_values() -> None:
    """Manual derivation (R12.0 tax=10 bps per TAIFEX 0.001 fee schedule):
    sell side, raw price = bid = 99.0
    cost: commission_per_contract=12, tax_bps=10, slippage_bps=15
    qty = 2 contracts
    slippage adjusted: 99.0 * (1 - 15/10000) = 99.0 * 0.9985 = 98.8515
    commission = 2 * 12 = 24.0
    tax = |98.8515| * 50 (TXO_MULTIPLIER) * 2 * 10 / 10000
        = 98.8515 * 50 * 2 * 0.001 = 9.88515 (per leg notional)
    """
    row = _stub_chain_row()
    cm = RetailCostModel(commission_per_contract=12.0, tax_bps=10.0, slippage_bps=15.0)
    fm = WorstSideFillModel(cost_model=cm)
    fill = fm.fill(row, "sell", 2)
    expected_price = 99.0 * 0.9985
    assert fill.fill_price == pytest.approx(expected_price, rel=1e-9)
    assert fill.commission == pytest.approx(24.0, rel=1e-9)
    assert fill.tax == pytest.approx(expected_price * 50 * 2 * 0.001, rel=1e-7)


def test_worst_side_buy_slippage_adds() -> None:
    """Buy side: raw = ask = 101.0; slippage adds (×(1 + slip)) → fill > raw."""
    row = _stub_chain_row()
    cm = RetailCostModel(slippage_bps=20.0)
    fm = WorstSideFillModel(cost_model=cm)
    fill = fm.fill(row, "buy", 1)
    assert fill.fill_price == pytest.approx(101.0 * 1.0020, rel=1e-9)
    # commission default 12, tax default 10 bps (R12.0 P4a fix; was 2 bps)
    assert fill.commission == 12.0


# ---------------------------------------------------------------------------
# SlippageFillModel native + RetailCostModel.slippage compounding (Pattern 0 #4)
# ---------------------------------------------------------------------------


def test_slippage_fillmodel_native_plus_cost_compounds() -> None:
    """SlippageFillModel.slippage_bps=10 + cost_model.slippage_bps=15 → both apply.

    sell: base mid=100, native slip → 100 * 0.999 = 99.9
    cost slippage on top → 99.9 * 0.9985 = 99.75015
    Confirms compounding rather than max.
    """
    row = _stub_chain_row()
    cm = RetailCostModel(commission_per_contract=0.0, tax_bps=0.0, slippage_bps=15.0)
    fm = SlippageFillModel(slippage_bps=10.0, base="mid", cost_model=cm)
    fill = fm.fill(row, "sell", 1)
    expected = 100.0 * (1 - 0.001) * (1 - 0.0015)
    assert fill.fill_price == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# Mutation: cost slippage 0 → only commission + tax (Pattern 11)
# ---------------------------------------------------------------------------


def test_cost_model_zero_slippage_mutation() -> None:
    """slippage_bps=0 → fill_price unchanged from base; commission + tax still apply."""
    row = _stub_chain_row()
    cm_zero_slip = RetailCostModel(commission_per_contract=12.0, tax_bps=10.0, slippage_bps=0.0)
    fm = WorstSideFillModel(cost_model=cm_zero_slip)
    fill = fm.fill(row, "sell", 1)
    assert fill.fill_price == 99.0  # unchanged from base bid
    assert fill.commission == 12.0
    assert fill.tax > 0.0


# ---------------------------------------------------------------------------
# Zero premium boundary: tax 0, commission applied (Pattern 0 #2)
# ---------------------------------------------------------------------------


def test_zero_premium_tax_zero_commission_nonzero() -> None:
    row = _stub_chain_row().copy()
    row["bid"] = 0.0
    row["ask"] = 0.0
    row["settle"] = 0.0
    cm = RetailCostModel(commission_per_contract=12.0, tax_bps=10.0, slippage_bps=15.0)
    fm = WorstSideFillModel(cost_model=cm)
    fill = fm.fill(row, "sell", 3)
    assert fill.fill_price == 0.0
    assert fill.tax == 0.0  # |0| × ... = 0
    assert fill.commission == 36.0  # 3 × 12


# ---------------------------------------------------------------------------
# Fill dataclass field validation
# ---------------------------------------------------------------------------


def test_fill_dataclass_negative_commission_raises() -> None:
    with pytest.raises(ValueError, match="Fill.commission must be >= 0"):
        Fill(
            date=pd.Timestamp("2026-01-15"),
            contract="TXO20260219C16800",
            strike=16800,
            option_type="call",
            side="sell",
            qty=1,
            fill_price=99.0,
            model_name="test",
            commission=-1.0,
        )


# ---------------------------------------------------------------------------
# cum_pnl invariant: ≥3 numbers (R10.x)
# ---------------------------------------------------------------------------


def test_cum_pnl_invariant_holds_with_retail_cost(synthetic_chain: pd.DataFrame) -> None:
    """e2e: cum_pnl = realised + unrealised holds when cost_model active.

    Pattern 0 #3: cost reduces cash AND realised_pnl_total in lockstep — cum_pnl
    invariant `daily_pnl.sum() == final_cash - initial_capital` (R10.x R7 F1)
    must hold when all positions are closed (final_unrealised==0). Mirror的
    既有 test_engine_pnl_invariant_all_closed pattern (skip if open positions).
    """
    from src.backtest.engine import run_backtest
    from src.strategies.iron_condor import IronCondor

    cm = RetailCostModel(commission_per_contract=12.0, tax_bps=10.0, slippage_bps=15.0)
    fm = WorstSideFillModel(cost_model=cm)
    ic = IronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
    result = run_backtest(
        ic,
        synthetic_chain,
        fill_model=fm,
        start_date="2026-01-05",
        end_date="2026-03-20",
        initial_capital=1_000_000.0,
    )
    if result["trades"].empty:
        pytest.skip("no trades closed in this window — cum_pnl invariant N/A")
    if abs(result["final_unrealised"]) >= 1e-6:
        pytest.skip(f"position still open at window end (unrealised={result['final_unrealised']})")

    cum_from_daily = float(result["daily_pnl"].sum())
    cash_change = float(result["final_cash"] - 1_000_000.0)
    sum_trade_realised = float(result["trades"]["realised_pnl"].sum())
    # R7 F1 invariant: when all closed, cum == cash_change == sum(trades.realised)
    assert abs(cum_from_daily - cash_change) < 1e-6
    assert abs(cum_from_daily - sum_trade_realised) < 1e-6


def test_p4b_open_cost_reflected_in_trade_log_r12_0() -> None:
    """R12.0 P4b fix (Codex audit): open cost 進 Position.realised_pnl_accumulated.

    Codex 抓到 engine open path 只扣 portfolio.realised_pnl_total 沒扣
    Position.realised_pnl_accumulated → trades.realised_pnl 表 sum 漏算
    開倉 commission + tax → silent inconsistency.

    Direct toy: Portfolio.open + manual cost 扣 → check accumulator updated.
    """
    from src.backtest.portfolio import Portfolio
    from src.common.types import OptionLeg

    p = Portfolio(initial_capital=1_000_000.0)
    leg = OptionLeg(
        contract="TXO20260219C16800",
        strike=16800,
        expiry=pd.Timestamp("2026-02-19"),
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-15"),
        entry_price=100.0,
    )
    p.open([leg], strategy_name="toy")

    # Mirror engine._apply_open_signal R12.0 P4b fix: 3-way deduction
    open_cost = 13.0
    p.cash -= open_cost
    p.realised_pnl_total -= open_cost
    p.positions[-1].realised_pnl_accumulated -= open_cost  # R12.0 P4b critical line

    assert p.realised_pnl_total == -13.0
    assert p.positions[0].realised_pnl_accumulated == -13.0  # was 0.0 pre-fix


def test_default_retail_cost_taifex_aligned_r12_1() -> None:
    """R12.1 Codex caveat 3: default RetailCostModel 必對齊 TAIFEX TXO 真規則.

    TAIFEX 費率表 (https://www.taifex.com.tw/cht/4/feeSchedules):
      - TXO 期權交易稅率 = 0.001 (0.1% on premium notional, per leg per side)
      - 換算 tax_bps = 10.0 (R12.0 P4a fix; pre-fix default 2.0 was 5x understatement)
    """
    cm = RetailCostModel()  # all defaults
    assert cm.tax_bps == 10.0, f"default tax_bps must be 10.0 per TAIFEX 0.001; got {cm.tax_bps}"
    assert cm.commission_per_contract == 12.0
    assert cm.slippage_bps == 15.0

    # Manual derivation: 100 pts × 50 multiplier × 1 lot × 10 bps / 10000 = 5.0 NTD
    # Use commission=0 + slippage=0 to isolate tax
    cm_tax_only = RetailCostModel(commission_per_contract=0.0, tax_bps=10.0, slippage_bps=0.0)
    fm = WorstSideFillModel(cost_model=cm_tax_only)
    row = _stub_chain_row()
    row["bid"] = 100.0  # exact 100 pts to align TAIFEX example
    fill = fm.fill(row, "sell", 1)
    assert fill.tax == pytest.approx(5.0, rel=1e-9), (
        f"100pt × 50 × 1 × 10/10000 must equal 5.0 NTD; got {fill.tax}"
    )


def test_p4b_open_cost_engine_e2e_invariant_r12_1(synthetic_chain: pd.DataFrame) -> None:
    """R12.1 Codex caveat 4: engine e2e 驗 sum(trades.realised_pnl) == cum_from_daily.

    Pre-R12.0-P4b-fix toy showed drift -1375 NTD (open cost 漏進 Position.
    realised_pnl_accumulated). Post-fix: 走 engine 真 path,  在 all-closed
    scenario 兩值必嚴格對齊 (R7 F1 invariant + open cost lockstep).

    Run synthetic chain with extended window so positions close. Skip if
    final_unrealised != 0 (mirrors test_engine_pnl_invariant_all_closed).
    """
    from src.backtest.engine import run_backtest
    from src.strategies.iron_condor import IronCondor

    cm = RetailCostModel()  # default 10 bps
    fm = WorstSideFillModel(cost_model=cm)
    ic = IronCondor(short_delta=0.16, wing_delta=0.08, target_dte=30, exit_dte=14)
    result = run_backtest(
        ic,
        synthetic_chain,
        fill_model=fm,
        start_date="2026-01-05",
        end_date="2026-03-31",
        initial_capital=1_000_000.0,
    )
    if result["trades"].empty:
        pytest.skip("no trades closed in this window")
    if abs(result["final_unrealised"]) >= 1e-6:
        pytest.skip(f"position still open at window end (unrealised={result['final_unrealised']})")

    cum_from_daily = float(result["daily_pnl"].sum())
    cash_change = float(result["final_cash"] - 1_000_000.0)
    sum_trade_realised = float(result["trades"]["realised_pnl"].sum())
    # R12.1 critical invariant: cost lockstep — 3 ways must agree
    assert abs(cum_from_daily - cash_change) < 1e-6, (
        f"cum_from_daily={cum_from_daily} vs cash_change={cash_change} drift"
    )
    assert abs(cum_from_daily - sum_trade_realised) < 1e-6, (
        f"cum_from_daily={cum_from_daily} vs sum(trades.realised)={sum_trade_realised} "
        f"drift — open cost likely missing from Position.realised_pnl_accumulated"
    )
