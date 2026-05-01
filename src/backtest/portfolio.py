"""Portfolio state + mark-to-market + per-day P&L.

Phase 1 Week 2-3 will implement:
  - Position lifecycle: open -> hold -> close, each leg individually tracked
  - Daily mark-to-market using current option chain settle (or configured
    mid/bid per FillModel symmetry)
  - Realised P&L on close; unrealised P&L on hold
  - Greeks aggregation across all legs (portfolio-level risk view)

Dataclass types here (``OptionLeg`` / ``Position``) provide typed structured
storage instead of raw dicts, reducing silent-bug surface in backtest logic.
Codex Round 1 flagged that dict-based position representation would accumulate
typo / field-mismatch bugs fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from config.constants import (
    CALENDAR_DAYS_PER_YEAR,
    DIVIDEND_YIELD_DEFAULT,
    RISK_FREE_RATE_DEFAULT,
    TXO_MULTIPLIER,
)

if TYPE_CHECKING:
    from src.backtest.execution import FillModel

# Closed set for fill side (avoid circular import in non-TYPE_CHECKING context).
Side = Literal["buy", "sell"]


# Closed set for option type (Codex R2: prevent typo on "cal" / "Put").
OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class OptionLeg:
    """Single option contract leg in a multi-leg position.

    Attributes:
        contract: Contract identifier (e.g. "TXO202507C17000").
        strike: Strike price.
        expiry: Contract expiry date.
        option_type: ``"call"`` or ``"put"`` (closed set).
        qty: Signed quantity (+ for long, - for short). MUST be non-zero.
        entry_date: Date leg was opened.
        entry_price: Fill price at entry (from FillModel).
    """

    contract: str
    strike: int
    expiry: pd.Timestamp
    option_type: OptionType
    qty: int
    entry_date: pd.Timestamp
    entry_price: float

    def __post_init__(self) -> None:
        if self.qty == 0:
            raise ValueError("OptionLeg.qty must be non-zero (positive=long, negative=short)")
        if self.option_type not in ("call", "put"):
            raise ValueError(
                f"OptionLeg.option_type must be 'call'|'put', got {self.option_type!r}"
            )


@dataclass
class Position:
    """Multi-leg position (e.g. 4-leg Iron Condor, 2-leg Vertical).

    Attributes:
        legs: List of ``OptionLeg`` making up the position.
        open_date: Date position was opened (equals legs[0].entry_date).
        close_date: Date position was closed; ``None`` while open.
        realised_pnl: P&L booked at close (``None`` while open).
        strategy_name: Human-readable strategy label (e.g. "IC 45DTE 0.16d").
        tags: Arbitrary metadata (e.g. {"adjusted": True, "roll_round": 1}).
    """

    legs: list[OptionLeg]
    open_date: pd.Timestamp
    strategy_name: str
    close_date: pd.Timestamp | None = None
    realised_pnl: float | None = None
    realised_pnl_accumulated: float = 0.0  # adjust legs booked here; final close adds to this
    tags: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("Position.legs must contain at least one OptionLeg")


def _row_for_leg(chain: pd.DataFrame, leg: OptionLeg) -> pd.Series | None:
    """Find the chain row matching a leg's (strike, expiry, option_type). None if absent."""
    rows = chain[
        (chain["strike"] == leg.strike)
        & (chain["expiry"] == leg.expiry)
        & (chain["option_type"] == leg.option_type)
    ]
    if rows.empty:
        return None
    return rows.iloc[0]


def _mid_price(row: pd.Series, *, strict: bool = True) -> float:
    """Mark-to-market reference price = (bid + ask) / 2.

    R10.10 升級 + R10.11 hybrid + R10.12 settle finite guard.
    Use _mid_price_with_basis() for (price, basis) tuple audit return.
    """
    bid = row.get("bid")
    ask = row.get("ask")
    if bid is not None and ask is not None and pd.notna(bid) and pd.notna(ask):
        return float((bid + ask) / 2.0)
    if strict:
        raise ValueError(
            f"unmarkable row: missing bid/ask "
            f"(strike={row.get('strike')}, type={row.get('option_type')}, "
            f"expiry={row.get('expiry')}, date={row.get('date')})"
        )
    # R10.11 fallback + R10.12 修法 b: settle 必 finite (Codex 抓 silent NaN)
    settle = row.get("settle")
    if settle is None or pd.isna(settle) or not pd.api.types.is_number(settle):
        raise ValueError(
            f"unmarkable+unfallbackable row: bid/ask AND settle all missing "
            f"(strike={row.get('strike')}, type={row.get('option_type')}, "
            f"expiry={row.get('expiry')}, date={row.get('date')})"
        )
    val = float(settle)
    import math

    if not math.isfinite(val):
        raise ValueError(
            f"settle is non-finite ({val}) (strike={row.get('strike')}, date={row.get('date')})"
        )
    return val


