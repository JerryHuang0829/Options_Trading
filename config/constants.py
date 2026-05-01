"""Project-wide constants for Options_Trading.

Values here are stable across the entire codebase. Anything that varies per
run (strike selection thresholds, DTE windows, position sizing) belongs in
strategy-level config (not here).

Override via environment variable (see ``.env.example``) when applicable.
"""

from __future__ import annotations

import os

RISK_FREE_RATE_DEFAULT: float = float(os.getenv("RISK_FREE_RATE", "0.015"))
"""Annualised risk-free rate as decimal (e.g. 0.015 = 1.5%).

Default anchored on TW 1Y deposit rate. Override via ``RISK_FREE_RATE`` env.
"""

DIVIDEND_YIELD_DEFAULT: float = float(os.getenv("DIVIDEND_YIELD", "0.035"))
"""Annualised continuous dividend yield for TAIEX as decimal (e.g. 0.035 = 3.5%).

TAIEX is a price index (not total-return); constituents' cash dividends cause
scheduled ex-dividend drops. BSM-Merton model requires q.

Default 3.5% anchored on TAIEX 2020-2025 trailing dividend yield. **This is a
synthetic / fallback default only — real backtests / live IV reverse-solving
MUST replace with point-in-time forward-implied q**:

  - Phase 1 Week 3+ (TAIFEX historical): derive q from put-call parity on the
    front-month ATM strike (``C - P = S·e^(-qT) - K·e^(-rT)`` → solve for q
    daily; persist as ``q_pit`` column in chain cache).
  - Phase 2 (live): use TAIFEX dividend futures basis or near-expiry forward
    curve.

Using this constant directly in production IV reverse-solving causes
systematic 1-2 vol-point bias, worse around ex-dividend dates. Override via
``DIVIDEND_YIELD`` env for synthetic / smoke testing only.
"""

TXO_MULTIPLIER: int = 50
"""TXO contract multiplier: each point = NT$50."""

TRADING_DAYS_PER_YEAR: int = 252
"""Trading days used for annualisation of returns / volatility."""

CALENDAR_DAYS_PER_YEAR: int = 365
"""Calendar days — for BSM time-to-expiry (T in years)."""
