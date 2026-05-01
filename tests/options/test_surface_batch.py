"""Tests for src/options/surface_batch.py — Week 4 Day 4 batch fit + multiprocessing.

10 tests:

  1. test_batch_fit_surface_single_group_sequential (1 expiry, 1 date, n_workers=1)
  2. test_batch_fit_surface_multiple_groups_sorted_output (3 expiries, 2 dates)
  3. test_batch_fit_surface_n_workers_2_multiprocessing (2 workers; equal output to seq)
  4. test_batch_fit_surface_skip_below_min_strikes (min_strikes=5, group has 3 → insufficient)
  5. test_batch_fit_surface_skip_all_nan_iv_group (all-NaN iv → insufficient_data)
  6. test_batch_fit_surface_skip_unparseable_forward (NaN underlying → insufficient_data)
  7. test_batch_fit_surface_skip_expired_group (date == expiry → insufficient_data)
  8. test_batch_fit_surface_dedup_strikes_across_call_put (median IV per strike)
  9. test_batch_fit_surface_input_validation (empty / missing cols / bad n_workers)
 10. test_records_to_dataframe_roundtrip (records → DataFrame → records preserves data)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.options.surface_batch import (
    batch_fit_surface,
    dataframe_to_records,
    records_to_dataframe,
)


def _make_chain_one_smile(
    date: str = "2024-01-15",
    expiry: str = "2024-02-21",
    underlying: float = 17500.0,
    *,
    n_strikes: int = 11,
    atm_iv: float = 0.18,
    skew: float = -0.30,
    curvature: float = 0.40,
) -> pd.DataFrame:
    """One (date, expiry) group with synthetic V-shape smile.

    σ(k) = atm_iv + skew·k + curvature·k² (sane shape for SVI to fit).
    Returns a long-format DataFrame matching enriched chain schema:
      date / expiry / strike / option_type / iv / underlying.
    """
    strikes = np.linspace(underlying * 0.85, underlying * 1.15, n_strikes)
    k = np.log(strikes / underlying)
    ivs = atm_iv + skew * k + curvature * k**2
    rows = []
    for K, iv in zip(strikes, ivs, strict=True):
        # Both call+put per strike (test dedup median path)
        for opt in ("call", "put"):
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "expiry": pd.Timestamp(expiry),
                    "strike": float(K),
                    "option_type": opt,
                    "iv": float(iv),
                    "underlying": underlying,
                }
            )
    return pd.DataFrame(rows)


def _make_chain_multi_groups() -> pd.DataFrame:
    """3 expiries × 2 dates = 6 groups; each ~11 strikes × 2 option_type."""
    parts = []
    for date in ("2024-01-15", "2024-01-16"):
        for expiry, atm in (
            ("2024-02-21", 0.18),
            ("2024-03-20", 0.20),
            ("2024-06-19", 0.22),
        ):
            parts.append(
                _make_chain_one_smile(date=date, expiry=expiry, underlying=17500.0, atm_iv=atm)
            )
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------------------
# Sequential single-group
# ---------------------------------------------------------------------------


def test_batch_fit_surface_single_group_sequential() -> None:
    """1 group, n_workers=1: 1 record, model_type ∈ {svi, sabr, poly}, RMSE small."""
    chain = _make_chain_one_smile()
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert len(records) == 1
    rec = records[0]
    assert rec.date == "2024-01-15"
    assert rec.expiry == "2024-02-21"
    assert rec.model_type in ("svi", "sabr", "poly")
    assert rec.converged is True
    assert rec.n_points == 11  # dedup 11 strikes
    assert rec.in_sample_rmse < 0.02  # synthetic smile, expect tight fit
    assert rec.fit_time_ms >= 0
    assert rec.forward == pytest.approx(17500.0)
    assert 0.0 < rec.T < 1.0
    assert rec.params  # non-empty dict
    assert rec.error is None


# ---------------------------------------------------------------------------
# Multi-group sequential — sorted output
# ---------------------------------------------------------------------------


def test_batch_fit_surface_multiple_groups_sorted_output() -> None:
    """6 groups (2 dates × 3 expiries) → sorted by (date, expiry)."""
    chain = _make_chain_multi_groups()
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert len(records) == 6
    # Sort invariant: (date, expiry) ascending
    keys = [(r.date, r.expiry) for r in records]
    assert keys == sorted(keys)
    # Each group converged
    assert all(r.converged for r in records)


# ---------------------------------------------------------------------------
# Multiprocessing parity
# ---------------------------------------------------------------------------


def test_batch_fit_surface_n_workers_2_multiprocessing() -> None:
    """n_workers=2 ProcessPoolExecutor: same model_type + n_points as sequential."""
    chain = _make_chain_multi_groups()
    seq = batch_fit_surface(chain=chain, n_workers=1)
    par = batch_fit_surface(chain=chain, n_workers=2)
    assert len(seq) == len(par)
    for s, p in zip(seq, par, strict=True):
        assert s.date == p.date
        assert s.expiry == p.expiry
        assert s.model_type == p.model_type
        assert s.n_points == p.n_points
        # RMSE should be deterministic (no random seed in fits)
        if s.converged and p.converged:
            assert s.in_sample_rmse == pytest.approx(p.in_sample_rmse, rel=1e-9)


# ---------------------------------------------------------------------------
# min_strikes gating
# ---------------------------------------------------------------------------


def test_batch_fit_surface_skip_below_min_strikes() -> None:
    """3 strikes group + min_strikes=5 → record(model_type='insufficient_data')."""
    chain = _make_chain_one_smile(n_strikes=3)
    records = batch_fit_surface(chain=chain, n_workers=1, min_strikes=5)
    assert len(records) == 1
    assert records[0].model_type == "insufficient_data"
    assert records[0].converged is False
    assert "min_strikes" in (records[0].error or "")


# ---------------------------------------------------------------------------
# All-NaN IV group
# ---------------------------------------------------------------------------


def test_batch_fit_surface_skip_all_nan_iv_group() -> None:
    """Group with all-NaN iv → insufficient_data."""
    chain = _make_chain_one_smile()
    chain["iv"] = np.nan
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert len(records) == 1
    assert records[0].model_type == "insufficient_data"
    assert records[0].converged is False


# ---------------------------------------------------------------------------
# Unparseable forward
# ---------------------------------------------------------------------------


def test_batch_fit_surface_skip_unparseable_forward() -> None:
    """All-NaN underlying → insufficient_data (forward unknown)."""
    chain = _make_chain_one_smile()
    chain["underlying"] = np.nan
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert len(records) == 1
    assert records[0].model_type == "insufficient_data"


# ---------------------------------------------------------------------------
# Expired group (T <= 0)
# ---------------------------------------------------------------------------


def test_batch_fit_surface_skip_expired_group() -> None:
    """date == expiry → T <= 0 → insufficient_data (no fit attempted)."""
    chain = _make_chain_one_smile(date="2024-02-21", expiry="2024-02-21")
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert len(records) == 1
    assert records[0].model_type == "insufficient_data"


# ---------------------------------------------------------------------------
# Dedup median per strike across call+put
# ---------------------------------------------------------------------------


def test_batch_fit_surface_dedup_strikes_across_call_put() -> None:
    """11 strikes × 2 option_type = 22 raw rows → 11 deduped strikes used."""
    chain = _make_chain_one_smile(n_strikes=11)
    assert len(chain) == 22  # sanity (call+put per strike)
    records = batch_fit_surface(chain=chain, n_workers=1)
    assert records[0].n_points == 11


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_batch_fit_surface_input_validation() -> None:
    """Empty / missing cols / bad n_workers / bad min_strikes → ValueError."""
    with pytest.raises(ValueError, match="empty"):
        batch_fit_surface(chain=pd.DataFrame(), n_workers=1)

    chain = _make_chain_one_smile()
    bad = chain.drop(columns=["underlying"])
    with pytest.raises(ValueError, match="missing required columns"):
        batch_fit_surface(chain=bad, n_workers=1)

    with pytest.raises(ValueError, match="n_workers must be"):
        batch_fit_surface(chain=chain, n_workers=0)

    with pytest.raises(ValueError, match="min_strikes must be"):
        batch_fit_surface(chain=chain, n_workers=1, min_strikes=2)


# ---------------------------------------------------------------------------
# DataFrame roundtrip
# ---------------------------------------------------------------------------


def test_records_to_dataframe_roundtrip() -> None:
    """records → DataFrame → records preserves all field values (params/attempts)."""
    chain = _make_chain_multi_groups()
    records = batch_fit_surface(chain=chain, n_workers=1)
    df = records_to_dataframe(records)
    assert len(df) == len(records)
    assert set(df.columns) == {
        "date",
        "expiry",
        "model_type",
        "converged",
        "n_points",
        "in_sample_rmse",
        "fit_time_ms",
        "forward",
        "T",
        "params_json",
        "attempts_json",
        "error",
    }
    restored = dataframe_to_records(df)
    assert len(restored) == len(records)
    for r0, r1 in zip(records, restored, strict=True):
        assert r0.date == r1.date
        assert r0.expiry == r1.expiry
        assert r0.model_type == r1.model_type
        assert r0.converged == r1.converged
        assert r0.n_points == r1.n_points
        assert r0.params == r1.params
        # attempts: restored may have float keys (rmse) preserved
        assert len(r0.attempts) == len(r1.attempts)
