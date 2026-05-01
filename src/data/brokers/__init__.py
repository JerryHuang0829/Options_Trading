"""Broker abstraction layer.

Phase 1 keeps this as interface-only (``base.py``) with ``shioaji_client.py``
as a pure stub. Phase 2 will fill shioaji_client.py to enable live trading
through Sinopac Securities.

Design rule: backtest and live-trading code paths both depend on
``Broker`` abstract protocol, never on a concrete broker implementation.
"""
