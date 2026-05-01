"""D-soft Day 6 integration tests — pipeline 通管路 (NOT 5yr 真 backtest).

Plan v6 §4 Day 6 預期 5 tests：
  1. test_gated_iron_condor_inherits_iron_condor_4_method (簽章對)
  2. test_gated_iron_condor_open_filters_executable_only (gate fail 改 hold)
  3. test_dummy_pipeline_check_synthetic_strict_mid_passes (full integration)
  4. test_engine_run_backtest_returns_mark_audit_df (R10.12 修法 a 守護)
  5. test_engine_mark_policy_invalid_raises (sanity)
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts._dummy_backtest_pipeline_check import (
    build_dummy_chain,
    run_dummy_pipeline_check,
)
from scripts._gated_strategy import GatedIronCondor
from src.backtest.engine import run_backtest
from src.strategies.iron_condor import IronCondor


def test_gated_iron_condor_inherits_iron_condor_4_method() -> None:
    """GatedIronCondor 必須繼承 IronCondor 並保留 4 個 Strategy abstract method."""
    assert issubclass(GatedIronCondor, IronCondor)
    g = GatedIronCondor()
    # 4 method 都呼得到 (簽章對齊 IronCondor)
    for name in ("should_open", "open_position", "should_close", "should_adjust"):
        assert callable(getattr(g, name)), f"GatedIronCondor missing {name}"


def test_gated_iron_condor_open_rejects_when_short_leg_lacks_bid() -> None:
    """模擬 short_call leg 沒 bid (can_sell=False) → open signal 改 hold."""
    chain = build_dummy_chain(start="2026-01-02", end="2026-02-27")
    strategy = GatedIronCondor(short_delta=0.16, wing_delta=0.08, target_dte=21, exit_dte=7)

    # 找 day 1 的 chain，破壞 short_call 候選的 can_sell
    from src.common.types import PortfolioState

    state = PortfolioState(positions=[], cash=1_000_000, unrealised_pnl=0.0, realised_pnl=0.0)
    day1 = chain[chain["date"] == chain["date"].min()].copy()
    # 隨手把所有 call 行 can_sell 設 False (極端情況：所有 sell call leg 都不可成交)
    day1.loc[day1["option_type"] == "call", "can_sell"] = False

    signal = strategy.open_position(day1, state)
    assert signal is not None
    assert signal.action == "hold"
    assert "execution_gate_fail" in signal.metadata.get("rejected_reason", "")


def test_dummy_pipeline_check_synthetic_strict_mid_passes() -> None:
    """Full integration: synthetic chain → GatedIronCondor → engine → mark_audit."""
    result = run_dummy_pipeline_check()
    # Smoke: result keys 全到位
    assert {
        "daily_pnl",
        "trades",
        "metrics",
        "mark_audit",
        "final_cash",
        "final_unrealised",
    } <= set(result)
    # synthetic 100% mid → fallback rate 必 0
    audit = result["mark_audit"]
    if not audit.empty:
        assert audit["fallback_rate"].max() == 0.0


def test_engine_run_backtest_returns_mark_audit_df() -> None:
    """R10.12 修法 a 守護: result['mark_audit'] DataFrame 有 fallback_rate /
    n_legs_marked / n_fallback_settle 三 col."""
    chain = build_dummy_chain()
    strategy = GatedIronCondor(target_dte=21, exit_dte=7)
    result = run_backtest(
        strategy=strategy,
        chain_data=chain,
        start_date=str(chain["date"].min().date()),
        end_date=str(chain["date"].max().date()),
        initial_capital=1_000_000,
        strategy_name="dummy",
        mark_policy="strict_mid",
    )
    audit = result["mark_audit"]
    assert isinstance(audit, pd.DataFrame)
    expected_cols = {"fallback_rate", "n_legs_marked", "n_fallback_settle"}
    assert expected_cols <= set(audit.columns), (
        f"missing audit cols: {expected_cols - set(audit.columns)}"
    )


def test_engine_mark_policy_invalid_raises() -> None:
    """invalid mark_policy 應觸發 portfolio.mark_to_market raise."""
    chain = build_dummy_chain()
    strategy = GatedIronCondor(target_dte=21, exit_dte=7)
    with pytest.raises((ValueError, KeyError)):
        run_backtest(
            strategy=strategy,
            chain_data=chain,
            start_date=str(chain["date"].min().date()),
            end_date=str(chain["date"].max().date()),
            initial_capital=1_000_000,
            strategy_name="dummy",
            mark_policy="bogus_policy",
        )


def test_gated_iron_condor_close_gate_defers_when_short_leg_lacks_ask() -> None:
    """Codex R11.2 P close-side gate: short leg 無 ask (買回不到) → should_close defer.

    Setup: 用 IC 開倉日後一天 chain，讓 IC.should_close 條件成立 (DTE stop)，
    但把 short_call leg 對應 chain row 的 ask 設 NaN → can_buy=False → close-side
    gate fail → GatedIC.should_close return False (defer).

    Without R11.2 gate, engine.close → execution._assert_executable raise.
    """
    import numpy as np

    from src.backtest.portfolio import OptionLeg, Position

    chain = build_dummy_chain(start="2026-01-02", end="2026-01-08")
    day1 = chain[chain["date"] == chain["date"].min()].iloc[0]

    # 模擬一個已開倉的 IC short_call leg (qty=-1)
    short_call_strike = 17600
    leg = OptionLeg(
        contract=f"TXO20260121C{short_call_strike}",
        strike=short_call_strike,
        expiry=pd.Timestamp("2026-01-21"),
        option_type="call",
        qty=-1,
        entry_date=day1["date"],
        entry_price=100.0,
    )
    position = Position(
        legs=[leg],
        open_date=day1["date"],
        strategy_name="GatedIronCondor",
        tags={"entry_credit_mid": 100.0},
    )

    # 使用 chain day 2 (DTE 還沒到 stop，但我們強制 ask=NaN 讓 close-side gate fail)
    day2_chain = chain[chain["date"] == sorted(chain["date"].unique())[1]].copy()
    # day2 = 2026-01-05; expiry 2026-01-21 → DTE 16. exit_dte=20 → DTE stop trigger.
    strategy = GatedIronCondor(target_dte=21, exit_dte=20, profit_target_pct=0.50)

    # 把對應 leg 的 chain row ask 設 NaN
    leg_row_mask = (
        (day2_chain["expiry"] == leg.expiry)
        & (day2_chain["strike"] == leg.strike)
        & (day2_chain["option_type"] == leg.option_type)
    )
    day2_chain.loc[leg_row_mask, "ask"] = np.nan
    day2_chain["can_buy"] = day2_chain["ask"].notna()
    day2_chain["can_sell"] = day2_chain["bid"].notna()

    # parent IronCondor.should_close 因 DTE remaining (16) <= exit_dte (20) → True
    # 但 GatedIC close-side gate 看到 short leg can_buy=False → defer (return False)
    assert strategy.should_close(day2_chain, position) is False, (
        "expected GatedIC to defer close when short leg lacks ask (can_buy=False)"
    )

    # 對照組: 不破壞 ask → should_close 應 return True (parent DTE stop trigger)
    day2_chain_clean = chain[chain["date"] == sorted(chain["date"].unique())[1]].copy()
    day2_chain_clean["can_buy"] = day2_chain_clean["ask"].notna()
    day2_chain_clean["can_sell"] = day2_chain_clean["bid"].notna()
    assert strategy.should_close(day2_chain_clean, position) is True, (
        "expected parent DTE stop trigger (DTE=16 <= exit_dte=20) → True"
    )


def test_should_close_raises_on_multi_day_chain() -> None:
    """R11.4 P3 (Codex multi-day attack 防呆): should_close 強制 single-day chain."""
    from src.backtest.portfolio import OptionLeg, Position

    expiry = pd.Timestamp("2026-01-21")
    chain = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-15"),
                "expiry": expiry,
                "strike": 17600,
                "option_type": "call",
                "settle": 50.0,
                "close": 48.0,
                "bid": 0.5,
                "ask": 0.6,
                "volume": 100,
                "open_interest": 100,
                "underlying": 17500.0,
                "can_buy": True,
                "can_sell": True,
            },
            {
                "date": expiry,  # 第 2 天 — multi-day chain
                "expiry": expiry,
                "strike": 17600,
                "option_type": "call",
                "settle": 0.5,
                "close": 0.5,
                "bid": 0.4,
                "ask": 0.6,
                "volume": 100,
                "open_interest": 100,
                "underlying": 17600.0,
                "can_buy": True,
                "can_sell": True,
            },
        ]
    )
    leg = OptionLeg(
        contract="X",
        strike=17600,
        expiry=expiry,
        option_type="call",
        qty=-1,
        entry_date=pd.Timestamp("2026-01-02"),
        entry_price=100.0,
    )
    position = Position(
        legs=[leg],
        open_date=pd.Timestamp("2026-01-02"),
        strategy_name="Gated",
        tags={"entry_credit_mid": 100.0},
    )
    strategy = GatedIronCondor(target_dte=21, exit_dte=20, profit_target_pct=0.50)
    with pytest.raises(ValueError, match="single-day"):
        strategy.should_close(chain, position)


def test_close_gate_expiry_day_rescue_no_position_stuck() -> None:
    """Codex R11.3 P3 e2e: expiry-day NaN bid/ask 不 defer (走 intrinsic payoff).

    Bug R11.3 抓: defer 設計造成 position 在 expiry 卡死 → closed_trades=0,
    final_unrealised inflated. Rescue: should_close 對 leg.expiry <= today
    skip gate 讓 portfolio.close 走 _intrinsic_payoff (不需 bid/ask).

    Setup: chain 跑到 expiry day 後一天 (2026-01-22, 過了 2026-01-21 expiry).
    第一天 finite (IC 開倉 OK), 之後全 NaN bid/ask. 預期：到 expiry day
    GatedIC.should_close 不再 defer → engine.close 走 intrinsic payoff →
    至少 1 closed trade (不再 0).
    """
    import numpy as np

    from src.backtest.engine import run_backtest

    chain = build_dummy_chain(start="2026-01-02", end="2026-01-22")  # 含 2026-01-21 expiry
    day1 = chain["date"].min()
    chain.loc[chain["date"] > day1, "bid"] = np.nan
    chain.loc[chain["date"] > day1, "ask"] = np.nan
    chain["can_buy"] = chain["ask"].notna()
    chain["can_sell"] = chain["bid"].notna()

    strategy = GatedIronCondor(
        short_delta=0.16,
        wing_delta=0.08,
        target_dte=21,
        exit_dte=20,
        profit_target_pct=0.50,
    )
    result = run_backtest(
        strategy=strategy,
        chain_data=chain,
        start_date=str(day1.date()),
        end_date=str(chain["date"].max().date()),
        initial_capital=1_000_000,
        strategy_name="dummy_expiry_rescue",
        mark_policy="mid_with_settle_fallback",
    )

    # 預期: position 不再卡死到 chain 最後一天，至少 1 closed trade
    assert len(result["trades"]) >= 1, (
        f"R11.3 P3 expiry-day rescue 失敗: closed_trades=0 表示 defer 仍卡住. "
        f"final_unrealised={result['final_unrealised']:.2f}, "
        f"trades_df rows={len(result['trades'])}"
    )


def test_dummy_pipeline_30pct_bidask_mask_with_settle_fallback() -> None:
    """Codex R11 P3 修法：30% bid/ask mask + mid_with_settle_fallback 壓力測試.

    設計約束 (Codex R11 P2 acknowledged: Week 6+ 才補 close-side gate)：
      - chain 限 5 天 + IC target_dte=21/exit_dte=1/profit_target=0.99 → DTE
        stop / profit-target / loss-cap 在 5 天內絕不 trigger，position 持有
        到 final_unrealised → 純測 mark hybrid，不踩 close-side execution gate
      - mask 只作用在 day > day1：day1 全保留 → IC 4-leg open 必成 → day 2+
        mark 走 settle fallback

    Validates:
      1. 真實 illiquid 場景 (NaN bid/ask) 下 hybrid mark_policy 不 crash
      2. mark_audit fallback_rate > 0 (證 fallback path 真的觸發)
      3. mark_audit n_legs_marked / n_fallback_settle 三欄完整
      4. daily_pnl Series 非空 (整段沒 crash)
    """
    import numpy as np

    chain = build_dummy_chain(start="2026-01-02", end="2026-01-08")  # 5 trading days
    day1 = chain["date"].min()
    # Mask 30% 只作用 day > day1 (day1 整 chain finite → IC open 必成)
    rng = np.random.default_rng(seed=2026)
    mask_rows = (chain["date"] > day1) & (rng.random(len(chain)) < 0.30)
    chain.loc[mask_rows, "bid"] = float("nan")
    chain.loc[mask_rows, "ask"] = float("nan")
    # can_buy/can_sell 同步刷新 (R10.10 3ii)
    chain["can_buy"] = chain["ask"].notna()
    chain["can_sell"] = chain["bid"].notna()

    # 高 profit_target + 短 chain → 5 天內 IC 絕不 close trigger
    strategy = GatedIronCondor(
        short_delta=0.16,
        wing_delta=0.08,
        target_dte=21,
        exit_dte=1,
        profit_target_pct=0.99,
    )
    result = run_backtest(
        strategy=strategy,
        chain_data=chain,
        start_date=str(chain["date"].min().date()),
        end_date=str(chain["date"].max().date()),
        initial_capital=1_000_000,
        strategy_name="dummy_masked",
        mark_policy="mid_with_settle_fallback",
    )

    audit = result["mark_audit"]
    # mark_audit DataFrame 三欄齊
    assert {"fallback_rate", "n_legs_marked", "n_fallback_settle"} <= set(audit.columns)
    # 開倉後才有 leg marked → 找有 leg 的天 fallback_rate 應 > 0
    days_with_legs = audit[audit["n_legs_marked"] > 0]
    assert not days_with_legs.empty, "expected IC opened on day1 → ≥1 day with legs marked"
    # 30% mask 在 day 2+ → max fallback_rate 必 ≥ 0.20 (Codex R11.1 P 修法)
    # 4-leg IC 在某天若有 1 leg 走 fallback → fallback_rate = 0.25；若有 2 leg → 0.50.
    # 30% mask × 4 leg 期望 ≈ 1.2 leg/day fallback → 0.30 平均；≥ 0.20 是合理下限.
    max_fb = days_with_legs["fallback_rate"].max()
    assert max_fb >= 0.20, (
        f"30% bid/ask mask 下預期 max fallback_rate >= 0.20 (Codex R11.1 hollow gate), "
        f"got max={max_fb}; days_with_legs:\n{days_with_legs}"
    )
    # mean fallback rate 需 > 0 (證有真資料路徑被 fire 過，非 hollow)
    mean_fb = days_with_legs["fallback_rate"].mean()
    assert mean_fb > 0.05, f"mean fallback_rate too low ({mean_fb}); fallback path 可能沒被 fire"
    # daily_pnl 非空 → 整段沒 crash
    assert isinstance(result["daily_pnl"], pd.Series)
    assert len(result["daily_pnl"]) > 0
