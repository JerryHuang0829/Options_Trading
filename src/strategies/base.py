"""Strategy abstract base class.

Defines the contract every concrete strategy (Iron Condor, Vertical, future
Calendar) must implement. The backtest engine consumes this interface
without caring about the specific strategy logic.

**Week 2 Day 1 refactor**: replaces dict-based interface with typed
``StrategySignal`` / ``PortfolioState`` / ``Position`` from ``src.common.types``.
Codex R4 + GPT-5.5 共識：dict 邊界產生 silent bugs，type-checked dataclasses
讓 strategy ↔ engine 邊界顯式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from src.common.types import PortfolioState, Position, StrategySignal


class Strategy(ABC):
    """Abstract strategy interface consumed by backtest engine."""

    @abstractmethod
    def should_open(self, chain: pd.DataFrame, state: PortfolioState) -> bool:
        """Return True if an open signal fires on this trading day.

        Args:
            chain: Enriched option chain for the current trading day
                (must include `iv` / `delta` / `dte` / `underlying`).
            state: Read-mostly portfolio snapshot at this point in time.
        """
        raise NotImplementedError

    @abstractmethod
    def open_position(self, chain: pd.DataFrame, state: PortfolioState) -> StrategySignal | None:
        """Return open signal (or None to skip the day).

        On success: ``StrategySignal(action="open", orders=[...], metadata={...})``.
        Metadata typically carries per-strategy context (e.g. IC's three credit
        metrics: settle_credit / mid_credit / worst_side_credit).
        """
        raise NotImplementedError

    @abstractmethod
    def should_close(self, chain: pd.DataFrame, position: Position) -> bool:
        """Return True if the open position should be closed on this trading day.

        Strategy decides based on profit target / DTE stop / stop-loss / etc.
        """
        raise NotImplementedError

    @abstractmethod
    def should_adjust(self, chain: pd.DataFrame, position: Position) -> StrategySignal | None:
        """Return adjust signal (or None if no action).

        Adjust can be a roll (close one side + open new vertical) or a partial
        scale-down. Returned signal contains the orders to apply.
        """
        raise NotImplementedError
