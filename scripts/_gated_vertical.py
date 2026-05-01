"""GatedVerticalStrategy — VerticalStrategy + R10.10 3ii 2-leg execution gate.

Week 6 Day 6.0 — mirror `_gated_strategy.py` 的 R10.10 紀律, 套到 Vertical
(2-leg credit spread) 上.

Gate policy (R10.10 3ii pure execution gate):

  Open path (`open_position`):
    - short leg 是 sell → 需要 row['can_sell']==True (= bid notna)
    - long  leg 是 buy  → 需要 row['can_buy']==True  (= ask notna)
    - 任一 fail → super().open_position 的 open signal 改 hold +
      rejected_reason='execution_gate_fail: <leg_name>'

  Close path (`should_close`): 與 GatedIC 相同 R10.10 close gate.
  Adjust path: VerticalStrategy.should_adjust 永遠 None, 不需 gate.
"""

from __future__ import annotations

import pandas as pd

from src.common.types import StrategySignal as StrategySignalImpl
from src.strategies.vertical_strategy import VerticalStrategy


class GatedVerticalStrategy(VerticalStrategy):
    """VerticalStrategy + side-specific can_buy/can_sell gate on 2 legs.

    Same constructor as VerticalStrategy. Override open_position / should_close.
    """

    def open_position(self, chain, state):
        """Pass through; reject if either leg lacks required side liquidity."""
        signal = super().open_position(chain, state)
        if signal is None or signal.action != "open":
            return signal

        for order in signal.orders:
            row = self._lookup_chain_row(chain, order)
            if row is None:
                raise RuntimeError(
                    f"GatedVerticalStrategy: order {order.contract} side={order.side} "
                    f"has no matching chain row at open"
                )
            need_can_buy = order.side == "buy"
            need_can_sell = order.side == "sell"
            can_buy = bool(row.get("can_buy", False))
            can_sell = bool(row.get("can_sell", False))
            if need_can_buy and not can_buy:
                return self._reject(
                    signal, f"execution_gate_fail: buy leg {order.contract} (ask NaN)"
                )
            if need_can_sell and not can_sell:
                return self._reject(
                    signal, f"execution_gate_fail: sell leg {order.contract} (bid NaN)"
                )
        return signal

    def should_close(self, chain, position):
        """Parent says close → check each leg's close-side liquidity.

        - leg.qty < 0 (short → close = buy back) → 需 can_buy
        - leg.qty > 0 (long → close = sell to close) → 需 can_sell
        任一 fail → return False (defer close).
        """
        if not super().should_close(chain, position):
            return False
        for leg in position.legs:
            row = self._lookup_chain_row_for_leg(chain, leg)
            if row is None:
                return False
            if leg.qty < 0 and not bool(row.get("can_buy", False)):
                return False
            if leg.qty > 0 and not bool(row.get("can_sell", False)):
                return False
        return True

    # ------------------------------------------------------------------
    # Helpers (mirror _gated_strategy.py)
    # ------------------------------------------------------------------

    def _lookup_chain_row(self, chain: pd.DataFrame, order) -> pd.Series | None:
        rows = chain[
            (chain["strike"] == order.strike)
            & (chain["expiry"] == order.expiry)
            & (chain["option_type"] == order.option_type)
        ]
        if rows.empty:
            return None
        return rows.iloc[0]

    def _lookup_chain_row_for_leg(self, chain: pd.DataFrame, leg) -> pd.Series | None:
        rows = chain[
            (chain["strike"] == leg.strike)
            & (chain["expiry"] == leg.expiry)
            & (chain["option_type"] == leg.option_type)
        ]
        if rows.empty:
            return None
        return rows.iloc[0]

    def _reject(self, signal, reason: str):
        return StrategySignalImpl(
            action="hold",
            orders=[],
            metadata={**signal.metadata, "rejected_reason": reason},
        )
