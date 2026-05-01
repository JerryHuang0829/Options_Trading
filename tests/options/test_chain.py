"""Tests for src/options/chain.py.

Covers:
  - test_filter_by_dte: inclusive DTE filtering + bound validation
  - test_select_by_delta: closest-delta selection (lenient default)
  - test_select_by_delta_lenient_vs_strict: strict mode raises when no
    strike within max_delta_diff
  - test_pivot_to_chain: long → wide pivot + multi-date error
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.options.chain import filter_by_dte, pivot_to_chain, select_by_delta


def _inline_chain() -> pd.DataFrame:
    """Hand-crafted minimal enriched chain (Day 4 synthetic.py not yet ready).

    Single date 2026-04-25; two expiries (15-DTE, 30-DTE); 3 strikes each;
    call + put. Total 12 rows. Delta values are hand-picked to make
    select_by_delta predictable.
    """
    date = pd.Timestamp("2026-04-25")
    rows = [
        # 15-DTE (2026-05-10), strikes 16500 / 16800 / 17100
        # call deltas around 0.20 / 0.50 / 0.80; put deltas mirror
        ("2026-05-10", 16500, "call", 0.80, 350.0, 0.18, 1500),
        ("2026-05-10", 16500, "put", -0.20, 50.0, 0.18, 1200),
        ("2026-05-10", 16800, "call", 0.50, 150.0, 0.18, 3000),
        ("2026-05-10", 16800, "put", -0.50, 150.0, 0.18, 3000),
        ("2026-05-10", 17100, "call", 0.20, 50.0, 0.18, 1100),
        ("2026-05-10", 17100, "put", -0.80, 350.0, 0.18, 1300),
        # 30-DTE (2026-05-25), same strikes
        ("2026-05-25", 16500, "call", 0.75, 400.0, 0.20, 800),
        ("2026-05-25", 16500, "put", -0.25, 90.0, 0.20, 700),
        ("2026-05-25", 16800, "call", 0.50, 200.0, 0.20, 2500),
        ("2026-05-25", 16800, "put", -0.50, 200.0, 0.20, 2500),
        ("2026-05-25", 17100, "call", 0.25, 80.0, 0.20, 600),
        ("2026-05-25", 17100, "put", -0.75, 380.0, 0.20, 750),
    ]
    df = pd.DataFrame(
        rows,
        columns=["expiry", "strike", "option_type", "delta", "settle", "iv", "open_interest"],
    )
    df["expiry"] = pd.to_datetime(df["expiry"])
    df["date"] = date
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df["underlying"] = 16800.0
    df["volume"] = 1000  # placeholder
    return df


# ---------- filter_by_dte ----------


def test_filter_by_dte() -> None:
    chain = _inline_chain()
    # 15-DTE rows: 6; 30-DTE rows: 6.
    only_15 = filter_by_dte(chain, 14, 16)
    assert len(only_15) == 6
    assert all(only_15["dte"] == 15)

    both = filter_by_dte(chain, 14, 31)
    assert len(both) == 12

    none_match = filter_by_dte(chain, 60, 90)
    assert len(none_match) == 0


def test_filter_by_dte_validation() -> None:
    chain = _inline_chain()
    with pytest.raises(ValueError, match="min_dte"):
        filter_by_dte(chain, 30, 14)
    with pytest.raises(ValueError, match=">= 0"):
        filter_by_dte(chain, -5, 30)

    chain_no_dte = chain.drop(columns=["dte"])
    with pytest.raises(KeyError, match="dte"):
        filter_by_dte(chain_no_dte, 0, 30)


# ---------- select_by_delta ----------


def test_select_by_delta_call() -> None:
    """target=0.16 should pick the OTM call (delta closest to 0.16)."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    row = select_by_delta(only_15, target_delta=0.20, option_type="call")
    assert row["strike"] == 17100
    assert row["delta"] == pytest.approx(0.20)


def test_select_by_delta_put_signed() -> None:
    """Put uses signed convention: target_delta=-0.20 picks 0.20-delta put."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    row = select_by_delta(only_15, target_delta=-0.20, option_type="put")
    assert row["strike"] == 16500
    assert row["delta"] == pytest.approx(-0.20)


def test_select_by_delta_lenient_returns_closest() -> None:
    """No tolerance + far target → still returns closest (lenient default)."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    # Farthest available call delta from 0.10 is 0.20 (diff 0.10) — still returned.
    row = select_by_delta(only_15, target_delta=0.10, option_type="call")
    assert row["strike"] == 17100  # delta=0.20, closest to 0.10


