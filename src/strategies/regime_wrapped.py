"""RegimeWrappedStrategy — 通用 regime gate wrapper for any Strategy.

Week 6 Day 6.0 — Pro 學術紀律 ablation study (Vanilla / IV percentile / HMM
三版對比). 此 wrapper 可包任何 Strategy (GatedIC / GatedVerticalStrategy / 等)
+ 任何 RegimeGate (IVPercentileGate / HMMRegimeGate / future) 形成 X×Y matrix.

Wrap 行為:
    should_open = base.should_open AND regime_gate.is_active(today, returns_history)
    open_position / should_close / should_adjust 全 delegate 給 base
        (regime gate 只控制 open 時機, 持倉中 close/adjust 仍由 base 決定)

PIT Correctness (R10.5 P2):
    returns_history 由 caller 注入 (engine / walk_forward); gate 只驗 date <=
    today 的歷史. **不可** 在 wrapper 內 fetch — separation of concerns.

Vanilla baseline (regime_gate=None):
    RegimeWrappedStrategy(base, regime_gate=None) ≡ base (pass-through).
    Used in ablation matrix as the no-gate baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from src.options.regime_gate import RegimeGate
from src.strategies.base import Strategy

if TYPE_CHECKING:
    from src.common.types import PortfolioState, Position, StrategySignal


class RegimeWrappedStrategy(Strategy):
    """Wrap any Strategy with optional RegimeGate.

    Vanilla baseline: regime_gate=None → pass-through.
    Gated: regime_gate=IVPercentileGate(...) or HMMRegimeGate(...).
    """

    def __init__(
        self,
        base: Strategy,
        regime_gate: RegimeGate | None,
        returns_history: pd.Series | None = None,
    ) -> None:
        """
        Args:
            base: underlying Strategy (GatedIronCondor / GatedVerticalStrategy / ...)
            regime_gate: RegimeGate instance, or None for vanilla pass-through.
            returns_history: pd.Series of underlying log returns indexed by date
                (e.g. TAIEX). Required if regime_gate is not None. Caller
                MUST ensure all index dates <= backtest current date at gate
                evaluation (PIT enforcement is here as defense; primary at
                walk_forward layer).
        """
        if regime_gate is not None and returns_history is None:
            raise ValueError(
                "RegimeWrappedStrategy: regime_gate provided but returns_history is None"
            )
        self.base = base
        self.regime_gate = regime_gate
        self.returns_history = returns_history

    def should_open(self, chain: pd.DataFrame, state: PortfolioState) -> bool:
        """Open only if base says open AND regime_gate is active (or None).

        Vanilla path (regime_gate=None) just calls base.should_open.
        """
        if not self.base.should_open(chain, state):
            return False
        if self.regime_gate is None:
            return True  # vanilla pass-through
        # PIT: gate evaluates at chain's first date
        today = pd.Timestamp(chain["date"].iloc[0])
        if self.returns_history is None:
            return False  # defensive
        return self.regime_gate.is_active(today, self.returns_history)

    def open_position(self, chain: pd.DataFrame, state: PortfolioState) -> StrategySignal | None:
        """Delegate to base.open_position. should_open already gated this call."""
        return self.base.open_position(chain, state)

    def should_close(self, chain: pd.DataFrame, position: Position) -> bool:
        """Delegate. Regime gate only controls open, not close."""
        return self.base.should_close(chain, position)

    def should_adjust(self, chain: pd.DataFrame, position: Position) -> StrategySignal | None:
        """Delegate. Regime gate only controls open, not adjust."""
        return self.base.should_adjust(chain, position)
