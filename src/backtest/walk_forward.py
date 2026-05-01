"""Walk-forward backtest harness (Week 6 Day 6.2).

Pro 量化標準時序 OOS validation: rolling train window → fixed-size OOS test
window → step forward by N days → repeat. Aggregate per-fold OOS daily PnL
into single cum_pnl curve for end-to-end evaluation.

User 拍板 (Plan §1) — R12.0 P3 fix (Codex audit) overrode step:
  train_window_days = 252  (1yr rolling)
  test_window_days  = 63   (1 quarter OOS)
  step_days         = 63   (R12.0 disjoint OOS; 原 plan 21=1mo overlap 已禁)
  expanding         = False (default rolling; expanding 待用)

R12.0 P3 紀律: step >= test 強制；step < test → __post_init__ raise.
原 plan 1mo step 會造成 fold OOS 重疊 42 days, concat daily_pnl 同日重複 →
aggregate Sharpe / max DD / Calmar 全 inflate. Disjoint quarterly 5yr ≈ 16 folds,
7yr ≈ 24 folds. 原 ~48 folds count 不再可達.

R12 Plan 3 + 3 版 ablation:
  Walk-forward 接受任意 strategy_factory(returns_history) → Strategy callable;
  caller 注入 vanilla / IVPercentileGate / HMMRegimeGate wrapper.

PIT Correctness (R10.5 P2):
  - test fold 只接受 chain rows 在 [test_start, test_end] 範圍
  - strategy_factory(returns_history_up_to_test_start) — train 期 returns 才能
    給 regime gate fit; gate 不可看 test 期資料
  - engine.run_backtest 內部已 day-by-day forward (R10.x 紀律), 無 backward look

Surface cache miss handling:
  fold 內某天 chain 行 model_price=NaN 由 add_model_price 上游處理 (Day 5.1
  紀律); walk_forward 不重複驗 — caller 注入已 enriched chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from src.backtest.engine import run_backtest

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.strategies.base import Strategy


@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-forward window 設定 (Plan §1 拍板; R12.0 P3 disjoint OOS).

    Attributes:
        train_window_days: rolling train window 天數 (1yr=252).
        test_window_days: OOS test 窗 (1q=63).
        step_days: 每 fold 前進步幅 (預設 = test_window_days 確保 fold 之間
            disjoint OOS — R12.0 Codex P3 fix). User 原 plan step=21 (1mo)
            導致 fold OOS 重疊 42 days; aggregate metrics 會 inflate. Disjoint
            版本 5yr ≈ 16 folds, 7yr ≈ 24 folds.
        expanding: True=expanding train (累積擴大); False=rolling (固定大小, default).
        mark_policy: 傳入 engine.run_backtest 的 mark_policy.
        initial_capital: 每 fold 起始 capital (簡化: 每 fold 重置, 不累積)
    """

    train_window_days: int = 252
    test_window_days: int = 63
    step_days: int = 63  # R12.0 P3: disjoint by default; 原 21 會重疊 OOS
    expanding: bool = False
    mark_policy: str = "mid_with_surface_fallback"
    initial_capital: float = 1_000_000.0

    def __post_init__(self) -> None:
        if self.train_window_days <= 0:
            raise ValueError(f"train_window_days must be > 0, got {self.train_window_days}")
        if self.test_window_days <= 0:
            raise ValueError(f"test_window_days must be > 0, got {self.test_window_days}")
        if self.step_days <= 0:
            raise ValueError(f"step_days must be > 0, got {self.step_days}")
        if self.initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {self.initial_capital}")
        # R12.0 P3 fix (Codex audit): step < test → fold OOS 重疊 → concat
        # daily_pnl 同一日重複計算 → aggregate Sharpe / max DD / Calmar 全 inflate.
        # Pro 紀律: walk-forward fold OOS 必須 disjoint;  step >= test 才能 disjoint.
        # User R12 拍板 step=21 test=63 — 該配置會 reject (此為 critical fix).
        if self.step_days < self.test_window_days:
            raise ValueError(
                f"step_days ({self.step_days}) must be >= test_window_days "
                f"({self.test_window_days}) to keep fold OOS windows non-overlapping; "
                f"otherwise concat daily_pnl repeats dates → aggregate metrics inflate "
                f"(R12.0 Codex P3 fix)."
            )


@dataclass(frozen=True)
class FoldResult:
    """One fold's IS/OOS outcome.

    OOS daily_pnl is the canonical aggregation target (cum_pnl 對齊真實序列).
    """

    fold_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    daily_pnl: pd.Series  # OOS only, indexed by trading dates
    trades: pd.DataFrame
    mark_audit: pd.DataFrame
    metrics: dict[str, float]
    final_cash: float
    final_unrealised: float
    error: str | None = None  # if fold raised, capture for audit
    # R12.2 P4 fix (Codex audit): per-fold rejected_reasons snapshot — captures
    # GatedIC/GatedVerticalStrategy.rejected_reasons accumulator at end of fold.
    # Empty DataFrame if strategy doesn't expose get_rejected_reasons (e.g.
    # vanilla IronCondor/VerticalStrategy without execution gate). Persists
    # across walk_forward folds (each fold builds fresh strategy via factory).
    rejected_reasons: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=["date", "path", "reason", "leg"])
    )


