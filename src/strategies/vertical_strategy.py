"""Vertical (credit spread) strategy with IV skew gate (Week 6 Day 6.0).

Phase 1 Week 6 baseline 對比 IronCondor — 用 Vertical credit spread (2 leg)
跑同 chain / 同 risk_config / 同 mark_policy 看是否比 IC 有 alpha.

開倉 signal (R12 拍板選 C IV skew gated):
  - 25-delta put IV - 25-delta call IV = skew
  - skew > +SKEW_THRESHOLD → bull put spread (押多, 賺 put fat tail normalize)
  - skew < -SKEW_THRESHOLD → bear call spread (押空, 賺 call fat tail normalize)
  - |skew| <= SKEW_THRESHOLD → 不開倉 (skew 不顯著)

Pro option trading 文獻: skew trade 是 vol surface alpha 的具體應用; 與 IC
的「賺 IV crush」alpha 不同, 所以兩 strategy 對比有 academic 意義 (R12 Plan
3 baseline 紀律).

Greeks exposure (與 IC 對比):
  IC:        delta ≈ 0; gamma < 0; theta > 0; vega < 0  (賺 IV crush)
  Vertical:  delta ≠ 0 (有方向 — bull/bear); gamma < 0; theta > 0; vega < 0

PIT correctness: skew 用當日 chain 算 (delta col 已 enriched), 不需 lookback;
與 IC vanilla 一樣 day-bound, 沒 look-ahead leakage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from src.common.types import Order, Side
from src.common.types import StrategySignal as StrategySignalImpl
from src.options.chain import filter_by_dte, select_by_delta
from src.strategies.base import Strategy

if TYPE_CHECKING:
    from src.common.types import (
        FillModel,
        PortfolioState,
        Position,
        RiskConfig,
        StrategySignal,
    )

# Strict tolerance for delta-based strike selection.
MAX_DELTA_DIFF = 0.05
DTE_BAND = 7

# Default IV skew threshold (1.0% = 100 bps; 量化文獻常用)
DEFAULT_SKEW_THRESHOLD = 0.01


class VerticalStrategy(Strategy):
    """Credit spread strategy (bull put / bear call) gated by 25-delta IV skew.

    Constructor params:
        short_delta: short leg target delta (default 0.25 — typical Vertical)
        wing_delta: wing leg target delta (default 0.10)
        target_dte: target days-to-expiry at open
        exit_dte: close when DTE drops below this
        profit_target_pct: close at this fraction of max profit
        skew_threshold: |25d-put-IV - 25d-call-IV| > threshold → 開倉 signal active
        fill_model / risk_config: forwarded
    """

    def __init__(
        self,
        short_delta: float = 0.25,
        wing_delta: float = 0.10,
        target_dte: int = 45,
        exit_dte: int = 21,
        profit_target_pct: float = 0.50,
        skew_threshold: float = DEFAULT_SKEW_THRESHOLD,
        fill_model: FillModel | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
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
        if skew_threshold < 0:
            raise ValueError(f"skew_threshold must be >= 0, got {skew_threshold}")
        self.short_delta = short_delta
        self.wing_delta = wing_delta
        self.target_dte = target_dte
        self.exit_dte = exit_dte
        self.profit_target_pct = profit_target_pct
        self.skew_threshold = skew_threshold
        self.fill_model = fill_model
        self.risk_config = risk_config

    # ------------------------------------------------------------------
    # IV skew signal
    # ------------------------------------------------------------------

    def _compute_skew(self, chain: pd.DataFrame) -> tuple[float, str | None]:
        """Compute 25-delta IV skew + signal direction.

        Returns:
            (skew, signal) where:
              skew = put_iv - call_iv at 25-delta (per side)
              signal ∈ {'bull_put', 'bear_call', None}

        Returns (NaN, None) if 25-delta legs not selectable in chain.
        """
        candidates = filter_by_dte(
            chain,
            min_dte=self.target_dte - DTE_BAND,
            max_dte=self.target_dte + DTE_BAND,
        )
        if candidates.empty:
            return (float("nan"), None)
        try:
            put_25d = select_by_delta(candidates, -0.25, "put", max_delta_diff=MAX_DELTA_DIFF)
            call_25d = select_by_delta(candidates, +0.25, "call", max_delta_diff=MAX_DELTA_DIFF)
        except ValueError:
            return (float("nan"), None)

        put_iv = float(put_25d.get("iv", float("nan")))
        call_iv = float(call_25d.get("iv", float("nan")))
        if not (pd.notna(put_iv) and pd.notna(call_iv)):
            return (float("nan"), None)

        skew = put_iv - call_iv
        if skew > self.skew_threshold:
            return (skew, "bull_put")
        if skew < -self.skew_threshold:
            return (skew, "bear_call")
        return (skew, None)

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def should_open(self, chain: pd.DataFrame, state: PortfolioState) -> bool:
        """Open if no existing position AND IV skew signal active."""
        if len(state.positions) > 0:
            return False
        _, signal = self._compute_skew(chain)
        return signal is not None

    def open_position(self, chain: pd.DataFrame, state: PortfolioState) -> StrategySignal | None:
        """Build 2-leg credit spread per skew signal direction.

        Returns:
            StrategySignal(action="open", orders=[short, long], metadata={
                'signal': 'bull_put' | 'bear_call',
                'skew': float (put_iv - call_iv at 25d),
                'settle_credit' / 'mid_credit' / 'worst_credit',
                'max_defined_risk_twd',
            })
        """
        skew, signal = self._compute_skew(chain)
        if signal is None:
            return None

        candidates = filter_by_dte(
            chain,
            min_dte=self.target_dte - DTE_BAND,
            max_dte=self.target_dte + DTE_BAND,
        )

        try:
            if signal == "bull_put":
                # Bull put: sell short put + buy long put (lower)
                short = select_by_delta(
                    candidates, -self.short_delta, "put", max_delta_diff=MAX_DELTA_DIFF
                )
                long = select_by_delta(
                    candidates, -self.wing_delta, "put", max_delta_diff=MAX_DELTA_DIFF
                )
                # Wing sanity: long put strike must be lower than short put
                if long["strike"] >= short["strike"]:
                    return None
                option_type = "put"
            else:  # bear_call
                # Bear call: sell short call + buy long call (higher)
                short = select_by_delta(
                    candidates, +self.short_delta, "call", max_delta_diff=MAX_DELTA_DIFF
                )
                long = select_by_delta(
                    candidates, +self.wing_delta, "call", max_delta_diff=MAX_DELTA_DIFF
                )
                if long["strike"] <= short["strike"]:
                    return None
                option_type = "call"
        except ValueError as exc:
            return StrategySignalImpl(
                action="hold",
                orders=[],
                metadata={"rejected_reason": f"strike selection failed: {exc}"},
            )

        # Three credit metrics (per IC convention)
        def _mid(row: pd.Series) -> float:
            return float((row["bid"] + row["ask"]) / 2.0)

        settle_credit = float(short["settle"] - long["settle"])
        mid_credit = _mid(short) - _mid(long)
        worst_credit = float(short["bid"] - long["ask"])

        from config.constants import TXO_MULTIPLIER

        wing_width = abs(int(long["strike"] - short["strike"]))
        max_defined_risk = float(wing_width * TXO_MULTIPLIER - worst_credit * TXO_MULTIPLIER)

        orders = [
            self._order_from_row(short, side="sell"),
            self._order_from_row(long, side="buy"),
        ]

        return StrategySignalImpl(
            action="open",
            orders=orders,
            metadata={
                "signal": signal,
                "skew": float(skew),
                "option_type": option_type,
                "settle_credit": settle_credit,
                "mid_credit": mid_credit,
                "worst_credit": worst_credit,
                "max_defined_risk_twd": max_defined_risk,
            },
        )

    def should_close(self, chain: pd.DataFrame, position: Position) -> bool:
        """Close if profit target hit OR DTE below exit threshold.

        Mirrors IC.should_close logic.
        """
        if not position.legs:
            return False
        today = pd.Timestamp(chain["date"].iloc[0])
        first_leg = position.legs[0]
        days_to_expiry = (first_leg.expiry - today).days
        if days_to_expiry <= self.exit_dte:
            return True
        # Profit target: 50% of mid_credit by default (entry_credit_mid in tags)
        entry_credit_mid = float(position.tags.get("entry_credit_mid", 0.0))
        if entry_credit_mid <= 0:
            return False
        # Compute current mark — best-effort
        from src.backtest.portfolio import _mid_price_with_basis, _row_for_leg

        current_credit = 0.0
        for leg in position.legs:
            row = _row_for_leg(chain, leg)
            if row is None:
                return False
            try:
                price, _ = _mid_price_with_basis(row, fallback_mode="strict")
            except ValueError:
                return False
            # Short legs collect; long legs pay
            current_credit += -leg.qty * price
        # Profit captured = entry_credit - current_credit (smaller is better for short premium)
        captured = entry_credit_mid - current_credit
        return captured >= self.profit_target_pct * entry_credit_mid

    def should_adjust(self, chain: pd.DataFrame, position: Position) -> StrategySignal | None:
        """No adjust path for Vertical Phase 1 (hold to close / exit_dte / profit target)."""
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _order_from_row(row: pd.Series, side: Side) -> Order:
        """Construct an Order from a chain row + side ('sell' / 'buy'). Mirror IC."""
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
