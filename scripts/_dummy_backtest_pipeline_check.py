"""D-soft Day 6: pipeline 通管路驗證 (NOT 5yr 真實 backtest).

Purpose:
  Prove integration between GatedIronCondor + engine.run_backtest(mark_policy=...)
  + Portfolio.mark_to_market audit metric works end-to-end. Use synthetic chain
  (100% bid/ask) so test is deterministic + fast (<10 sec).

Validates:
  1. GatedIronCondor inherits IronCondor; 4-leg side-specific gate compiles
  2. engine.run_backtest accepts mark_policy ∈ {'strict_mid', 'mid_with_settle_fallback'}
  3. Result dict contains 'mark_audit' DataFrame with per-day fallback_rate
  4. With synthetic 100% mid: fallback_rate per day = 0.0 (sanity)
  5. PnL / max_dd / win_rate metrics computed (no Sharpe interpretation, just math)

NOT validated (留 Week 6+):
  - 5yr TXO 真實 backtest
  - vol surface mark
  - sharpe interpretability
  - entry success rate on 真實 illiquid days

CLI usage:
    python scripts/_dummy_backtest_pipeline_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap repo root on sys.path so `python scripts/_dummy_backtest_pipeline_check.py`
# 能直接跑 (Codex R11 P1 修法). `python -m scripts._dummy_backtest_pipeline_check`
# 走的是 package 路徑不需要這段，但 README 通常教 `python script.py`。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from scripts._gated_strategy import GatedIronCondor  # noqa: E402
from src.backtest.engine import run_backtest  # noqa: E402
from src.data.enrich import add_can_buy_can_sell  # noqa: E402
from src.data.synthetic import SyntheticChainConfig, generate_chain  # noqa: E402


def build_dummy_chain(start: str = "2026-01-02", end: str = "2026-02-27") -> pd.DataFrame:
    """30-trading-day synthetic chain + can_buy/can_sell (synthetic 100% executable)."""
    chain = generate_chain(
        SyntheticChainConfig(
            start_date=start,
            end_date=end,
            spot_start=17500.0,
            sigma=0.18,
            n_strikes_per_side=20,
            max_dte=60,
            seed=42,
        )
    )
    return add_can_buy_can_sell(chain)


def run_dummy_pipeline_check() -> dict:
    """Run integration check; return result dict for assertion / inspection."""
    chain = build_dummy_chain()
    strategy = GatedIronCondor(
        short_delta=0.16,
        wing_delta=0.08,
        target_dte=21,
        exit_dte=7,
        profit_target_pct=0.50,
    )
    return run_backtest(
        strategy=strategy,
        chain_data=chain,
        start_date=str(chain["date"].min().date()),
        end_date=str(chain["date"].max().date()),
        initial_capital=1_000_000,
        strategy_name="GatedIronCondor",
        mark_policy="strict_mid",  # synthetic 100% mid → 不會 fallback
    )


def main() -> int:
    print("Running D-soft dummy pipeline check (NOT 5yr 真 backtest)...")
    result = run_dummy_pipeline_check()

    # Assertions
    assert "mark_audit" in result, "result missing 'mark_audit' (R10.12 修法 a)"
    audit = result["mark_audit"]
    assert isinstance(audit, pd.DataFrame), (
        f"mark_audit must be DataFrame, got {type(audit).__name__}"
    )
    if not audit.empty:
        assert audit["fallback_rate"].max() == 0.0, (
            f"synthetic 100% mid expected fallback_rate=0; got max={audit['fallback_rate'].max()}"
        )
        assert audit["n_legs_marked"].min() >= 0
    assert isinstance(result["daily_pnl"], pd.Series)
    assert isinstance(result["trades"], pd.DataFrame)
    assert "sharpe" in result["metrics"]
    assert "max_drawdown" in result["metrics"]
    assert "win_rate" in result["metrics"]

    n_days = len(result["daily_pnl"])
    n_trades = len(result["trades"])
    print(f"OK: D-soft pipeline check passed ({n_days} days, {n_trades} closed trades)")
    print(f"  daily_pnl head: {result['daily_pnl'].head(3).to_dict()}")
    if not audit.empty:
        print(
            f"  mark_audit shape: {audit.shape}, fallback_rate max: {audit['fallback_rate'].max()}"
        )
    print(f"  metrics: {result['metrics']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
