"""Shioaji (Sinopac Securities) broker client — Phase 2 stub.

Phase 1 keeps this empty so Phase 2 integration does not require any change
to the ``Broker`` abstraction or the live-trading engine. Add the ``shioaji``
dependency + fill methods only when ready to go live.
"""

from __future__ import annotations

from src.data.brokers.base import Broker


class ShioajiClient(Broker):
    """Sinopac Shioaji API adapter. Phase 2 implementation."""

    def __init__(self, api_key: str, secret: str, simulation: bool = True) -> None:
        """Initialise with credentials. Set simulation=True for paper trading."""
        self.api_key = api_key
        self.secret = secret
        self.simulation = simulation

    def connect(self) -> None:
        raise NotImplementedError("Phase 2: wire up shioaji.Shioaji().login()")

    def disconnect(self) -> None:
        raise NotImplementedError("Phase 2: wire up shioaji.Shioaji().logout()")

    def place_order(self, order: dict) -> str:
        raise NotImplementedError("Phase 2: wire up shioaji place_order flow")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("Phase 2: wire up shioaji list_positions")

    def get_quote(self, contract: str) -> dict:
        raise NotImplementedError("Phase 2: wire up shioaji contract quote lookup")