def _mid_price_with_basis(
    row: pd.Series,
    *,
    fallback_mode: str = "strict",
) -> tuple[float, str]:
    """R10.11 audit variant: return (price, basis) for fallback tracking.

    Args:
        row: chain row (must have bid / ask; settle / model_price optional).
        fallback_mode: ``'strict'`` (no fallback; missing bid/ask raises),
            ``'settle'`` (R10.11 hybrid; bid/ask 缺 → settle), or
            ``'surface'`` (Week 5 Day 5.2; bid/ask 缺 → model_price).

    Returns: (price, basis) where basis ∈ {'mid', 'settle_fallback',
        'surface_fallback'}. R10.12 修法 b: settle finite guard.

    Raises:
        ValueError: missing required source per fallback_mode.
    """
    import math

    bid = row.get("bid")
    ask = row.get("ask")
    if bid is not None and ask is not None and pd.notna(bid) and pd.notna(ask):
        return float((bid + ask) / 2.0), "mid"

    if fallback_mode == "strict":
        raise ValueError(
            f"unmarkable row: missing bid/ask "
            f"(strike={row.get('strike')}, type={row.get('option_type')}, "
            f"expiry={row.get('expiry')}, date={row.get('date')})"
        )

    if fallback_mode == "surface":
        # Week 5 Day 5.2: model_price 為 fallback (cache miss / insufficient
        # / all_failed → NaN by Day 5.1 add_model_price; trust producer gate
        # — Pattern 14 producer/consumer parity).
        model_price = row.get("model_price")
        if (
            model_price is not None
            and pd.notna(model_price)
            and pd.api.types.is_number(model_price)
        ):
            val = float(model_price)
            if math.isfinite(val):
                return val, "surface_fallback"
            # non-finite model_price → fall through to settle (3rd layer)
        # R12.4 P fix (Codex audit institutional-grade gate):
        # 3rd-layer settle fallback when bid/ask AND model_price both missing.
        # Real TAIFEX 5yr實證 8073 such rows 100% have valid settle (mostly
        # settle=0 for far-OTM worthless strikes). settle=0 對 deep OTM 是
        # institutionally 正確 mark (option = worthless). Only raise if
        # truly_unmarkable (bid/ask AND model_price AND settle all missing).
        settle = row.get("settle")
        if settle is not None and pd.notna(settle) and pd.api.types.is_number(settle):
            val_s = float(settle)
            if math.isfinite(val_s):
                return val_s, "settle_3rd_fallback"
        raise ValueError(
            f"truly_unmarkable row: bid/ask AND model_price AND settle all missing "
            f"(strike={row.get('strike')}, type={row.get('option_type')}, "
            f"expiry={row.get('expiry')}, date={row.get('date')}). "
            f"R12.4 P (Codex): institutional-grade — should be 0 in real TAIFEX cache."
        )

    # fallback_mode == "settle": R10.12 修法 b: settle finite guard
    settle = row.get("settle")
    if settle is None or pd.isna(settle) or not pd.api.types.is_number(settle):
        raise ValueError(
            f"unmarkable+unfallbackable row: bid/ask AND settle all missing "
            f"(strike={row.get('strike')}, date={row.get('date')})"
        )
    val = float(settle)
    if not math.isfinite(val):
        raise ValueError(
            f"settle is non-finite ({val}) (strike={row.get('strike')}, date={row.get('date')})"
        )
    return val, "settle_fallback"


def _intrinsic_payoff(spot: float, strike: float, option_type: OptionType) -> float:
    """European-style settlement payoff at expiry."""
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


