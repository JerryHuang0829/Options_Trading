"""GatedIronCondor — IronCondor + R10.10 3ii side-specific 4-leg execution gate.

D-soft Day 6 (plan v6 §4) + Codex R11.2 P close-side gate addition.

Gate policy (R10.10 3ii pure execution gate):

  Open path (`open_position`):
    - short_call / short_put 是 sell legs → 需要 row['can_sell']==True (= bid notna)
    - long_call  / long_put  是 buy  legs → 需要 row['can_buy']==True  (= ask notna)
    - 4-leg 任一 fail → super().open_position 的 open signal 改 hold +
      rejected_reason='execution_gate_fail: <leg_name>'

  Close path (`should_close` — Codex R11.2 P 修法):
    - parent says True → 額外檢查每個 leg 是否能 close
    - leg.qty < 0 (short, sell-to-open) → close = buy back → 需要 can_buy
    - leg.qty > 0 (long, buy-to-open) → close = sell to close → 需要 can_sell
    - 任一 leg gate fail → return False (defer close, 等下日再試)
    - 注意：defer 可能造成 position 卡死到 expiry。Week 4-5 vol surface 加
      mid_with_surface_fallback policy 後 close 走 model price 不再受真 quote 限制

  Adjust path (`should_adjust` — Codex R11.2 P 修法):
    - parent emits adjust signal (close 2 breached legs) → 檢查 orders 的 buy/sell
      side 對應的 chain row 是否 can_buy / can_sell
    - 任一 fail → return None (defer adjust)

Day 6 dummy run 下 synthetic chain 100% bid/ask → 4-leg gate 永不 fail，gate
是 pass-through。真資料 (Codex R10.12 實證 2024 ~60% missing quote) 才會
頻繁 fail；defer 不是好設計但比 raise 好，Week 4-5 vol surface 加 model_price
fallback 後 mark_policy='mid_with_surface_fallback' 永不 NaN.
"""

from __future__ import annotations

import pandas as pd

from src.common.types import StrategySignal as StrategySignalImpl
from src.strategies.iron_condor import IronCondor


