"""Broker abstract protocol.

Defines the minimum interface any concrete broker (Shioaji, paper, mock)
must provide for the trading code path to use it. Keeps the backtest-to-live
transition ABI-stable.

Phase 1: interface only — no concrete calls.
Phase 2: ShioajiClient implements this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Broker(ABC):
    """Broker abstraction — consumed by the live-trading engine."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection + authenticate. Raise on failure."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Clean up connection."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: dict) -> str:
        """Submit an order. Return broker-assigned order id."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return currently held positions."""
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, contract: str) -> dict:
        """Return latest bid/ask/last for a contract."""
        raise NotImplementedError
