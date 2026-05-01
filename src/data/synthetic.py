"""Synthetic option chain factory for Phase 1 Week 1-2 smoke testing.

Before the TAIFEX loader is ready (Week 3+), this module produces a deterministic
TXO-like chain so BSM / IC / backtest pipelines can be developed end-to-end.

Pipeline:
  1. ``SyntheticChainConfig`` (frozen dataclass) declares all parameters.
  2. ``generate_chain(config)`` returns an **enriched** DataFrame with:
       - All 16+ raw TAIFEX columns (date / contract / strike / settle / bid / ask / ...)
       - 4 enriched columns required by ``src/options/chain.py``: iv / delta / dte / underlying

Design notes:
  - Spot path: seeded geometric Brownian motion (drift defaults to risk-neutral r-q).
  - Strikes: fixed grid centred on ``spot_start`` (matches TXO real-world behaviour
    where strike grid is set by exchange, not adjusted daily for spot moves).
  - **Expiries: fixed expiry calendar (3rd Wednesday of each month, TXO standard)**.
    Every trading day emits chain rows for **all active expiries** (those between
    today and ``today + max_dte``). This guarantees that a position opened on a
    given day can be marked-to-market every day until close — rolling DTE
    (R5 P1 root cause) breaks this invariant because each day's chain would
    contain a different ``expiry`` per row.
  - IV: constant ``config.sigma`` for every contract (no smile / term structure).
    Real-world IV smile is Phase 2 sensitivity work.
  - Settle: computed via ``src.options.pricing.bsm_price`` (canonical BSM-Merton).
    Put-call parity therefore holds to floating-point precision; synthetic data
    is a clean ground truth that exposes pipeline bugs but not market microstructure.
  - bid/ask: ``settle * (1 ± uniform(0.01, 0.03))`` (simplistic spread; real TXO
    spread varies with liquidity and Vega).
  - delta: computed via ``src.options.greeks.delta`` (avoids reverse-solving IV
    since synthetic settle was generated with known sigma).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from config.constants import (
    CALENDAR_DAYS_PER_YEAR,
    DIVIDEND_YIELD_DEFAULT,
    RISK_FREE_RATE_DEFAULT,
)
from src.options.greeks import delta as greek_delta
from src.options.pricing import bsm_price

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class SyntheticChainConfig:
    """Configuration for ``generate_chain``.

    Attributes:
        start_date: ISO date string (inclusive); first trading day in output.
        end_date: ISO date string (inclusive); last trading day in output.
        spot_start: Underlying spot at ``start_date``.
        sigma: Annualised volatility (constant across strikes / expiries).
        drift: Annualised GBM drift. ``None`` (default) → risk-neutral ``r - q``.
        r: Annualised risk-free rate (decimal).
        q: Annualised continuous dividend yield (decimal).
        strike_step: Strike grid step in index points (TXO uses 100).
        n_strikes_per_side: Strikes generated above and below ``spot_start``;
            total = ``2 * n_strikes_per_side + 1``.
        max_dte: Look-ahead ceiling. Every trading day emits chain rows for all
            active expiries within ``[today, today + max_dte]``. Must be > 0.
        seed: RNG seed for reproducibility.
    """

    start_date: str
    end_date: str
    spot_start: float = 16800.0
    sigma: float = 0.20
    drift: float | None = None
    r: float = RISK_FREE_RATE_DEFAULT
    q: float = DIVIDEND_YIELD_DEFAULT
    strike_step: int = 100
    n_strikes_per_side: int = 30
    max_dte: int = 90
    seed: int = 42

    def __post_init__(self) -> None:
        start = pd.Timestamp(self.start_date)
        end = pd.Timestamp(self.end_date)
        if end < start:
            raise ValueError(f"end_date ({self.end_date}) < start_date ({self.start_date})")
        if self.spot_start <= 0:
            raise ValueError(f"spot_start must be > 0, got {self.spot_start}")
        if self.sigma <= 0:
            raise ValueError(f"sigma must be > 0, got {self.sigma}")
        if self.strike_step <= 0:
            raise ValueError(f"strike_step must be > 0, got {self.strike_step}")
        if self.n_strikes_per_side <= 0:
            raise ValueError(f"n_strikes_per_side must be > 0, got {self.n_strikes_per_side}")
        if self.max_dte <= 0:
            raise ValueError(f"max_dte must be > 0, got {self.max_dte}")


def _generate_spot_path(
    n_days: int, spot_start: float, drift: float, sigma: float, rng: np.random.Generator
) -> np.ndarray:
    """Generate GBM spot path (length ``n_days``); first element = ``spot_start``."""
    dt = 1.0 / CALENDAR_DAYS_PER_YEAR
    sqrt_dt = np.sqrt(dt)
    z = rng.standard_normal(n_days - 1)
    log_returns = (drift - 0.5 * sigma * sigma) * dt + sigma * sqrt_dt * z
    log_path = np.concatenate([[np.log(spot_start)], np.log(spot_start) + np.cumsum(log_returns)])
    return np.exp(log_path)


def _expiry_calendar(start: pd.Timestamp, end: pd.Timestamp, max_dte: int) -> list[pd.Timestamp]:
    """Generate fixed monthly expiries (3rd Wednesday) covering [start, end + max_dte].

    Real TXO weekly contracts are not modelled in Phase 1; monthly is sufficient
    to validate persistence-of-expiry over a position's holding period (R5 P1).
    """
    last_day = end + pd.Timedelta(days=max_dte + 31)
    months = pd.date_range(start.replace(day=1), last_day.replace(day=1), freq="MS")
    expiries: list[pd.Timestamp] = []
    for month_start in months:
        # 3rd Wednesday: first Wed = month_start + ((2 - weekday) mod 7) days; +14.
        weekday = month_start.weekday()  # Mon=0
        first_wed = month_start + pd.Timedelta(days=(2 - weekday) % 7)
        third_wed = first_wed + pd.Timedelta(days=14)
        expiries.append(pd.Timestamp(third_wed))
    return expiries


def generate_chain(config: SyntheticChainConfig) -> pd.DataFrame:
    """Build enriched synthetic chain DataFrame.

    Output columns (24 total):
        Raw (16+): date, contract, contract_month_week, contract_date, strike,
            option_type, trading_session, open, high, low, last,
            change, change_pct, historical_high, historical_low, bid, ask,
            settle, volume, open_interest
        Enriched (4): iv, delta, dte, underlying
    """
    rng = np.random.default_rng(config.seed)
    drift = config.drift if config.drift is not None else (config.r - config.q)

    # Trading days = business days between start and end (B = Mon-Fri).
    dates = pd.bdate_range(start=config.start_date, end=config.end_date)
    if len(dates) == 0:
        raise ValueError("No business days in [start_date, end_date]")

    # Fixed strike grid centred on spot_start.
    strikes = [
        int(config.spot_start + i * config.strike_step)
        for i in range(-config.n_strikes_per_side, config.n_strikes_per_side + 1)
    ]

    # GBM spot path, one value per trading day.
    spots = _generate_spot_path(len(dates), config.spot_start, drift, config.sigma, rng)

    # Fixed monthly expiry calendar (3rd Wed). Each trading day will emit chain
    # rows for the subset of expiries within [today, today + max_dte].
    expiry_calendar = _expiry_calendar(
        pd.Timestamp(config.start_date), pd.Timestamp(config.end_date), config.max_dte
    )

    rows: list[dict] = []
    for date_idx, (date, spot) in enumerate(zip(dates, spots, strict=True)):
        active_expiries = [e for e in expiry_calendar if 0 < (e - date).days <= config.max_dte]
        for expiry in active_expiries:
            dte = int((expiry - date).days)
            T = dte / CALENDAR_DAYS_PER_YEAR
            contract_month_week = expiry.strftime("%Y%m")
            for strike in strikes:
                for option_type in ("call", "put"):
                    settle = bsm_price(
                        spot, strike, T, config.r, config.q, config.sigma, option_type
                    )
                    d = greek_delta(spot, strike, T, config.r, config.q, config.sigma, option_type)
                    spread_pct_bid = rng.uniform(0.01, 0.03)
                    spread_pct_ask = rng.uniform(0.01, 0.03)
                    open_noise = rng.normal(0.0, 0.005)
                    high_noise = rng.uniform(0.0, 0.01)
                    low_noise = rng.uniform(0.0, 0.01)
                    open_px = float(settle * (1.0 + open_noise))
                    high_px = float(max(open_px, settle) * (1.0 + high_noise))
                    low_px = float(min(open_px, settle) * (1.0 - low_noise))
                    rows.append(
                        {
                            "date": date,
                            "contract": "TXO",
                            "contract_month_week": contract_month_week,
                            "contract_date": expiry,
                            "expiry": expiry,  # Week 2 Day 2: Day 3 chain helpers expect 'expiry'
                            "strike": strike,
                            "option_type": option_type,
                            "trading_session": "regular",
                            "open": open_px,
                            "high": high_px,
                            "low": low_px,
                            "last": float(settle),
                            "change": np.nan,  # filled below
                            "change_pct": np.nan,
                            "historical_high": np.nan,
                            "historical_low": np.nan,
                            "bid": float(settle * (1.0 - spread_pct_bid)),
                            "ask": float(settle * (1.0 + spread_pct_ask)),
                            "settle": float(settle),
                            "volume": int(rng.exponential(scale=500.0)),
                            "open_interest": int(rng.exponential(scale=2000.0)),
                            "iv": config.sigma,
                            "delta": float(d),
                            "dte": dte,
                            "underlying": float(spot),
                        }
                    )
                    _ = date_idx  # acknowledge unused (kept for future per-day logic)

    if not rows:
        # R6 F3: short max_dte windows can produce zero active expiries
        # (e.g. start = day after 3rd Wed with max_dte=7 gives no monthly
        # expiry within reach). Surface this explicitly instead of crashing
        # in pivot/pandas with a KeyError.
        raise ValueError(
            f"No active expiries within [{config.start_date}, {config.end_date}] "
            f"under max_dte={config.max_dte}. Increase max_dte or shift the "
            f"window to include a 3rd-Wednesday monthly expiry."
        )

    df = pd.DataFrame(rows)

    # Per-contract change / change_pct (vs previous trading day same contract).
    # Codex R4 P2: ``contract_date`` is the canonical per-contract group key —
    # different expiries that happen to share a month / week label would
    # otherwise pool together and produce a fake cross-expiry "change".
    group_cols = ["contract_date", "contract_month_week", "strike", "option_type"]
    df = df.sort_values(["date", *group_cols]).reset_index(drop=True)
    grp = df.groupby(group_cols, sort=False)["settle"]
    prev_settle = grp.shift(1)
    df["change"] = df["settle"] - prev_settle
    df["change_pct"] = df["change"] / prev_settle.replace(0, np.nan)

    # Historical high/low per contract: cumulative max/min of settle over the
    # contract's life as observed in this dataset (synthetic proxy for "since listing").
    df["historical_high"] = grp.cummax()
    df["historical_low"] = grp.cummin()

    return df
