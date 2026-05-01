"""Event-driven backtest loop (daily granularity).

Week 2 Day 5 implementation. Codex R4 + GPT-5.5 north-star: "settle-as-fill
overstates short-premium PnL". Engine therefore takes ``WorstSideFillModel``
as default and exposes ``fill_model`` keyword for sensitivity tests.

Daily loop (per trading day, in order):
  1. Snapshot chain for today (PIT slice).
  2. For each open position, evaluate ``strategy.should_close`` → close if True.
  3. For each surviving open position, evaluate ``strategy.should_adjust``
     → if non-None and action == ``"adjust"``, close the breached legs (we
     keep the surviving vertical for Phase 1; Phase 2 will support partial
     close with a real adjust order pipeline).
  4. ``strategy.should_open(state)`` and ``strategy.open_position(state)``.
     If signal action == ``"open"``: pass each order through ``fill_model``
     to derive a ``Fill``, build OptionLeg per fill, then ``portfolio.open``.
  5. Mark-to-market the day's portfolio for the daily PnL row.

Strict point-in-time (PIT): chain passed to strategy is filtered to
``date == today`` only. The full chain history stays internal.

Returns dict::

    {
      "daily_pnl":     pd.Series indexed by date (TWD),
      "trades":        pd.DataFrame of closed positions (open/close dates,
                       strategy, realised_pnl, n_legs),
      "metrics":       {sharpe, max_drawdown, win_rate},
      "final_cash":    float,
      "final_unrealised": float,
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from config.constants import TXO_MULTIPLIER
from src.backtest.execution import WorstSideFillModel
from src.backtest.metrics import max_drawdown, sharpe_ratio, win_rate
from src.backtest.portfolio import OptionLeg, Portfolio
from src.common.types import PortfolioState, Side

if TYPE_CHECKING:
    from src.backtest.execution import Fill, FillModel
    from src.common.types import Order, StrategySignal
    from src.strategies.base import Strategy


def _build_state(portfolio: Portfolio, unrealised: float = 0.0) -> PortfolioState:
    """Build a `PortfolioState` snapshot from a `Portfolio` (engine-internal helper).

    Aggregate Greeks are intentionally left empty here; engines that want
    Greek-driven strategies can wire `portfolio.aggregate_greeks(chain)` in.

    ``unrealised`` (R5 P2) is supplied so the portfolio_loss_cap gate can see
    open-position drawdown, not just realised PnL.
    """
    open_positions = [p for p in portfolio.positions if p.close_date is None]
    return PortfolioState(
        cash=portfolio.cash,
        positions=open_positions,
        realised_pnl=portfolio.realised_pnl_total,
        unrealised_pnl=unrealised,
        initial_capital=portfolio.initial_capital,
        aggregate_greeks={},
    )


def _row_for_order(chain_today: pd.DataFrame, order: Order) -> pd.Series:
    """Locate the single chain row matching an order's (strike, expiry, type)."""
    rows = chain_today[
        (chain_today["strike"] == order.strike)
        & (chain_today["expiry"] == order.expiry)
        & (chain_today["option_type"] == order.option_type)
    ]
    if rows.empty:
        raise ValueError(
            f"No chain row matches order: strike={order.strike} expiry={order.expiry} "
            f"option_type={order.option_type}"
        )
    return rows.iloc[0]