def test_select_by_delta_strict_raises() -> None:
    """Strict mode: closest exceeds max_delta_diff → raise."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    with pytest.raises(ValueError, match="max_delta_diff"):
        select_by_delta(only_15, target_delta=0.10, option_type="call", max_delta_diff=0.05)


def test_select_by_delta_sign_mismatch() -> None:
    """Sign convention violation: positive target on put / negative on call."""
    chain = _inline_chain()
    with pytest.raises(ValueError, match="put target_delta must be <= 0"):
        select_by_delta(chain, target_delta=0.16, option_type="put")
    with pytest.raises(ValueError, match="call target_delta must be >= 0"):
        select_by_delta(chain, target_delta=-0.16, option_type="call")


def test_select_by_delta_empty_subset() -> None:
    """No rows of requested option_type → ValueError."""
    chain = _inline_chain()
    only_calls = chain.loc[chain["option_type"] == "call"]
    with pytest.raises(ValueError, match="no 'put' rows"):
        select_by_delta(only_calls, target_delta=-0.20, option_type="put")


def test_select_by_delta_validation() -> None:
    chain = _inline_chain()
    with pytest.raises(ValueError, match="option_type"):
        select_by_delta(chain, 0.16, "Call")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_delta_diff"):
        select_by_delta(chain, 0.16, "call", max_delta_diff=-0.01)
    chain_no_delta = chain.drop(columns=["delta"])
    with pytest.raises(KeyError, match="delta"):
        select_by_delta(chain_no_delta, 0.16, "call")


# ---------- pivot_to_chain ----------


def test_pivot_to_chain() -> None:
    chain = _inline_chain()
    only_one_day = chain.loc[chain["date"] == pd.Timestamp("2026-04-25")]
    pivoted = pivot_to_chain(only_one_day)
    # Index = 3 strikes; Columns MultiIndex = 2 expiries × 2 option_types = 4.
    assert len(pivoted.index) == 3
    assert len(pivoted.columns) == 4
    # Verify a known cell: strike=16800, 15-DTE (2026-05-10), call → settle=150.0.
    val = pivoted.loc[16800, (pd.Timestamp("2026-05-10"), "call")]
    assert val == pytest.approx(150.0)


def test_pivot_to_chain_multi_date_raises() -> None:
    chain = _inline_chain()
    extra = chain.copy()
    extra["date"] = pd.Timestamp("2026-04-26")
    multi = pd.concat([chain, extra], ignore_index=True)
    with pytest.raises(ValueError, match="multiple dates"):
        pivot_to_chain(multi)


def test_pivot_to_chain_empty_raises() -> None:
    empty = pd.DataFrame(columns=["date", "expiry", "strike", "option_type", "settle"])
    with pytest.raises(ValueError, match="empty"):
        pivot_to_chain(empty)


def test_pivot_to_chain_missing_columns() -> None:
    chain = _inline_chain()
    with pytest.raises(KeyError, match="settle"):
        pivot_to_chain(chain.drop(columns=["settle"]))


# ---------- Day 7: NaN / duplicate guard ----------


def test_select_by_delta_raises_on_nan_default() -> None:
    """Default raise_on_nan=True: any NaN delta → ValueError."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    chain_with_nan = only_15.copy()
    # Inject NaN into one call delta row.
    chain_with_nan.loc[chain_with_nan["option_type"] == "call", "delta"] = (
        chain_with_nan.loc[chain_with_nan["option_type"] == "call", "delta"]
        .reset_index(drop=True)
        .where(lambda s: s.index != 0)  # first call row → NaN
        .values
    )
    with pytest.raises(ValueError, match="NaN"):
        select_by_delta(chain_with_nan, target_delta=0.20, option_type="call")


def test_select_by_delta_skip_nan_when_raise_off() -> None:
    """raise_on_nan=False drops NaN rows; selection still works on remaining."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    chain_with_nan = only_15.copy()
    # NaN out the OTM call (delta=0.20) — selection should fall back to 0.50 strike.
    chain_with_nan.loc[
        (chain_with_nan["option_type"] == "call") & (chain_with_nan["strike"] == 17100),
        "delta",
    ] = float("nan")
    row = select_by_delta(chain_with_nan, target_delta=0.20, option_type="call", raise_on_nan=False)
    # 17100 dropped → next-closest to 0.20 from {0.50, 0.80} is 0.50 (strike 16800).
    assert row["strike"] == 16800


def test_select_by_delta_all_nan_after_drop_raises() -> None:
    """raise_on_nan=False but every requested-side row is NaN → still ValueError."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    chain_with_nan = only_15.copy()
    chain_with_nan.loc[chain_with_nan["option_type"] == "call", "delta"] = float("nan")
    with pytest.raises(ValueError, match="NaN delta"):
        select_by_delta(chain_with_nan, target_delta=0.20, option_type="call", raise_on_nan=False)


def test_select_by_delta_duplicate_warns_by_default() -> None:
    """Duplicate (strike, expiry) → UserWarning; selection still proceeds."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    dup = pd.concat([only_15, only_15.iloc[[0]]], ignore_index=True)  # duplicate first row
    import warnings as _warnings

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        select_by_delta(dup, target_delta=0.20, option_type="call")
    assert any("duplicate" in str(w.message) for w in caught)


def test_select_by_delta_duplicate_strict_raises() -> None:
    """raise_on_duplicate=True → strict mode raises."""
    chain = _inline_chain()
    only_15 = filter_by_dte(chain, 14, 16)
    dup = pd.concat([only_15, only_15.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        select_by_delta(dup, target_delta=0.20, option_type="call", raise_on_duplicate=True)


def test_pivot_to_chain_strict_raises_on_duplicate() -> None:
    """raise_on_duplicate=True → strict mode raises on duplicate rows."""
    chain = _inline_chain()
    only_one_day = chain.loc[chain["date"] == pd.Timestamp("2026-04-25")]
    dup = pd.concat([only_one_day, only_one_day.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        pivot_to_chain(dup, raise_on_duplicate=True)


def test_pivot_to_chain_lenient_default_silent() -> None:
    """Default raise_on_duplicate=False: pivot silently aggregates first."""
    chain = _inline_chain()
    only_one_day = chain.loc[chain["date"] == pd.Timestamp("2026-04-25")]
    dup = pd.concat([only_one_day, only_one_day.iloc[[0]]], ignore_index=True)
    # Should not raise.
    out = pivot_to_chain(dup)
    assert not out.empty
