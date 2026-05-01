"""Tests for src/data/synthetic.py.

Covers:
  - test_synthetic_chain_shape: 24-col schema (16+ raw + 4 enriched), non-empty, dtypes
  - test_synthetic_chain_put_call_parity (Merton): same K/T → C - P ≈ S·e^(-qT) - K·e^(-rT)
    Tolerance < 1e-10 (synthetic settle is BSM-priced; should hit float precision).
  - test_synthetic_chain_reproducibility: seed=42 produces DataFrame.equals output twice
  - test_synthetic_chain_config_validation: bad config raises ValueError
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.data.synthetic import SyntheticChainConfig, generate_chain


def _small_config() -> SyntheticChainConfig:
    """Small chain for fast tests: 5 business days × monthly expiries × 21 strikes × 2 types."""
    return SyntheticChainConfig(
        start_date="2026-01-05",  # Monday
        end_date="2026-01-09",  # Friday
        spot_start=16800.0,
        sigma=0.20,
        r=0.015,
        q=0.035,
        strike_step=100,
        n_strikes_per_side=10,
        max_dte=60,  # 2026-01 + 2026-02 expiries cover this window
        seed=42,
    )


REQUIRED_COLUMNS = {
    # Raw 20 columns
    "date",
    "contract",
    "contract_month_week",
    "contract_date",
    "strike",
    "option_type",
    "trading_session",
    "open",
    "high",
    "low",
    "last",
    "change",
    "change_pct",
    "historical_high",
    "historical_low",
    "bid",
    "ask",
    "settle",
    "volume",
    "open_interest",
    # Enriched 5 columns (Week 2 Day 2: 'expiry' alias to contract_date for chain helpers)
    "iv",
    "delta",
    "dte",
    "underlying",
    "expiry",
}


def test_synthetic_chain_shape() -> None:
    """Verify schema (25 cols), non-empty, expected dtypes."""
    config = _small_config()
    df = generate_chain(config)

    # Schema: must contain all 25 expected columns.
    assert set(df.columns) >= REQUIRED_COLUMNS, (
        f"missing columns: {REQUIRED_COLUMNS - set(df.columns)}"
    )

    # Non-empty: 5 days × n_active_expiries × 21 strikes × 2 types.
    # max_dte=60 from 2026-01-05 covers 2026-01 (Wed 21) + 2026-02 (Wed 18) expiries.
    n_strikes = 2 * config.n_strikes_per_side + 1
    assert len(df) > 0
    # Each (date, expiry) cell should have n_strikes × 2 (call+put) rows.
    cell_counts = df.groupby(["date", "expiry"]).size()
    assert (cell_counts == n_strikes * 2).all()

    # Dtype spot-checks (most important ones).
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_datetime64_any_dtype(df["contract_date"])
    assert pd.api.types.is_integer_dtype(df["strike"])
    assert pd.api.types.is_integer_dtype(df["volume"])
    assert pd.api.types.is_integer_dtype(df["open_interest"])
    assert pd.api.types.is_integer_dtype(df["dte"])
    assert pd.api.types.is_float_dtype(df["settle"])
    assert pd.api.types.is_float_dtype(df["delta"])
    assert pd.api.types.is_float_dtype(df["iv"])
    assert pd.api.types.is_float_dtype(df["underlying"])

    # Constants make sense.
    assert (df["contract"] == "TXO").all()
    assert (df["trading_session"] == "regular").all()
    assert df["option_type"].isin(["call", "put"]).all()
    assert (df["iv"] == config.sigma).all()  # constant IV per Day 4 design


def test_synthetic_chain_put_call_parity() -> None:
    """C - P ≈ S·e^(-qT) - K·e^(-rT) (Merton parity), expect float precision."""
    config = _small_config()
    df = generate_chain(config)
    r, q = config.r, config.q

    # Pivot per (date, strike, contract_month_week) to get aligned call / put.
    pivoted = df.pivot_table(
        index=["date", "contract_month_week", "strike", "underlying", "dte"],
        columns="option_type",
        values="settle",
        aggfunc="first",
    ).reset_index()

    # Random sample 50 rows for parity check (or all if fewer).
    rng = np.random.default_rng(seed=99)
    n_sample = min(50, len(pivoted))
    sample_idx = rng.choice(len(pivoted), size=n_sample, replace=False)
    sample = pivoted.iloc[sample_idx]

    for _, row in sample.iterrows():
        S = float(row["underlying"])
        K = float(row["strike"])
        T = float(row["dte"]) / 365.0
        C = float(row["call"])
        P = float(row["put"])
        lhs = C - P
        rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
        diff = abs(lhs - rhs)
        assert diff < 1e-10, (
            f"parity broken: S={S}, K={K}, T={T}, C={C}, P={P}, "
            f"lhs={lhs}, rhs={rhs}, diff={diff:.2e}"
        )


def test_synthetic_chain_reproducibility() -> None:
    """seed=42 must produce DataFrame.equals output across two runs."""
    config = _small_config()
    df1 = generate_chain(config)
    df2 = generate_chain(config)
    pd.testing.assert_frame_equal(df1, df2)


def test_synthetic_chain_config_validation() -> None:
    """SyntheticChainConfig __post_init__ rejects bad inputs."""
    start, end = "2026-01-05", "2026-01-09"
    with pytest.raises(ValueError, match="end_date"):
        SyntheticChainConfig(start_date="2026-01-09", end_date="2026-01-05")
    with pytest.raises(ValueError, match="spot_start"):
        SyntheticChainConfig(start_date=start, end_date=end, spot_start=-100.0)
    with pytest.raises(ValueError, match="sigma"):
        SyntheticChainConfig(start_date=start, end_date=end, sigma=0.0)
    with pytest.raises(ValueError, match="strike_step"):
        SyntheticChainConfig(start_date=start, end_date=end, strike_step=0)
    with pytest.raises(ValueError, match="n_strikes_per_side"):
        SyntheticChainConfig(start_date=start, end_date=end, n_strikes_per_side=0)
    with pytest.raises(ValueError, match="max_dte"):
        SyntheticChainConfig(start_date=start, end_date=end, max_dte=0)


def test_synthetic_expiry_persists_across_days() -> None:
    """R5 P1 regression: a position opened on day N must find its expiry rows
    on every subsequent trading day until ≤ expiry."""
    config = SyntheticChainConfig(
        start_date="2026-01-05",
        end_date="2026-02-13",  # 6 weeks
        n_strikes_per_side=3,
        max_dte=60,
    )
    df = generate_chain(config)
    # Pick 2026-01 expiry that exists on day 1.
    day1 = df["date"].min()
    day1_expiries = sorted(df.loc[df["date"] == day1, "expiry"].unique())
    assert len(day1_expiries) >= 2, "need at least 2 active expiries on day 1"
    target_expiry = day1_expiries[0]  # nearest expiry

    # That expiry must appear on every trading day strictly before its expiry date.
    for d in df["date"].unique():
        d = pd.Timestamp(d)
        if d >= target_expiry:
            continue
        rows = df[(df["date"] == d) & (df["expiry"] == target_expiry)]
        assert not rows.empty, f"expiry {target_expiry.date()} missing on trading day {d.date()}"


def test_per_contract_grouping_isolates_each_expiry() -> None:
    """change / historical_high / historical_low must group by contract_date.

    With fixed monthly expiries (3rd Wed), the 2026-01 and 2026-02 expiries
    share neither date nor month_week, so per-contract grouping is trivially
    correct. We still verify the invariant on diff-equality.
    """
    config = SyntheticChainConfig(
        start_date="2026-01-05",
        end_date="2026-02-20",
        n_strikes_per_side=3,
        max_dte=60,
    )
    df = generate_chain(config)

    # Invariant 1: per (contract_date, strike, option_type), change equals
    # settle - prev_settle (with first-day NaN).
    sorted_df = df.sort_values(["contract_date", "strike", "option_type", "date"])
    grp = sorted_df.groupby(["contract_date", "strike", "option_type"], sort=False)
    expected_change = grp["settle"].diff()
    pd.testing.assert_series_equal(
        sorted_df["change"].reset_index(drop=True),
        expected_change.reset_index(drop=True),
        check_names=False,
    )

    # Invariant 2: first observed row of every contract has change NaN.
    first_day_per_contract = grp.head(1)
    assert first_day_per_contract["change"].isna().all(), (
        "change should be NaN on the first observed day of each contract"
    )


def test_generate_chain_no_active_expiry_raises() -> None:
    """R6 F3: short max_dte window with no monthly 3rd-Wed expiry in reach
    must raise ValueError, not crash with KeyError later in the pipeline."""
    # 2026-01-22 is the day after 3rd Wed (2026-01-21); next monthly expiry
    # is 2026-02-18 (~27 days away). max_dte=7 → 0 active expiries.
    config = SyntheticChainConfig(
        start_date="2026-01-22",
        end_date="2026-01-23",
        n_strikes_per_side=2,
        max_dte=7,
    )
    with pytest.raises(ValueError, match="No active expiries"):
        generate_chain(config)


def test_generate_chain_no_business_days_raises() -> None:
    """All-weekend window has no bdate → raise."""
    # 2026-01-03 (Sat) to 2026-01-04 (Sun) — all weekend.
    config = SyntheticChainConfig(
        start_date="2026-01-03",
        end_date="2026-01-04",
        n_strikes_per_side=2,
        max_dte=14,
    )
    with pytest.raises(ValueError, match="No business days"):
        generate_chain(config)
