"""Iron Condor (鐵兀鷹) strategy.

Phase 1 Week 2:
  - Day 2: ``open_position`` — 4-leg selection at target deltas + 3 credit
    metrics (settle / mid / worst-side) per Codex R4 + GPT-5.5 caveat.
  - Day 3: ``should_close`` (50% profit / 21-DTE stop) + ``should_adjust``
    (roll on short-strike breach into Vertical).

Greeks exposure signature:
  delta ≈ 0  (neutral)
  gamma < 0  (short)
  theta > 0  (long — main profit source)
  vega  < 0  (short — main risk)

Constructor wires in `FillModel` (defaults to WorstSide per GPT-5.5) and
`RiskConfig` (Day 4 risk wiring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from src.common.types import Order, Side
from src.common.types import StrategySignal as StrategySignalImpl
from src.options.chain import filter_by_dte, select_by_delta
from src.risk.limits import check_risk
from src.strategies.base import Strategy

if TYPE_CHECKING:
    from src.common.types import (
        FillModel,
        PortfolioState,
        Position,
        RiskConfig,
        StrategySignal,
    )

# Strict tolerance for delta-based strike selection. If chain doesn't have a
# strike within ``MAX_DELTA_DIFF`` of target, we abort the open (don't open
# a poorly-aligned IC).
MAX_DELTA_DIFF = 0.05

# +/- DTE band around ``target_dte`` for chain narrowing.
DTE_BAND = 7


class IronCondor(Strategy):
    """Delta-neutral short-premium strategy selling 4 legs across OTM wings."""

    def __init__(
        self,
        short_delta: float = 0.16,
        wing_delta: float = 0.08,
        target_dte: int = 45,
        exit_dte: int = 21,
        profit_target_pct: float = 0.50,
        fill_model: FillModel | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        """Initialise IC with delta / DTE / profit-target parameters."""
        if not 0 < wing_delta < short_delta:
            raise ValueError(
                f"wing_delta ({wing_delta}) must satisfy 0 < wing_delta < short_delta "
                f"({short_delta})"
            )
        if not 0 < profit_target_pct <= 1:
            raise ValueError(f"profit_target_pct must be in (0, 1], got {profit_target_pct}")
        if not 0 < exit_dte < target_dte:
            raise ValueError(
                f"exit_dte ({exit_dte}) must satisfy 0 < exit_dte < target_dte ({target_dte})"
            )
        self.short_delta = short_delta
        self.wing_delta = wing_delta
        self.target_dte = target_dte
        self.exit_dte = exit_dte
        self.profit_target_pct = profit_target_pct
        self.fill_model = fill_model
        self.risk_config = risk_config

    # ------------------------------------------------------------------
    # Day 2 — open
    # ------------------------------------------------------------------

    def should_open(self, chain: pd.DataFrame, state: PortfolioState) -> bool:
        """Gate: only open if no existing IC position.

        Day 4 will tighten with ``risk_config`` (max_concurrent / loss_cap).
        """
        # No double-stacking: one IC at a time in Phase 1 Week 2 (single position).
        return len(state.positions) == 0

    def open_position(self, chain: pd.DataFrame, state: PortfolioState) -> StrategySignal | None:
        """Build 4-leg IC + emit signal with 3 credit metrics in metadata.

        Steps:
          1. Filter chain to ``target_dte ± DTE_BAND``.
          2. Pick 4 legs via ``select_by_delta`` (strict mode).
          3. Compute ``settle_credit`` / ``mid_credit`` / ``worst_credit``.
          4. Compute ``max_defined_risk`` = wing_width × multiplier - worst_credit.
          5. Emit ``StrategySignal(action="open", orders=4, metadata={...})``.

        Returns ``None`` (with a hold signal would also work, but caller convention
        is None == skip) if any strike pick fails strict delta tolerance.
        """
        candidates = filter_by_dte(
            chain,
            min_dte=self.target_dte - DTE_BAND,
            max_dte=self.target_dte + DTE_BAND,
        )
        if candidates.empty:
            return None

        try:
            short_call = select_by_delta(
                candidates, +self.short_delta, "call", max_delta_diff=MAX_DELTA_DIFF
            )
            long_call = select_by_delta(
                candidates, +self.wing_delta, "call", max_delta_diff=MAX_DELTA_DIFF
            )
            short_put = select_by_delta(
                candidates, -self.short_delta, "put", max_delta_diff=MAX_DELTA_DIFF
            )
            long_put = select_by_delta(
                candidates, -self.wing_delta, "put", max_delta_diff=MAX_DELTA_DIFF
            )
        except ValueError as exc:
            # No strike within strict tolerance → skip open today.
            return StrategySignalImpl(
                action="hold",
                orders=[],
                metadata={"rejected_reason": f"strike selection failed: {exc}"},
            )

        # Wing width sanity: long call must be above short call; long put below.
        if long_call["strike"] <= short_call["strike"]:
            return None
        if long_put["strike"] >= short_put["strike"]:
            return None

        # Three credit metrics (Codex R4 + GPT-5.5 mandate).
        # Convention: short legs collect, long legs pay. Each option's mid price
        # is (bid + ask) / 2 if both finite.
        def _mid(row: pd.Series) -> float:
            return float((row["bid"] + row["ask"]) / 2.0)

        # settle credit (optimistic baseline; do NOT use for live PnL estimate)
        settle_credit = float(
            short_call["settle"] + short_put["settle"] - long_call["settle"] - long_put["settle"]
        )
        # mid credit (academic / mid-market reference)
        mid_credit = _mid(short_call) + _mid(short_put) - _mid(long_call) - _mid(long_put)
        # worst-side credit (实盘锚点: sell-at-bid, buy-at-ask)
        worst_credit = float(
            short_call["bid"] + short_put["bid"] - long_call["ask"] - long_put["ask"]
        )

        # Max defined risk per IC = wing width × multiplier − worst-side credit.
        from config.constants import TXO_MULTIPLIER

        call_wing = int(long_call["strike"] - short_call["strike"])
        put_wing = int(short_put["strike"] - long_put["strike"])
        max_wing = max(call_wing, put_wing)
        max_defined_risk = float(max_wing * TXO_MULTIPLIER - worst_credit * TXO_MULTIPLIER)

        # Build 4 Orders.
        orders = [
            self._order_from_row(short_call, side="sell"),
            self._order_from_row(long_call, side="buy"),
            self._order_from_row(short_put, side="sell"),
            self._order_from_row(long_put, side="buy"),
        ]

        candidate_signal = StrategySignalImpl(
            action="open",
            orders=orders,
            metadata={
                "settle_credit": settle_credit,
                "mid_credit": mid_credit,
                "worst_credit": worst_credit,
                "max_defined_risk_twd": max_defined_risk,
                "call_wing_pts": call_wing,
                "put_wing_pts": put_wing,
                "short_call_strike": int(short_call["strike"]),
                "long_call_strike": int(long_call["strike"]),
                "short_put_strike": int(short_put["strike"]),
                "long_put_strike": int(long_put["strike"]),
            },
        )

        # Day 4: hard-gate via RiskConfig (if provided). Failed risk check
        # converts the open signal into a hold + rejected_reason.
        if self.risk_config is not None:
            allowed, reason = check_risk(state, candidate_signal, self.risk_config)
            if not allowed:
                return StrategySignalImpl(
                    action="hold",
                    orders=[],
                    metadata={
                        "rejected_reason": f"risk check failed: {reason}",
                        # preserve original credit metrics for inspection
                        "settle_credit": settle_credit,
                        "mid_credit": mid_credit,
                        "worst_credit": worst_credit,
                        "max_defined_risk_twd": max_defined_risk,
                    },
                )

        return candidate_signal

    @staticmethod
    def _order_from_row(row: pd.Series, side: Side) -> Order:
        """Build an ``Order`` from a chain row + side."""
        contract = (
            f"TXO{pd.Timestamp(row['expiry']).strftime('%Y%m%d')}"
            f"{'C' if row['option_type'] == 'call' else 'P'}{int(row['strike'])}"
        )
        return Order(
            contract=contract,
            strike=int(row["strike"]),
            expiry=pd.Timestamp(row["expiry"]),
            option_type=row["option_type"],
            side=side,
            qty=1,
        )

    # ------------------------------------------------------------------
    # Day 3 — close / adjust
    # ------------------------------------------------------------------

    def should_close(self, chain: pd.DataFrame, position: Position) -> bool:
        """Decide whether to close the position today.

        Triggers:
          1. **DTE stop**: any leg with ``(expiry - today).days <= exit_dte``
             → force-close (avoid gamma risk near expiry).
          2. **Profit target**: current unrealised PnL ≥ ``profit_target_pct``
             of the entry credit (signed correctly: positive PnL = profit).
          3. **Stop-loss**: gated by ``risk_config.stop_loss_multiple`` if
             provided. Wired in Day 4; here we treat ``risk_config is None``
             as no stop-loss check.

        Notes:
          - Adjustment-already-rolled positions still get DTE / profit close.
          - This method is **read-only**; it does not emit a StrategySignal
            (engine builds the close signal from the position itself).
        """
        if not position.legs:
            return False
        if chain.empty:
            # R8 P3: defensive guard for public-API callers passing empty chain.
            # Engine itself never reaches here with empty chain (run_backtest
            # filters then `if chain_today.empty: continue`), but external
            # callers must not get an opaque IndexError.
            return False
        today = pd.Timestamp(chain["date"].iloc[0])

        # 1. DTE stop — any leg under exit_dte threshold.
        min_dte_remaining = min((leg.expiry - today).days for leg in position.legs)
        if min_dte_remaining <= self.exit_dte:
            return True

        # 2. Profit target — need to compute current credit (mid-based) on the
        # position's legs vs entry credit baseline. We use the metadata from the
        # Position.tags (engine populates ``entry_credit_mid``) when available.
        entry_credit = position.tags.get("entry_credit_mid")
        if entry_credit is None:
            return False  # no baseline → cannot evaluate profit target

        current_credit = self._mid_credit_for_position(chain, position)
        if current_credit is None:
            return False  # missing leg in chain (e.g. expired) — let DTE handle it

        # PnL for short-premium IC = entry_credit - current_credit.
        # (At entry: collected entry_credit; at exit: pay current_credit to close.)
        unrealised_pnl_pts = float(entry_credit) - current_credit

        # Profit target: PnL ≥ entry_credit × profit_target_pct
        profit_threshold = float(entry_credit) * self.profit_target_pct
        if unrealised_pnl_pts >= profit_threshold:
            return True

        # 3. Stop-loss — only checked if risk_config provided.
        if self.risk_config is not None:
            stop_threshold = -float(entry_credit) * self.risk_config.stop_loss_multiple
            if unrealised_pnl_pts <= stop_threshold:
                return True

        return False

    def should_adjust(self, chain: pd.DataFrame, position: Position) -> StrategySignal | None:
        """Roll a breached short wing into a single vertical (single-roll Phase 1).

        If today's spot has crossed a short strike, close the breached side
        (the 2 legs whose option_type matches the breach direction) and the
        position effectively becomes the surviving vertical. Phase 1 emits an
        adjust signal that closes the breached pair; the surviving pair stays
        open as the now-defined-risk vertical.

        Multi-roll (re-rolling the same position twice) is **out of scope**
        for Phase 1 Week 2 — we tag ``position.tags['adjusted']`` so callers
        can avoid re-triggering.
        """
        if position.tags.get("adjusted"):
            # Already adjusted once; Phase 1 doesn't roll again.
            return None
        if not position.legs:
            return None
        if chain.empty:
            # R8 P3: defensive guard symmetric to should_close.
            return None

        spot = float(chain["underlying"].iloc[0])
        # Identify short strikes (qty < 0 in our sign convention is short).
        short_call = next(
            (leg for leg in position.legs if leg.option_type == "call" and leg.qty < 0),
            None,
        )
        short_put = next(
            (leg for leg in position.legs if leg.option_type == "put" and leg.qty < 0),
            None,
        )

        breached_side: str | None = None
        if short_call is not None and spot >= short_call.strike:
            breached_side = "call"
        elif short_put is not None and spot <= short_put.strike:
            breached_side = "put"
        if breached_side is None:
            return None

        # Close the 2 legs on the breached side (short + long of that type).
        # ``qty`` carries sign; close = opposite side, abs qty.
        close_orders: list[Order] = []
        for leg in position.legs:
            if leg.option_type != breached_side:
                continue
            close_side: Side = "buy" if leg.qty < 0 else "sell"
            close_orders.append(
                Order(
                    contract=leg.contract,
                    strike=leg.strike,
                    expiry=leg.expiry,
                    option_type=leg.option_type,
                    side=close_side,
                    qty=abs(leg.qty),
                )
            )

        if not close_orders:
            return None

        return StrategySignalImpl(
            action="adjust",
            orders=close_orders,
            metadata={
                "breached_side": breached_side,
                "spot": spot,
                "surviving_side": "put" if breached_side == "call" else "call",
                "rationale": "short_strike_breach",
            },
        )

    @staticmethod
    def _mid_credit_for_position(chain: pd.DataFrame, position: Position) -> float | None:
        """Compute current mid-based credit-to-close for the position.

        Returns ``None`` if any leg is missing from the chain (e.g. legs from
        a different expiry not present today's snapshot).

        The IC's "credit to close" = sum_short(mid) - sum_long(mid). When
        equal to entry_credit → break-even; lower → profit (short premium
        decayed); higher → loss.
        """
        total = 0.0
        for leg in position.legs:
            row = chain[
                (chain["strike"] == leg.strike)
                & (chain["option_type"] == leg.option_type)
                & (chain["expiry"] == leg.expiry)
            ]
            if row.empty:
                return None
            mid = float((row["bid"].iloc[0] + row["ask"].iloc[0]) / 2.0)
            # short legs (qty<0) contribute +mid (cost to buy back);
            # long legs (qty>0) contribute -mid (received from selling).
            # Credit-to-close = -1 × (cost to buy back shorts - sell longs)
            # → short adds +mid; long adds -mid. Wait: total cost to close =
            # buy back shorts at mid + sell longs at mid. credit_to_close =
            # net cash to leave = sum_short(mid) - sum_long(mid).
            if leg.qty < 0:
                total += mid
            else:
                total -= mid
        return total
