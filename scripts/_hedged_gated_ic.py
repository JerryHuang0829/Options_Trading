"""HedgedGatedIronCondor — GatedIC + Long calendar ATM call hedge attach.

Phase 1 Week 7 Quick A (post-Day-7.0 feasibility audit pivot):

  - 5-cohort multi-expiry ladder 不可行 (Day 7.0 audit: mean 1.44 unique
    expiry/day in DTE 21-63; max 2; pct days >=3 = 0.0%).
  - Quick A 退化 = 1-cohort hedged IC: 1 IC + 1 calendar hedge per opening.
  - 6-leg single Position structure (no engine `_apply_open_signal` change).

Hedge mode = calendar (call-only):
  - Day 7.0 audit settle-based estimate: median 1.35x IC credit (acceptable)
  - Straddle median 8.36x → NO-GO, not implemented in Quick A.

Position structure (1 Position with 6 legs):
  IC legs (4):
    - Sell short_call (delta 0.16) — 4-leg builder from IronCondor
    - Buy  long_call  (delta 0.08)
    - Sell short_put  (delta -0.16)
    - Buy  long_put   (delta -0.08)
  Hedge legs (2):
    - Sell front ATM call  (expiry = IC expiry)
    - Buy  back  ATM call  (expiry = IC expiry + hedge_dte_offset)

Lifecycle:
  - Open: super().open_position 4 IC legs + try attach 2 hedge legs
    - Success: 6-leg signal; engine creates 1 Position with 6 legs.
    - Hedge build fail (no back expiry / no ATM strike): degrade to 4-leg IC,
      record reject reason "hedge_attach_fail", IC still opens.
  - Close (inherited GatedIC): DTE stop on min(leg.expiry - today). Hedge
    back leg DTE (~75) > IC DTE (~45) so min DTE is from IC; entire 6-leg
    Position closes when IC reaches exit_dte. Hedge legs still have remaining
    time value → engine fill_model closes them at mid/settle.
  - Adjust (DISABLED for hedged variant): see _hedge_adjust_disabled below.

Quick A trade-offs:
  1. should_adjust returns None — IC short_call dispatch can't disambiguate
     IC short_call (OTM) from hedge front ATM short call (same option_type +
     qty<0 + same expiry). Disable adjust accepted (Phase 1 single-roll only).
  2. Hedge legs share IC's DTE-based exit → some hedge value left at close.
  3. ATM strike rounding ±25 pts on 50-pt grid (TXO_STRIKE_GRID).

Pattern 14 producer/consumer parity: GatedIC `_record_reject` accumulator
extended for hedge_attach path (date / path='hedge_attach' / reason / leg).
"""

from __future__ import annotations

import pandas as pd

from scripts._gated_strategy import GatedIronCondor
from src.common.types import StrategySignal as StrategySignalImpl
from src.strategies.calendar_hedge import build_long_calendar_atm_call