def _apply_open_signal(
    portfolio: Portfolio,
    chain_today: pd.DataFrame,
    signal: StrategySignal,
    fill_model: FillModel,
    today: pd.Timestamp,
    strategy_name: str,
) -> tuple[list[Fill], float]:
    """Resolve all `signal.orders` to `Fill`s and open the position. Returns (fills, mid_credit).

    Day 6.4: 套用 RetailCostModel — fills carry commission + tax fields; engine
    從 cash + realised_pnl_total 同步扣 (R10.x cum_pnl invariant: cum_pnl =
    realised + unrealised — costs 算 realised loss, 不會 silent 漏 cum_pnl).
    """
    fills: list[Fill] = []
    legs: list[OptionLeg] = []
    for order in signal.orders:
        row = _row_for_order(chain_today, order)
        fill = fill_model.fill(row, order.side, order.qty)
        fills.append(fill)
        # Map fill back to a signed-qty OptionLeg (sell → -qty, buy → +qty).
        signed_qty = -fill.qty if order.side == "sell" else fill.qty
        legs.append(
            OptionLeg(
                contract=fill.contract,
                strike=fill.strike,
                expiry=order.expiry,
                option_type=fill.option_type,
                qty=signed_qty,
                entry_date=today,
                entry_price=fill.fill_price,
            )
        )

    # Engine-injected tags so risk gating + close logic have baselines.
    tags: dict[str, float] = {}
    if "max_defined_risk_twd" in signal.metadata:
        tags["max_defined_risk_twd"] = float(signal.metadata["max_defined_risk_twd"])
    if "mid_credit" in signal.metadata:
        tags["entry_credit_mid"] = float(signal.metadata["mid_credit"])
    if "worst_credit" in signal.metadata:
        tags["entry_credit_worst"] = float(signal.metadata["worst_credit"])

    portfolio.open(legs, strategy_name, **tags)
    # Day 6.4 + R12.0 P4b fix (Codex audit):
    #   Deduct retail costs after portfolio.open populates entry. Apply to:
    #     1. cash                       — actual TWD outflow
    #     2. realised_pnl_total         — cum_pnl invariant (cum = realised + unrealised)
    #     3. position.realised_pnl_accumulated — trade log per-trade attribution
    #        (R12.0 P4b: 原本只扣 portfolio.realised_pnl_total，trades 表 sum 漏算
    #        開倉成本; sum(trades.realised) ≠ cum_from_daily silent inconsistency.)
    total_cost = sum(fill.commission + fill.tax for fill in fills)
    if total_cost > 0:
        portfolio.cash -= total_cost
        portfolio.realised_pnl_total -= total_cost
        portfolio.positions[-1].realised_pnl_accumulated -= total_cost
    return fills, float(signal.metadata.get("mid_credit", 0.0))


def _apply_adjust_signal(
    portfolio: Portfolio,
    chain_today: pd.DataFrame,
    signal: StrategySignal,
    position_idx: int,
    today: pd.Timestamp,
    fill_model: FillModel,
) -> float:
    """Close the breached-side legs of a position via ``fill_model``.

    Phase 1 single-roll: we partially close the position (the breached pair).
    Symmetric to open: short close goes through "buy" side, long close through
    "sell" side. Missing chain row before expiry → raise (R5 P1).
    """
    position = portfolio.positions[position_idx]
    if position.close_date is not None:
        return 0.0

    breached_contracts = {order.contract for order in signal.orders}
    surviving: list[OptionLeg] = []
    realised = 0.0
    cash_delta = 0.0
    for leg in position.legs:
        if leg.contract not in breached_contracts:
            surviving.append(leg)
            continue
        rows = chain_today[
            (chain_today["strike"] == leg.strike)
            & (chain_today["expiry"] == leg.expiry)
            & (chain_today["option_type"] == leg.option_type)
        ]
        if rows.empty:
            if today >= leg.expiry:
                from src.backtest.portfolio import _intrinsic_payoff

                spot = float(chain_today["underlying"].iloc[0])
                exit_price = _intrinsic_payoff(spot, float(leg.strike), leg.option_type)
            else:
                raise ValueError(
                    f"adjust: leg {leg.contract} (expiry={leg.expiry.date()}) has no chain "
                    f"row at {today.date()} and is not expired (R5 P1)."
                )
        else:
            close_side: Side = "buy" if leg.qty < 0 else "sell"
            fill = fill_model.fill(rows.iloc[0], close_side, abs(leg.qty))
            exit_price = fill.fill_price
            # Day 6.4: deduct adjust-leg retail cost from cash + realised
            cost = fill.commission + fill.tax
            if cost > 0:
                portfolio.cash -= cost
                realised -= cost
        realised += leg.qty * (exit_price - leg.entry_price) * TXO_MULTIPLIER
        cash_delta += leg.qty * exit_price * TXO_MULTIPLIER

    portfolio.cash += cash_delta
    portfolio.realised_pnl_total += realised
    # R6 F2: accumulate adjust-leg realised onto the position so the trade log
    # at final close sees the full lifecycle PnL, not just the closing legs.
    position.realised_pnl_accumulated += realised
    position.legs = surviving
    position.tags["adjusted"] = True
    position.tags["adjusted_date"] = today
    if not surviving:
        position.close_date = today
        position.realised_pnl = position.realised_pnl_accumulated
    return realised


