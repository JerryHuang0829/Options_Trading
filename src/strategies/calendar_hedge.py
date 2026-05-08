"""Long calendar ATM call hedge — 2-leg builder for Phase 1 Week 7 Quick A.

Per Day 7.0 feasibility audit (2026-05-02): 5-cohort multi-expiry ladder 不可行
(strategy_view monthly only / mean 1.44 unique expiry/day in DTE 21-63). Quick A
退化方案 = 1-cohort hedged IC; calendar median cost ratio 1.35x viable.

Calendar structure (call-only, half cost vs straddle):
  - Sell front ATM call (expiry = IC expiry; gets 100% of theta back)
  - Buy back ATM call (expiry = IC expiry + hedge_dte_offset; long vega)
  → Net: vega-positive (back > front), theta-cost (back decay slower),
    delta neutral at ATM (~+0.5 - 0.5 = 0).

Why call-only not call+put (straddle):
  - Day 7.0 audit: straddle median 8.36x IC credit → NO-GO. Calendar 1.35x viable.
  - Put-call parity at ATM: K = F means C - P = 0 → put leg redundant for vega.
  - IC already has put-side short gamma; hedge for vega term-structure not gamma.

Why calendar (call only) for Quick A:
  - Available 90%+ days (back month TXO listed always; only front DTE 21-63 days
    have 1.44 mean unique expiry).
  - Settle-based audit estimated 1.35x cost ratio (real worst-side ~1.5-1.8x).
"""

from __future__ import annotations

import pandas as pd

from src.common.types import Order
from src.strategies.iron_condor import IronCondor

# TXO 履約價 50-pt grid (config/constants.py 既有 TXO_STRIKE_GRID 為 50)
TXO_STRIKE_GRID = 50.0


def build_long_calendar_atm_call(
    chain: pd.DataFrame,
    ic_expiry: pd.Timestamp,
    underlying: float,
    hedge_dte_offset: int = 30,
) -> list[Order]:
    """Build 2-leg long calendar: sell front ATM call + buy back ATM call.

    Args:
        chain: single-day enriched chain DataFrame (cols: date/expiry/strike/
            option_type/bid/ask/settle/can_buy/can_sell).
        ic_expiry: IC's expiry; calendar's front leg uses this expiry.
        underlying: spot price (chain["underlying"].iloc[0]).
        hedge_dte_offset: target DTE offset for back leg (default 30 days
            past IC expiry).

    Returns: list of 2 Orders (sell front, buy back). Total 2 legs.

    Raises:
        ValueError: back expiry unavailable / ATM strike not in front or back chain.

    Pattern 18 absolute claim guard: Quick A 接受 ATM strike rounding ±25 pts;
    若 underlying mid-grid (e.g. 16825) → round to 16800. Real broker may have
    less liquid mid-strikes; reflected in worst-side fill cost.
    """
    if chain.empty:
        raise ValueError("calendar hedge: empty chain")
    today = pd.Timestamp(chain["date"].iloc[0])

    # 1. Pick back expiry — closest to ic_expiry + hedge_dte_offset
    target_back_dte = (ic_expiry - today).days + hedge_dte_offset
    if target_back_dte <= 0:
        raise ValueError(
            f"calendar hedge: ic_expiry {ic_expiry.date()} <= today {today.date()} "
            f"+ offset {hedge_dte_offset}; cannot place back leg"
        )
    candidates = chain[chain["expiry"] > ic_expiry][["expiry"]].drop_duplicates()
    if candidates.empty:
        raise ValueError(f"calendar hedge: no back-month expiry > {ic_expiry.date()} in chain")
    candidates = candidates.copy()
    candidates["dte"] = (candidates["expiry"] - today).dt.days
    candidates["diff"] = (candidates["dte"] - target_back_dte).abs()
    back_expiry = pd.Timestamp(candidates.sort_values("diff").iloc[0]["expiry"])

    # 2. ATM strike on 50-pt grid closest to underlying
    atm_strike = round(underlying / TXO_STRIKE_GRID) * TXO_STRIKE_GRID

    # 3. Find front + back ATM call rows
    front_call = chain[
        (chain["expiry"] == ic_expiry)
        & (chain["strike"] == atm_strike)
        & (chain["option_type"] == "call")
    ]
    back_call = chain[
        (chain["expiry"] == back_expiry)
        & (chain["strike"] == atm_strike)
        & (chain["option_type"] == "call")
    ]
    if front_call.empty:
        raise ValueError(
            f"calendar hedge: front ATM call strike={atm_strike} expiry={ic_expiry.date()} "
            f"not in chain (underlying={underlying:.0f})"
        )
    if back_call.empty:
        raise ValueError(
            f"calendar hedge: back ATM call strike={atm_strike} expiry={back_expiry.date()} "
            f"not in chain"
        )

    # 4. Build 2 Orders (mirror IronCondor._order_from_row contract format)
    return [
        IronCondor._order_from_row(front_call.iloc[0], side="sell"),  # short front
        IronCondor._order_from_row(back_call.iloc[0], side="buy"),  # long back
    ]


def estimate_calendar_premium(
    chain: pd.DataFrame,
    front_strike: float,
    front_expiry: pd.Timestamp,
    back_strike: float,
    back_expiry: pd.Timestamp,
    use_settle: bool = True,
) -> float:
    """Settle-based premium estimate: pay back - receive front.

    For instrumentation / metadata only; engine uses fill_model for real PnL.
    """
    front = chain[
        (chain["expiry"] == front_expiry)
        & (chain["strike"] == front_strike)
        & (chain["option_type"] == "call")
    ]
    back = chain[
        (chain["expiry"] == back_expiry)
        & (chain["strike"] == back_strike)
        & (chain["option_type"] == "call")
    ]
    if front.empty or back.empty:
        return float("nan")
    col = "settle" if use_settle else "bid"
    front_px = float(front[col].iloc[0]) if pd.notna(front[col].iloc[0]) else 0.0
    back_px = float(back["ask" if not use_settle else "settle"].iloc[0])
    if pd.isna(back_px):
        return float("nan")
    return back_px - front_px