class HedgedGatedIronCondor(GatedIronCondor):
    """GatedIronCondor + 2-leg long calendar ATM call hedge attach.

    Same constructor signature as GatedIronCondor + 1 new kwarg:
      hedge_dte_offset: int — back leg DTE = IC DTE + offset (default 30)

    Hedge mode locked to "calendar" for Quick A; future plan升級加 mode flag.
    """

    def __init__(self, *args, hedge_dte_offset: int = 30, **kwargs):
        super().__init__(*args, **kwargs)
        if hedge_dte_offset <= 0:
            raise ValueError(f"hedge_dte_offset must be > 0, got {hedge_dte_offset}")
        self.hedge_dte_offset = hedge_dte_offset
        # Counters for monitor instrumentation
        self.hedge_attach_count = 0
        self.hedge_fail_count = 0

    def open_position(self, chain, state):
        """Pass through GatedIC.open_position; if action='open', attach hedge.

        Returns:
            - Same signal types as super (None / hold / open).
            - When action='open' AND hedge build succeeds: orders extended with
              2 calendar legs; metadata adds hedge_attached=True + hedge details.
            - When hedge build fails: IC still opens (4 legs), metadata records
              hedge_attached=False + reject reason.
        """
        signal = super().open_position(chain, state)
        if signal is None or signal.action != "open":
            return signal

        # IC opened successfully — try attach hedge.
        # signal.orders[0] is short_call (first IC order per IronCondor:168-173).
        ic_expiry = pd.Timestamp(signal.orders[0].expiry)
        if "underlying" not in chain.columns or chain.empty:
            self.hedge_fail_count += 1
            self._record_reject(chain, "hedge_attach", "no_underlying_or_empty_chain")
            return self._mark_hedge_failed(signal)
        underlying = float(chain["underlying"].iloc[0])

        try:
            hedge_orders = build_long_calendar_atm_call(
                chain,
                ic_expiry=ic_expiry,
                underlying=underlying,
                hedge_dte_offset=self.hedge_dte_offset,
            )
        except ValueError as e:
            self.hedge_fail_count += 1
            self._record_reject(chain, "hedge_attach", f"hedge_build_fail: {e}")
            return self._mark_hedge_failed(signal)

        # Hedge build succeeded — extend signal with 2 hedge orders.
        # Pattern 17 hollow PASS guard: also check execution gate on hedge legs.
        for hedge_order in hedge_orders:
            row = self._lookup_chain_row(chain, hedge_order)
            if row is None:
                self.hedge_fail_count += 1
                self._record_reject(
                    chain,
                    "hedge_attach",
                    f"hedge_chain_row_missing: {hedge_order.contract}",
                    leg=hedge_order.contract,
                )
                return self._mark_hedge_failed(signal)
            need_can_buy = hedge_order.side == "buy"
            need_can_sell = hedge_order.side == "sell"
            can_buy = bool(row.get("can_buy", False))
            can_sell = bool(row.get("can_sell", False))
            if need_can_buy and not can_buy:
                self.hedge_fail_count += 1
                self._record_reject(
                    chain,
                    "hedge_attach",
                    f"hedge_gate_fail: buy {hedge_order.contract} (ask NaN)",
                    leg=hedge_order.contract,
                )
                return self._mark_hedge_failed(signal)
            if need_can_sell and not can_sell:
                self.hedge_fail_count += 1
                self._record_reject(
                    chain,
                    "hedge_attach",
                    f"hedge_gate_fail: sell {hedge_order.contract} (bid NaN)",
                    leg=hedge_order.contract,
                )
                return self._mark_hedge_failed(signal)

        # Build new signal with 6 orders + hedge metadata.
        new_orders = list(signal.orders) + list(hedge_orders)
        new_metadata = dict(signal.metadata)
        new_metadata["hedge_attached"] = True
        new_metadata["hedge_mode"] = "calendar"
        new_metadata["hedge_n_legs"] = len(hedge_orders)
        new_metadata["hedge_dte_offset"] = self.hedge_dte_offset
        new_metadata["hedge_front_contract"] = hedge_orders[0].contract
        new_metadata["hedge_back_contract"] = hedge_orders[1].contract
        self.hedge_attach_count += 1

        return StrategySignalImpl(
            action="open",
            orders=new_orders,
            metadata=new_metadata,
        )

    def should_adjust(self, chain, position):
        """DISABLED for HedgedGatedIC.

        Reason: IronCondor.should_adjust identifies short_call by
        `option_type=='call' and qty<0`. Hedge front leg is also a short call
        with qty<0 → adjust would close hedge legs incorrectly when IC short
        strike breached. Quick A scope: accept worse breach handling for
        simplicity (Phase 1 was single-roll only anyway, value-add limited).

        Future升級 (Phase 1 Week 8+): tag legs with leg_type ('ic_primary' /
        'hedge_calendar') and filter adjust by tag.
        """
        return

    @staticmethod
    def _mark_hedge_failed(signal):
        """Return same signal with hedge_attached=False metadata; IC still opens."""
        new_metadata = dict(signal.metadata)
        new_metadata["hedge_attached"] = False
        return StrategySignalImpl(
            action=signal.action,
            orders=signal.orders,
            metadata=new_metadata,
        )