def run_backtest(
    strategy: Strategy,
    chain_data: pd.DataFrame,
    *,
    fill_model: FillModel | None = None,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    initial_capital: float,
    strategy_name: str = "Strategy",
    mark_policy: str = "strict_mid",
) -> dict:
    """Run a daily-loop backtest and return result dict.

    Args:
        strategy: Concrete Strategy instance (e.g. IronCondor()).
        chain_data: Full enriched option chain history (raw + iv/delta/dte/underlying).
        fill_model: Concrete FillModel (Settle/Mid/WorstSide/Slippage). **Default
            ``WorstSideFillModel()``** — realistic for retail market orders;
            override for sensitivity tests only.
        start_date / end_date: ISO date strings or pd.Timestamps (inclusive).
        initial_capital: Starting capital in TWD.
        strategy_name: Human label propagated onto each opened Position.
        mark_policy: R10.12 修法 a (Codex 抓主路徑沒接 hybrid). Forwarded to
            ``portfolio.mark_to_market(mark_policy=...)`` at all 3 daily-loop
            call sites (pre_open, eod, final).
            - ``"strict_mid"`` (default, R10.10 1A): missing bid/ask raises
              (synthetic chain 100% bid/ask compatible);
            - ``"mid_with_settle_fallback"`` (R10.11 hybrid 1+3): missing
              bid/ask falls back to settle + audit metric tracked on portfolio.
              **Required for真實 TXO backtest** (Codex R10.12 證實 2024 全年
              242/242 天 fallback rate ≥20%; strict_mid 跑不到 2 天就 raise).

    Returns:
        Dict with daily_pnl / trades / metrics / final_cash / final_unrealised /
        mark_audit (R10.12 新加: per-day fallback rate DataFrame).
    """
    if fill_model is None:
        fill_model = WorstSideFillModel()
    portfolio = Portfolio(initial_capital=initial_capital)

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ValueError(f"start_date={start_date} > end_date={end_date}")

    # Chain filtered to backtest window only (PIT preserved).
    window = chain_data[(chain_data["date"] >= start) & (chain_data["date"] <= end)]
    trading_days = sorted(window["date"].unique())
    daily_pnl_records: list[tuple[pd.Timestamp, float]] = []
    # R10.12 修法 a: per-day mark audit (fallback rate / n_legs / n_fallback)
    mark_audit_records: list[tuple[pd.Timestamp, float, int, int, int, int]] = []
    prev_total = 0.0  # running cum PnL (= realised_pnl_total + unrealised; see L266)

    for raw_today in trading_days:
        today = pd.Timestamp(raw_today)
        chain_today = window[window["date"] == today].copy()
        if chain_today.empty:
            continue

        # 1. should_close on each open position (iterate snapshot of indices).
        for idx in range(len(portfolio.positions)):
            position = portfolio.positions[idx]
            if position.close_date is not None or not position.legs:
                continue
            if strategy.should_close(chain_today, position):
                portfolio.close(idx, chain_today, fill_model=fill_model)

        # 2. should_adjust on still-open positions.
        for idx in range(len(portfolio.positions)):
            position = portfolio.positions[idx]
            if position.close_date is not None or not position.legs:
                continue
            adjust_signal = strategy.should_adjust(chain_today, position)
            if adjust_signal is not None and adjust_signal.action == "adjust":
                _apply_adjust_signal(portfolio, chain_today, adjust_signal, idx, today, fill_model)

        # 3. should_open / open_position. Build state with current unrealised
        # so risk gate's loss-cap can see open-position drawdown (R5 P2).
        # R10.12 修法 a: forward mark_policy.
        unrealised_pre_open = portfolio.mark_to_market(chain_today, mark_policy=mark_policy)
        state = _build_state(portfolio, unrealised=unrealised_pre_open)
        if strategy.should_open(chain_today, state):
            signal = strategy.open_position(chain_today, state)
            if signal is not None and signal.action == "open":
                _apply_open_signal(portfolio, chain_today, signal, fill_model, today, strategy_name)

        # 4. Mark-to-market for day-end PnL.
        # Codex R7 F1: cumulative PnL must NOT use ``cash - initial_capital``
        # because cash already reflects collected premium at open while
        # ``mark_to_market`` separately reports unrealised PnL relative to entry.
        # Adding both double-counts the premium → fake gains on open day.
        # Correct invariant: cum_pnl = realised_pnl_total + unrealised_pnl.
        # R10.12 修法 a + Day 5.3: forward mark_policy + capture 4-col audit per-day.
        # Day 5.3 加 n_fallback_surface (Day 5.2 portfolio 升 4 attr; getattr default 0
        # 向後相容 — 既有 strict_mid / settle_fallback policy 不會 set 此 attr 但 default
        # 0 安全 — Pattern 14 producer/consumer 對稱).
        unrealised = portfolio.mark_to_market(chain_today, mark_policy=mark_policy)
        # R12.5 P fix (Codex audit): n_fallback_settle_3rd separate from
        # n_fallback_settle so caller can distinguish "direct settle policy"
        # from "surface degraded to settle". Backward-compat: getattr default 0
        # so existing strict_mid / settle_fallback / surface_fallback callers
        # without 3rd-layer fallback still get 0.
        mark_audit_records.append(
            (
                today,
                getattr(portfolio, "last_mark_fallback_rate", 0.0),
                getattr(portfolio, "last_mark_n_legs_marked", 0),
                getattr(portfolio, "last_mark_n_fallback_settle", 0),
                getattr(portfolio, "last_mark_n_fallback_surface", 0),
                getattr(portfolio, "last_mark_n_fallback_settle_3rd", 0),
            )
        )
        cum_pnl = portfolio.realised_pnl_total + unrealised
        daily_pnl = cum_pnl - prev_total
        prev_total = cum_pnl
        daily_pnl_records.append((today, daily_pnl))

    # Build outputs.
    if daily_pnl_records:
        daily_pnl_series = pd.Series(
            data=[v for _, v in daily_pnl_records],
            index=pd.DatetimeIndex([d for d, _ in daily_pnl_records]),
            name="daily_pnl",
        )
    else:
        daily_pnl_series = pd.Series(dtype=float, name="daily_pnl")

    # Trades: closed positions only.
    trade_rows = []
    for position in portfolio.positions:
        if position.close_date is None:
            continue
        trade_rows.append(
            {
                "open_date": position.open_date,
                "close_date": position.close_date,
                "strategy": position.strategy_name,
                "realised_pnl": position.realised_pnl,
                "n_legs_at_close": len(position.legs),
                "adjusted": bool(position.tags.get("adjusted", False)),
            }
        )
    trades_df = pd.DataFrame(trade_rows)

    cumulative = daily_pnl_series.cumsum()
    metrics = {
        # daily_pnl is in TWD → must pass initial_capital so Sharpe converts to
        # returns before subtracting risk-free rate (R10 F1).
        "sharpe": sharpe_ratio(daily_pnl_series, initial_capital=initial_capital),
        "max_drawdown": max_drawdown(cumulative, initial_capital=initial_capital),
        "win_rate": win_rate(trades_df) if not trades_df.empty else 0.0,
    }

    # R10.12 修法 a + Day 5.3 + R12.5 P fix: per-day mark audit DataFrame 升 5 col
    # schema (n_fallback_settle_3rd 為 R12.5 Codex audit 加 — 區分 surface
    # degraded to settle vs direct settle policy; 既有 R10.12 tests 用 issubset
    # 檢查不 break — backward-compat additive).
    audit_cols = [
        "fallback_rate",
        "n_legs_marked",
        "n_fallback_settle",
        "n_fallback_surface",
        "n_fallback_settle_3rd",
    ]
    if mark_audit_records:
        mark_audit_df = pd.DataFrame(
            mark_audit_records,
            columns=["date", *audit_cols],
        ).set_index("date")
    else:
        mark_audit_df = pd.DataFrame(columns=audit_cols)

    return {
        "daily_pnl": daily_pnl_series,
        "trades": trades_df,
        "metrics": metrics,
        "final_cash": portfolio.cash,
        "final_unrealised": portfolio.mark_to_market(
            window[window["date"] == trading_days[-1]] if trading_days else window,
            mark_policy=mark_policy,
        ),
        "mark_audit": mark_audit_df,  # R10.12: per-day fallback rate
    }