class GatedIronCondor(IronCondor):
    """IronCondor + side-specific can_buy/can_sell gate on the 4 selected legs.

    Same constructor signature as IronCondor (no new params). Override 3
    methods: open_position / should_close / should_adjust. Day 6 dummy 路徑
    + Codex R11.2 P close-side gate. Week 4-5 vol surface 完成後 close path
    可改走 model price 不再走 defer 邏輯.

    Day 6.3 加 rejected_reasons accumulator (Pattern 17 hollow PASS detector
    紀律): 每次 open / close / adjust gate fail 紀錄 (date, path, reason)；
    walk_forward fold-level monitor 可 aggregate 看 reject 真實分佈。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # date / path ('open' | 'close' | 'adjust') / reason / leg
        self.rejected_reasons: list[dict] = []

    def get_rejected_reasons(self) -> pd.DataFrame:
        """Snapshot accumulated rejections as a DataFrame for monitor."""
        if not self.rejected_reasons:
            return pd.DataFrame(columns=["date", "path", "reason", "leg"])
        return pd.DataFrame(self.rejected_reasons)

    def _record_reject(
        self,
        chain: pd.DataFrame,
        path: str,
        reason: str,
        leg: str = "",
    ) -> None:
        date = pd.Timestamp(chain["date"].iloc[0]) if not chain.empty else pd.NaT
        self.rejected_reasons.append({"date": date, "path": path, "reason": reason, "leg": leg})

    def open_position(self, chain, state):
        """Pass through IronCondor.open_position; reject if any of 4 legs lacks
        the required side liquidity (can_buy for buys, can_sell for sells).

        Returns:
            - None: super 回 None (e.g. empty DTE candidates / wing width fail) → pass through
            - StrategySignal action='hold': super 已 hold (strike select fail) OR gate fail → pass through
            - StrategySignal action='open': 4 legs all liquid → unchanged signal
        """
        signal = super().open_position(chain, state)
        if signal is None or signal.action != "open":
            return signal

        # 4-leg side-specific gate. orders[i] = Order with side ('buy'|'sell') + strike + option_type.
        # Map each order back to its chain row to read can_buy/can_sell.
        for order in signal.orders:
            row = self._lookup_chain_row(chain, order)
            if row is None:
                # 找不到對應 chain row 不該發生（同一 chain 來的 order）；防禦性 raise
                raise RuntimeError(
                    f"GatedIronCondor: order {order.contract} side={order.side} "
                    f"has no matching chain row at open"
                )
            need_can_buy = order.side == "buy"
            need_can_sell = order.side == "sell"
            can_buy = bool(row.get("can_buy", False))
            can_sell = bool(row.get("can_sell", False))
            if need_can_buy and not can_buy:
                reason = f"execution_gate_fail: buy leg {order.contract} (ask NaN)"
                self._record_reject(chain, "open", reason, leg=order.contract)
                return self._reject(signal, reason)
            if need_can_sell and not can_sell:
                reason = f"execution_gate_fail: sell leg {order.contract} (bid NaN)"
                self._record_reject(chain, "open", reason, leg=order.contract)
                return self._reject(signal, reason)
        return signal

    def should_close(self, chain, position):
        """Pass through IronCondor.should_close; defer if any leg lacks close-side liquidity.

        Close-side gate (R10.10 3ii / Codex R11.2 P):
          - leg.qty < 0 (short) → close = buy → need can_buy (ask notna)
          - leg.qty > 0 (long)  → close = sell → need can_sell (bid notna)

        Codex R11.3 P3 expiry-day rescue:
          - 任意 leg DTE <= 0 (today >= expiry) → portfolio.close 走
            _intrinsic_payoff (R5 P1 修法已實作), 不需 bid/ask → skip gate.
          - 沒這 rescue, position 在 expiry day NaN bid/ask 會被 defer 永遠
            close 不掉 → final_unrealised 永遠 inflate (Codex R11.3 toy 實證).

        If parent says close but any non-expired leg fails gate → return False (defer).
        """
        if not super().should_close(chain, position):
            return False
        if chain.empty:
            return False
        # R11.4 P3 修法 (Codex multi-day chain 攻擊防呆):
        # engine.run_backtest 每日切片 single-day chain 餵給 strategy；外部
        # caller 若誤傳 multi-day chain，today=chain.iloc[0] 會依第一列日期決定
        # gate 行為，silent 偏差。public API 強制 single-day 契約。
        unique_dates = chain["date"].unique()
        if len(unique_dates) > 1:
            raise ValueError(
                f"GatedIronCondor.should_close: chain must be single-day "
                f"(got {len(unique_dates)} unique dates: {sorted(unique_dates)[:3]}...). "
                f"Caller is engine.run_backtest with daily slice; external callers "
                f"must pre-filter chain to today's rows."
            )
        today = pd.Timestamp(chain["date"].iloc[0])
        for leg in position.legs:
            # Expiry-day rescue (R11.3 P3): leg 已到期 → portfolio.close 走 intrinsic payoff
            if today >= leg.expiry:
                continue
            row = self._lookup_leg_row(chain, leg)
            leg_id = f"{leg.option_type[0].upper()}{leg.strike:.0f}@{leg.expiry.date()}"
            if row is None:
                self._record_reject(chain, "close", "leg_row_missing_in_chain", leg=leg_id)
                return False  # leg row not in today's chain (e.g. delisted) → defer
            need_can_buy = leg.qty < 0  # short → close = buy
            need_can_sell = leg.qty > 0  # long → close = sell
            can_buy = bool(row.get("can_buy", False))
            can_sell = bool(row.get("can_sell", False))
            if need_can_buy and not can_buy:
                self._record_reject(chain, "close", "close_gate_fail: buy-back ask NaN", leg=leg_id)
                return False
            if need_can_sell and not can_sell:
                self._record_reject(
                    chain, "close", "close_gate_fail: sell-to-close bid NaN", leg=leg_id
                )
                return False
        return True

    def should_adjust(self, chain, position):
        """Pass through IronCondor.should_adjust; defer if any close order lacks liquidity."""
        signal = super().should_adjust(chain, position)
        if signal is None or signal.action != "adjust":
            return signal
        for order in signal.orders:
            row = self._lookup_chain_row(chain, order)
            if row is None:
                self._record_reject(chain, "adjust", "leg_row_missing_in_chain", leg=order.contract)
                return None  # leg row missing → defer
            need_can_buy = order.side == "buy"
            need_can_sell = order.side == "sell"
            can_buy = bool(row.get("can_buy", False))
            can_sell = bool(row.get("can_sell", False))
            if need_can_buy and not can_buy:
                self._record_reject(
                    chain,
                    "adjust",
                    "adjust_gate_fail: buy ask NaN",
                    leg=order.contract,
                )
                return None
            if need_can_sell and not can_sell:
                self._record_reject(
                    chain,
                    "adjust",
                    "adjust_gate_fail: sell bid NaN",
                    leg=order.contract,
                )
                return None
        return signal

    @staticmethod
    def _lookup_leg_row(chain: pd.DataFrame, leg) -> pd.Series | None:
        """Find chain row matching a Position leg by (expiry, strike, option_type)."""
        match = chain[
            (chain["expiry"] == leg.expiry)
            & (chain["strike"] == leg.strike)
            & (chain["option_type"] == leg.option_type)
        ]
        if match.empty:
            return None
        return match.iloc[0]

    @staticmethod
    def _lookup_chain_row(chain: pd.DataFrame, order) -> pd.Series | None:
        """Find the chain row matching order.contract by (expiry, strike, option_type).

        Order.contract format from IronCondor._order_from_row:
            'TXO<YYYYMMDD><C|P><strike>'
        """
        contract = order.contract
        # Parse: TXO + 8 digits expiry + 1 letter (C|P) + strike int
        if not contract.startswith("TXO") or len(contract) < 13:
            return None
        expiry_str = contract[3:11]
        cp = contract[11]
        strike = int(contract[12:])
        try:
            expiry = pd.Timestamp(f"{expiry_str[:4]}-{expiry_str[4:6]}-{expiry_str[6:8]}")
        except (ValueError, TypeError):
            return None
        opt_type = "call" if cp == "C" else "put" if cp == "P" else None
        if opt_type is None:
            return None
        match = chain[
            (chain["expiry"] == expiry)
            & (chain["strike"] == strike)
            & (chain["option_type"] == opt_type)
        ]
        if match.empty:
            return None
        return match.iloc[0]

    @staticmethod
    def _reject(signal, reason: str):
        """Convert open signal → hold + rejected_reason; preserve credit metadata."""
        meta = dict(signal.metadata)
        meta["rejected_reason"] = reason
        return StrategySignalImpl(action="hold", orders=[], metadata=meta)