class Portfolio:
    """Tracks positions, cash, and P&L across a backtest.

    Cash convention (TWD):
      - At ``open``: cash += sum(-leg.qty * entry_price * TXO_MULTIPLIER)
        (short legs increase cash via collected premium; long legs decrease cash)
      - At ``close``: cash += sum(leg.qty * exit_price * TXO_MULTIPLIER)
        (close = sell longs at +qty * price, buy back shorts at -qty * price)
        Realised PnL per leg = leg.qty * (exit - entry) * TXO_MULTIPLIER.
    """

    def __init__(self, initial_capital: float) -> None:
        """Initialise with starting TWD capital and empty positions."""
        if initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: list[Position] = []
        self.realised_pnl_total: float = 0.0

    def open(self, legs: list[OptionLeg], strategy_name: str, **tags: Any) -> Position:
        """Record opening of a multi-leg position; debit/credit cash by entry premium.

        Args:
            legs: 1+ ``OptionLeg`` (entry_date / entry_price already populated by engine).
            strategy_name: Human label (e.g. "IC 45DTE 0.16d").
            **tags: arbitrary metadata to attach (e.g. entry_credit_mid /
                max_defined_risk_twd populated by the engine for risk gating).

        Returns: the new ``Position`` (also appended to ``self.positions``).
        """
        if not legs:
            raise ValueError("Portfolio.open requires at least one leg")
        # Cash flow at open: +qty * price (long pays, short collects).
        # leg.qty signs: short=-1, long=+1. Cash delta = -qty * price * mult.
        cash_delta = sum(-leg.qty * leg.entry_price * TXO_MULTIPLIER for leg in legs)
        self.cash += cash_delta
        position = Position(
            legs=list(legs),
            open_date=legs[0].entry_date,
            strategy_name=strategy_name,
            tags=dict(tags) if tags else {},
        )
        self.positions.append(position)
        return position

    def close(
        self,
        position_idx: int,
        chain: pd.DataFrame,
        *,
        fill_model: FillModel | None = None,
    ) -> float:
        """Close a position at the chain snapshot; book realised PnL.

        Args:
            position_idx: Index in ``self.positions``.
            chain: Single-day chain snapshot (must contain a ``date`` column).
            fill_model: Optional. If supplied, close legs go through the
                fill_model (short close = "buy" side; long close = "sell" side
                — symmetric to open). If None, exit price is mid-based
                (bid+ask)/2 with settle fallback.

        Behaviour on missing chain row:
            - If today >= leg.expiry → European settlement: exit_price equals
              ``max(spot - K, 0)`` (call) or ``max(K - spot, 0)`` (put). Spot is
              read from any row of ``chain['underlying']``.
            - Otherwise → ``ValueError`` (Codex R5 P1: must not assume zero).
        """
        if not 0 <= position_idx < len(self.positions):
            raise IndexError(f"position_idx={position_idx} out of range [0, {len(self.positions)})")
        position = self.positions[position_idx]
        if position.close_date is not None:
            raise ValueError(f"Position {position_idx} already closed on {position.close_date}")
        if chain.empty:
            raise ValueError("close: chain snapshot is empty")
        today = pd.Timestamp(chain["date"].iloc[0])

        realised = 0.0
        cash_delta = 0.0
        for leg in position.legs:
            row = _row_for_leg(chain, leg)
            if row is None:
                if today >= leg.expiry:
                    # European settlement: payoff at expiry.
                    spot = float(chain["underlying"].iloc[0])
                    exit_price = _intrinsic_payoff(spot, float(leg.strike), leg.option_type)
                else:
                    raise ValueError(
                        f"close: leg {leg.contract} (expiry={leg.expiry.date()}) has no chain "
                        f"row at {today.date()} and is not yet expired — refusing to assume "
                        f"zero. Synthetic chains must keep all open expiries continuous "
                        f"(R5 P1)."
                    )
            elif fill_model is not None:
                close_side: Side = "buy" if leg.qty < 0 else "sell"
                fill = fill_model.fill(row, close_side, abs(leg.qty))
                exit_price = fill.fill_price
                # Day 6.4: deduct close-leg retail cost from cash + realised
                cost = fill.commission + fill.tax
                if cost > 0:
                    self.cash -= cost
                    realised -= cost
            else:
                exit_price = _mid_price(row)
            realised += leg.qty * (exit_price - leg.entry_price) * TXO_MULTIPLIER
            # Cash at close: +qty * exit_price * mult (long sells, short buys back).
            cash_delta += leg.qty * exit_price * TXO_MULTIPLIER

        self.cash += cash_delta
        self.realised_pnl_total += realised
        # R6 F2: total realised PnL = adjust legs already booked + final close.
        position.realised_pnl = position.realised_pnl_accumulated + realised
        position.close_date = today
        return realised

    def mark_to_market(
        self,
        chain: pd.DataFrame,
        *,
        strict: bool = True,
        mark_policy: str = "strict_mid",
    ) -> float:
        """Return total unrealised PnL across all OPEN positions in TWD.

        Per-leg unrealised = leg.qty * (current_mid - entry_price) * TXO_MULTIPLIER.
        Closed positions contribute 0 (their realised PnL is already booked).

        R6 F1: row missing on an open leg is **NOT** silently ignored.
          - Default ``strict=True``: row missing on a leg whose ``today <
            leg.expiry`` raises ``ValueError``.
          - ``strict=False``: legs with row missing contribute 0 (legacy).

        R10.10 / R10.11 / Week 5 Day 5.2: bid/ask missing policy via ``mark_policy``:
          - ``"strict_mid"`` (default, OptionMetrics IvyDB-US standard):
            missing bid/ask raises (R5 P1 升級, Phase A). 用於 unit test +
            synthetic chain (100% bid/ask) + 任何要求 mark = midpoint only
            的 institutional research.
          - ``"mid_with_settle_fallback"`` (institutional 1B/3 standard):
            missing bid/ask falls back to settle. Audit-track fallback rate
            via ``self.last_mark_fallback_rate``. 用於 TAIFEX 真資料
            backtest (60% rows have missing bid/ask, drop 不可行).
          - ``"mid_with_surface_fallback"`` (Week 5 Day 5.2 — vol surface
            mark integration): missing bid/ask falls back to ``model_price``
            (Day 5.1 add_model_price 從 cached SVI/SABR/poly fit 反算).
            ``n_fallback_surface`` audit 計數於 ``self.last_mark_n_fallback_surface``.
            設計對稱 ``mid_with_settle_fallback`` (2-tier with 1 fallback);
            cache miss / insufficient_data → model_price NaN → raise (D-soft
            紀律: 缺價 = 缺價，不串接 settle 二級退路).

        Legs with ``today >= leg.expiry`` (settled) use intrinsic payoff
        regardless (matches close()'s European convention).
        """
        valid_policies = ("strict_mid", "mid_with_settle_fallback", "mid_with_surface_fallback")
        if mark_policy not in valid_policies:
            raise ValueError(f"mark_policy must be {'|'.join(valid_policies)}, got {mark_policy!r}")
        # R10.11 + Day 5.2: mark_policy → fallback_mode dispatch
        if mark_policy == "strict_mid":
            fallback_mode = "strict"
        elif mark_policy == "mid_with_settle_fallback":
            fallback_mode = "settle"
        else:  # 'mid_with_surface_fallback'
            fallback_mode = "surface"

        if chain.empty:
            raise ValueError("mark_to_market: chain snapshot is empty")
        today = pd.Timestamp(chain["date"].iloc[0])
        unrealised = 0.0
        n_legs_marked = 0
        n_fallback_settle = 0
        n_fallback_surface = 0
        n_fallback_settle_3rd = 0  # R12.5 P fix: separate metric (Codex audit)
        for position in self.positions:
            if position.close_date is not None:
                continue
            for leg in position.legs:
                row = _row_for_leg(chain, leg)
                if row is None:
                    if today >= leg.expiry:
                        spot = float(chain["underlying"].iloc[0])
                        current = _intrinsic_payoff(spot, float(leg.strike), leg.option_type)
                    elif strict:
                        raise ValueError(
                            f"mark_to_market: leg {leg.contract} (expiry={leg.expiry.date()}) "
                            f"has no chain row at {today.date()} and is not yet expired. "
                            f"Stale-quote policy required (R6 F1). Pass strict=False only "
                            f"after substituting upstream."
                        )
                    else:
                        continue
                else:
                    current, basis = _mid_price_with_basis(row, fallback_mode=fallback_mode)
                    n_legs_marked += 1
                    if basis == "settle_fallback":
                        n_fallback_settle += 1
                    elif basis == "surface_fallback":
                        n_fallback_surface += 1
                    elif basis == "settle_3rd_fallback":
                        # R12.4 P fix: 3rd-layer settle fallback within
                        # mid_with_surface_fallback policy (mid -> surface -> settle).
                        # R12.5 P fix (Codex audit): separate metric so caller can
                        # distinguish "direct settle policy" from "surface degraded
                        # to settle". Backward-compat: also count into n_fallback_settle
                        # so existing callers see total settle-route legs.
                        n_fallback_settle += 1
                        n_fallback_settle_3rd += 1
                unrealised += leg.qty * (current - leg.entry_price) * TXO_MULTIPLIER

        # R10.11 + Day 5.2 audit: track fallback rate (combined settle + surface
        # for backward-compat single rate; per-source counts in n_fallback_*).
        n_fallback = n_fallback_settle + n_fallback_surface
        self.last_mark_fallback_rate = n_fallback / n_legs_marked if n_legs_marked > 0 else 0.0
        self.last_mark_n_legs_marked = n_legs_marked
        self.last_mark_n_fallback_settle = n_fallback_settle
        self.last_mark_n_fallback_surface = n_fallback_surface
        # R12.5 P fix (Codex audit): separate settle_3rd_fallback exposure
        self.last_mark_n_fallback_settle_3rd = n_fallback_settle_3rd
        return unrealised

    def aggregate_greeks(self, chain: pd.DataFrame, *, strict: bool = True) -> dict[str, float]:
        """Aggregate delta / gamma / theta / vega across all OPEN legs.

        Each chain row carries a precomputed ``delta`` (enriched schema). For
        gamma / theta / vega we compute from BSM-Merton on the row's ``iv``
        and ``underlying``. Returns 0.0 entries when no open legs.

        Position-level Greek = leg.qty * (per-contract Greek) * TXO_MULTIPLIER.
        TXO_MULTIPLIER converts per-contract sensitivity to NT$ / unit move.

        R6 F1 sweep (Pattern 5): same stale-quote policy as ``mark_to_market``.
        Default ``strict=True`` raises on any open leg whose quote is missing
        before expiry; legs that have already settled (today >= expiry) are
        skipped (Greeks are zero at/after expiry).
        """
        from src.options.greeks import gamma as bsm_gamma
        from src.options.greeks import theta as bsm_theta
        from src.options.greeks import vega as bsm_vega

        agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        if not self.positions:
            return agg
        if chain.empty:
            raise ValueError("aggregate_greeks: chain snapshot is empty")

        today = pd.Timestamp(chain["date"].iloc[0])
        for position in self.positions:
            if position.close_date is not None:
                continue
            for leg in position.legs:
                row = _row_for_leg(chain, leg)
                if row is None:
                    if today >= leg.expiry:
                        continue  # settled leg → contributes 0 (no Greeks at expiry)
                    if strict:
                        raise ValueError(
                            f"aggregate_greeks: leg {leg.contract} "
                            f"(expiry={leg.expiry.date()}) has no chain row at "
                            f"{today.date()} and is not yet expired (R6 Pattern 5)."
                        )
                    continue
                T = max((leg.expiry - today).days / CALENDAR_DAYS_PER_YEAR, 1e-6)
                S = float(row["underlying"])
                K = float(leg.strike)
                # R10.12 修法 c (Codex Pattern 5 sibling): iv/delta NaN hard raise.
                # 之前 silent NaN propagate 到 gamma/theta/vega → state 污染 risk gate.
                iv_raw = row.get("iv")
                if iv_raw is None or pd.isna(iv_raw):
                    if strict:
                        raise ValueError(
                            f"aggregate_greeks: leg {leg.contract} iv is NaN at "
                            f"{today.date()} (R10.12 修法 c sibling sweep). "
                            f"Pass strict=False only after upstream substitution."
                        )
                    continue
                sigma = float(iv_raw)
                # delta from chain (caller pre-computed).
                d_raw = row.get("delta")
                if d_raw is None or pd.isna(d_raw):
                    if strict:
                        raise ValueError(
                            f"aggregate_greeks: leg {leg.contract} delta is NaN at "
                            f"{today.date()} (R10.12 修法 c sibling sweep)."
                        )
                    continue
                d = float(d_raw)
                g = bsm_gamma(S, K, T, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT, sigma)
                t = bsm_theta(
                    S, K, T, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT, sigma, leg.option_type
                )
                v = bsm_vega(S, K, T, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT, sigma)
                qty_mult = leg.qty * TXO_MULTIPLIER
                agg["delta"] += qty_mult * d
                agg["gamma"] += qty_mult * g
                agg["theta"] += qty_mult * t
                agg["vega"] += qty_mult * v
        return agg
