"""Vertical spread primitives for IC adjustment.

Per Phase 1 decision (plan §Stage 1 #2), Vertical spreads are NOT traded
standalone — they exist only as a roll target when an Iron Condor's short
wing is breached. Open-as-IC / close-as-IC / roll-leg-as-Vertical.

Exposed primitives (Week 2 Day 3):
  - build_bull_put_spread: sell put K1, buy put K2 (K2 < K1)
  - build_bear_call_spread: sell call K1, buy call K2 (K2 > K1)

Both return ``list[OptionLeg]`` of length 2 (short leg first, long leg
second). The chain row's ``settle`` is used as the entry price placeholder;
Day 4 wires in ``FillModel`` to replace this with bid/ask-based fill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from src.common.types import OptionLeg

if TYPE_CHECKING:
    pass


def _select_row(chain: pd.DataFrame, strike: float, option_type: str) -> pd.Series:
    """Return the chain row matching (strike, option_type) for the single date.

    Raises ValueError if not exactly one row matches (handles missing strike
    or duplicate quotes — Codex R4 Audit 3 lenient handling, Day 7 may add
    raise_on_duplicate guard).
    """
    rows = chain[(chain["strike"] == int(strike)) & (chain["option_type"] == option_type)]
    if rows.empty:
        raise ValueError(f"No {option_type} option at strike {strike} in chain")
    if len(rows) > 1:
        # Take first; Day 7 will add explicit raise_on_duplicate guard.
        rows = rows.iloc[[0]]
    return rows.iloc[0]


def _build_leg(row: pd.Series, qty: int) -> OptionLeg:
    """Construct an OptionLeg from a chain row + signed qty."""
    contract = (
        f"TXO{pd.Timestamp(row['expiry']).strftime('%Y%m%d')}"
        f"{'C' if row['option_type'] == 'call' else 'P'}{int(row['strike'])}"
    )
    return OptionLeg(
        contract=contract,
        strike=int(row["strike"]),
        expiry=pd.Timestamp(row["expiry"]),
        option_type=row["option_type"],
        qty=qty,
        entry_date=pd.Timestamp(row["date"]),
        entry_price=float(row["settle"]),
    )


def build_bull_put_spread(
    chain: pd.DataFrame,
    short_strike: float,
    long_strike: float,
) -> list[OptionLeg]:
    """Return the 2 legs of a Bull Put Spread.

    Bull Put = sell put @ K1 (higher), buy put @ K2 (lower).
    Net credit; profits if spot stays above K1 to expiry.

    Args:
        chain: Single-day option chain (must include both strikes' put rows).
        short_strike: Short put strike (higher strike).
        long_strike: Long put strike (lower strike, protective wing).
    """
    if long_strike >= short_strike:
        raise ValueError(
            f"Bull Put: long_strike ({long_strike}) must be < short_strike ({short_strike})"
        )
    short_row = _select_row(chain, short_strike, "put")
    long_row = _select_row(chain, long_strike, "put")
    return [
        _build_leg(short_row, qty=-1),  # short = -1
        _build_leg(long_row, qty=+1),
    ]


def build_bear_call_spread(
    chain: pd.DataFrame,
    short_strike: float,
    long_strike: float,
) -> list[OptionLeg]:
    """Return the 2 legs of a Bear Call Spread.

    Bear Call = sell call @ K1 (lower), buy call @ K2 (higher).
    Net credit; profits if spot stays below K1 to expiry.

    Args:
        chain: Single-day option chain (must include both strikes' call rows).
        short_strike: Short call strike (lower strike).
        long_strike: Long call strike (higher strike, protective wing).
    """
    if long_strike <= short_strike:
        raise ValueError(
            f"Bear Call: long_strike ({long_strike}) must be > short_strike ({short_strike})"
        )
    short_row = _select_row(chain, short_strike, "call")
    long_row = _select_row(chain, long_strike, "call")
    return [
        _build_leg(short_row, qty=-1),
        _build_leg(long_row, qty=+1),
    ]