@dataclass(frozen=True)
class AggregatedResult:
    """Aggregated walk-forward result across all folds."""

    folds: list[FoldResult]
    daily_pnl: pd.Series  # concat all folds' OOS daily_pnl
    n_folds: int
    n_failed_folds: int
    metrics: dict[str, float]  # aggregate Sharpe / max_DD / etc
    fold_metrics_df: pd.DataFrame  # per-fold metrics for audit


_MAX_UNWRAP_DEPTH = 16  # R12.4 P fix (Codex audit): 3 layer too tight


def _extract_rejected_reasons(strategy: Any) -> pd.DataFrame:
    """R12.2 P4 + R12.4 P fix: traverse strategy → base/inner to find accumulator.

    Supports arbitrary wrapper depth up to _MAX_UNWRAP_DEPTH (16) via cycle-
    safe traversal. Codex R12.3 verified depth 0-2 OK but depth 3+ silent fail
    in original `range(3)` loop. Generalised to handle:
      - Direct: GatedIronCondor / GatedVerticalStrategy
      - 1-deep: RegimeWrappedStrategy(GatedIC)
      - N-deep: any future composition (MetaWrapper(RegimeWrapped(Gated)) etc)

    Schema fixed at (date, path, reason, leg) for stable CSV output.
    Cycle-safe: track id() of visited candidates to avoid infinite loop.
    """
    empty = pd.DataFrame(columns=["date", "path", "reason", "leg"])
    candidate = strategy
    visited: set[int] = set()
    for _ in range(_MAX_UNWRAP_DEPTH):
        if candidate is None or id(candidate) in visited:
            break
        visited.add(id(candidate))
        getter = getattr(candidate, "get_rejected_reasons", None)
        if callable(getter):
            try:
                df = getter()
            except (AttributeError, TypeError):
                return empty
            if df is None or len(df) == 0:
                return empty
            for col in ["date", "path", "reason", "leg"]:
                if col not in df.columns:
                    df[col] = ""
            return df
        # Try unwrapping via common wrapper attribute names
        for attr in ("base", "base_strategy", "inner", "wrapped"):
            nxt = getattr(candidate, attr, None)
            if nxt is not None:
                candidate = nxt
                break
        else:
            break  # no more unwrap path
    return empty


def _generate_fold_windows(
    chain_dates: pd.DatetimeIndex,
    config: WalkForwardConfig,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Generate (train_start, train_end, test_start, test_end) per fold.

    Rolling: train window 固定 size, 滑動 step_days.
    Expanding: train window 累積擴大從 chain_dates[0] 到 train_end.
    """
    sorted_dates = chain_dates.sort_values().unique()
    if len(sorted_dates) < config.train_window_days + config.test_window_days:
        return []  # too short for one fold

    folds: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    n = len(sorted_dates)
    train_start_idx = 0
    while True:
        train_end_idx = train_start_idx + config.train_window_days - 1
        test_start_idx = train_end_idx + 1
        test_end_idx = test_start_idx + config.test_window_days - 1
        if test_end_idx >= n:
            break
        train_start = (
            pd.Timestamp(sorted_dates[0])
            if config.expanding
            else pd.Timestamp(sorted_dates[train_start_idx])
        )
        train_end = pd.Timestamp(sorted_dates[train_end_idx])
        test_start = pd.Timestamp(sorted_dates[test_start_idx])
        test_end = pd.Timestamp(sorted_dates[test_end_idx])
        folds.append((train_start, train_end, test_start, test_end))
        train_start_idx += config.step_days
    return folds


def walk_forward_backtest(
    strategy_factory: Callable[[pd.Series], Strategy],
    chain: pd.DataFrame,
    config: WalkForwardConfig,
    *,
    underlying_returns: pd.Series | None = None,
    fill_model: Any = None,
) -> AggregatedResult:
    """Run walk-forward backtest: rolling train/test fold loop.

    Args:
        strategy_factory: callable(train_returns) -> Strategy. The factory
            receives the underlying returns up to test_start (PIT) so it can
            fit any regime gate using train data only.
        chain: enriched chain DataFrame (含 date, expiry, strike, ..., model_price).
        config: WalkForwardConfig.
        underlying_returns: pd.Series of underlying log returns indexed by
            date (e.g. TAIEX). Required if strategy_factory uses regime gate;
            walk_forward slices to train period before passing to factory.
        fill_model: passed to engine.run_backtest.

    Returns:
        AggregatedResult with concat'd OOS daily_pnl + per-fold metrics.

    Raises:
        ValueError: chain empty / chain missing 'date' col.
    """
    if chain is None or chain.empty:
        raise ValueError("walk_forward_backtest: chain is empty")
    if "date" not in chain.columns:
        raise ValueError("walk_forward_backtest: chain missing 'date' column")

    chain_dates = pd.DatetimeIndex(pd.to_datetime(chain["date"]).unique())
    fold_windows = _generate_fold_windows(chain_dates, config)
    if not fold_windows:
        return AggregatedResult(
            folds=[],
            daily_pnl=pd.Series(dtype=np.float64),
            n_folds=0,
            n_failed_folds=0,
            metrics={"sharpe": float("nan"), "max_drawdown": 0.0, "win_rate": float("nan")},
            fold_metrics_df=pd.DataFrame(),
        )

    folds: list[FoldResult] = []
    for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(fold_windows):
        # PIT slice: returns history up to (NOT including) test_start
        if underlying_returns is not None:
            train_returns = underlying_returns[
                (underlying_returns.index >= train_start) & (underlying_returns.index <= train_end)
            ]
        else:
            train_returns = pd.Series(dtype=np.float64)

        try:
            strategy = strategy_factory(train_returns)
        except Exception as e:  # noqa: BLE001
            folds.append(
                FoldResult(
                    fold_index=fold_idx,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    daily_pnl=pd.Series(dtype=np.float64),
                    trades=pd.DataFrame(),
                    mark_audit=pd.DataFrame(),
                    metrics={},
                    final_cash=float("nan"),
                    final_unrealised=float("nan"),
                    error=f"strategy_factory: {type(e).__name__}: {str(e)[:100]}",
                )
            )
            continue

        try:
            result = run_backtest(
                strategy,
                chain,
                start_date=str(test_start.date()),
                end_date=str(test_end.date()),
                initial_capital=config.initial_capital,
                fill_model=fill_model,
                mark_policy=config.mark_policy,
            )
            # R12.2 P4 fix (Codex audit): capture rejected_reasons accumulator
            # before strategy goes out of scope. Try strategy.get_rejected_reasons()
            # then strategy.base.get_rejected_reasons() for RegimeWrappedStrategy.
            rejected_df = _extract_rejected_reasons(strategy)
            folds.append(
                FoldResult(
                    fold_index=fold_idx,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    daily_pnl=result["daily_pnl"],
                    trades=result["trades"],
                    mark_audit=result["mark_audit"],
                    metrics=dict(result["metrics"]),
                    final_cash=float(result["final_cash"]),
                    final_unrealised=float(result["final_unrealised"]),
                    error=None,
                    rejected_reasons=rejected_df,
                )
            )
        except (ValueError, RuntimeError) as e:
            folds.append(
                FoldResult(
                    fold_index=fold_idx,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    daily_pnl=pd.Series(dtype=np.float64),
                    trades=pd.DataFrame(),
                    mark_audit=pd.DataFrame(),
                    metrics={},
                    final_cash=float("nan"),
                    final_unrealised=float("nan"),
                    error=f"run_backtest: {type(e).__name__}: {str(e)[:100]}",
                )
            )

    return _aggregate_folds(folds, config.initial_capital)


def _aggregate_folds(folds: list[FoldResult], initial_capital: float) -> AggregatedResult:
    """Concat OOS daily_pnl + per-fold metrics summary."""
    successful = [f for f in folds if f.error is None]
    n_failed = len(folds) - len(successful)

    if not successful:
        return AggregatedResult(
            folds=folds,
            daily_pnl=pd.Series(dtype=np.float64),
            n_folds=len(folds),
            n_failed_folds=n_failed,
            metrics={"sharpe": float("nan"), "max_drawdown": 0.0, "win_rate": float("nan")},
            fold_metrics_df=pd.DataFrame(),
        )

    # Concat OOS daily_pnl (per-fold dates non-overlapping by design)
    pnl_parts = [f.daily_pnl for f in successful if not f.daily_pnl.empty]
    agg_pnl = pd.concat(pnl_parts).sort_index() if pnl_parts else pd.Series(dtype=np.float64)

    # Aggregate metrics from concat'd PnL
    agg_metrics: dict[str, float] = {}
    if len(agg_pnl) >= 2:
        from src.backtest.metrics import max_drawdown, sharpe_ratio

        agg_metrics["sharpe"] = sharpe_ratio(agg_pnl, initial_capital=initial_capital)
        agg_metrics["max_drawdown"] = max_drawdown(agg_pnl, initial_capital=initial_capital)
        agg_metrics["n_observations"] = float(len(agg_pnl))
    else:
        agg_metrics["sharpe"] = float("nan")
        agg_metrics["max_drawdown"] = 0.0
        agg_metrics["n_observations"] = float(len(agg_pnl))

    # Per-fold metrics DataFrame
    fold_rows = []
    for f in folds:
        row = {
            "fold_index": f.fold_index,
            "train_start": f.train_start,
            "train_end": f.train_end,
            "test_start": f.test_start,
            "test_end": f.test_end,
            "n_trades": len(f.trades),
            "error": f.error,
            **{f"metric_{k}": v for k, v in f.metrics.items()},
        }
        fold_rows.append(row)
    fold_metrics_df = pd.DataFrame(fold_rows)

    return AggregatedResult(
        folds=folds,
        daily_pnl=agg_pnl,
        n_folds=len(folds),
        n_failed_folds=n_failed,
        metrics=agg_metrics,
        fold_metrics_df=fold_metrics_df,
    )
