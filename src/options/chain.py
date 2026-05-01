"""Option chain utilities: filtering, pivoting, strike selection.

Operates on **enriched** chain DataFrames — caller must pre-compute ``iv`` /
``delta`` / ``dte`` / ``underlying`` before passing in. Rationale: an IC
backtest queries the chain hundreds of times per day; computing IV on every
``select_by_delta`` call (30+ seconds/day) is unacceptable. The pipeline
``synthetic.py`` / ``backtest engine`` populates these columns once when the
chain is loaded; ``chain.py`` is then a pure lookup layer.

Per-row enriched schema additions:
  - ``iv``         : implied volatility per row (annualised decimal)
  - ``delta``      : signed Greek delta per row (call > 0; put < 0)
  - ``dte``        : (expiry - date).days  (helper; saves repeated arithmetic)
  - ``underlying`` : spot price for the trading day

Delta-based strike selection (the most common IC / Vertical open-close
primitive) lives here rather than in each strategy to keep strategy code
declarative.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd

if TYPE_CHECKING:
    pass

OptionType = Literal["call", "put"]


@dataclass(frozen=True)
class ChainQuote:
    """Single option quote in a chain snapshot.

    Typed structure to avoid dict-typo bugs when strategies pass quotes around.
    Codex Round 1 recommended dataclass over dict for execution-adjacent data.

    Attributes:
        date: Snapshot date.
        expiry: Contract expiry date.
        strike: Strike price.
        option_type: ``"call"`` or ``"put"``.
        settle: Daily settle price.
        bid: Best bid (can be NaN if bulk CSV lacks bid/ask).
        ask: Best ask (can be NaN if bulk CSV lacks bid/ask).
        iv: Implied volatility (computed; NaN if not solvable).
        volume: Day volume.
        open_interest: End-of-day OI.
    """

    date: pd.Timestamp
    expiry: pd.Timestamp
    strike: int
    option_type: str
    settle: float
    bid: float
    ask: float
    iv: float
    volume: int
    open_interest: int


def filter_by_dte(chain: pd.DataFrame, min_dte: int, max_dte: int) -> pd.DataFrame:
    """Return chain rows whose ``dte`` ∈ ``[min_dte, max_dte]`` (inclusive).

    Args:
        chain: Enriched chain DataFrame containing a ``dte`` column.
        min_dte: Lower bound (inclusive) of DTE in days.
        max_dte: Upper bound (inclusive) of DTE in days.

    Returns:
        Filtered DataFrame (may be empty); preserves all original columns.

    Raises:
        ValueError: If ``min_dte > max_dte`` or either bound is negative.
        KeyError: If chain does not contain a ``dte`` column (caller forgot
            to pre-compute it; see module docstring).
    """
    if min_dte < 0 or max_dte < 0:
        raise ValueError(f"DTE bounds must be >= 0, got [{min_dte}, {max_dte}]")
    if min_dte > max_dte:
        raise ValueError(f"min_dte ({min_dte}) > max_dte ({max_dte})")
    if "dte" not in chain.columns:
        raise KeyError("chain missing 'dte' column; caller must pre-compute (expiry - date).days")
    mask = (chain["dte"] >= min_dte) & (chain["dte"] <= max_dte)
    return chain.loc[mask].copy()


def select_by_delta(
    chain: pd.DataFrame,
    target_delta: float,
    option_type: OptionType,
    *,
    max_delta_diff: float | None = None,
    raise_on_nan: bool = True,
    raise_on_duplicate: bool = False,
) -> pd.Series:
    """Return the chain row whose delta is closest to ``target_delta``.

    Args:
        chain: Enriched chain DataFrame with ``delta`` and ``option_type`` cols.
        target_delta: Signed delta target. Use 0.16 for 0.16-delta call;
            use **-0.16 for 0.16-delta put** (signed convention).
        option_type: ``"call"`` or ``"put"``; chain is filtered first.
        max_delta_diff: Optional strict tolerance. If provided and the closest
            match's |delta - target_delta| exceeds it, raises ValueError.
            Default ``None`` returns closest unconditionally (lenient mode
            for backtest where occasional poor strike grid is acceptable).
        raise_on_nan: If True (default), raise on ``delta`` NaN. If False,
            silently drop NaN rows before idxmin (lenient mode for sparse
            real-world chains where a few strikes lack quotes).
        raise_on_duplicate: If True, raise when more than one row exists at
            the same (strike, expiry). Default False emits a UserWarning and
            returns the first match (stable over input order).

    Returns:
        Single ``pd.Series`` row from the chain.

    Raises:
        ValueError: If chain has no rows of the requested option_type, or
            (in strict mode) the closest |delta - target_delta| exceeds
            ``max_delta_diff``, or sign mismatch, or (raise_on_nan=True) any
            ``delta`` is NaN, or (raise_on_duplicate=True) duplicate
            (strike, expiry).
        KeyError: If chain missing ``delta`` or ``option_type`` columns.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    if option_type == "call" and target_delta < 0:
        raise ValueError(f"call target_delta must be >= 0 (signed convention), got {target_delta}")
    if option_type == "put" and target_delta > 0:
        raise ValueError(
            f"put target_delta must be <= 0 (signed convention; "
            f"use -0.16 for 0.16-delta put), got {target_delta}"
        )
    if max_delta_diff is not None and max_delta_diff < 0:
        raise ValueError(f"max_delta_diff must be >= 0, got {max_delta_diff}")

    if "delta" not in chain.columns or "option_type" not in chain.columns:
        raise KeyError(
            "chain missing 'delta' or 'option_type' column "
            "(caller must pre-compute Greeks; see module docstring)"
        )

    subset = chain.loc[chain["option_type"] == option_type]
    if subset.empty:
        raise ValueError(f"chain has no {option_type!r} rows after filter")

    # NaN guard: synthetic chains never produce NaN delta, but real TAIFEX
    # chains can have missing IV → missing delta on illiquid strikes. Default
    # behaviour is strict (catches silent bugs); set raise_on_nan=False for
    # tolerant mode that drops NaN rows before selection.
    n_nan = int(subset["delta"].isna().sum())
    if n_nan > 0:
        if raise_on_nan:
            raise ValueError(
                f"chain['delta'] has {n_nan} NaN row(s) for option_type={option_type!r}; "
                f"set raise_on_nan=False to drop them"
            )
        subset = subset.loc[subset["delta"].notna()]
        if subset.empty:
            raise ValueError(
                f"all {option_type!r} rows have NaN delta after dropping; cannot select"
            )

    # Duplicate guard: if (strike, expiry) appears multiple times the chain
    # is malformed (e.g. accidental concat of two snapshots). Default emits
    # a UserWarning and falls through to idxmin(); strict mode raises.
    if {"strike", "expiry"}.issubset(subset.columns):
        dup_mask = subset.duplicated(subset=["strike", "expiry"], keep=False)
        n_dup = int(dup_mask.sum())
        if n_dup > 0:
            msg = (
                f"chain has {n_dup} duplicate (strike, expiry) row(s) for "
                f"option_type={option_type!r}"
            )
            if raise_on_duplicate:
                raise ValueError(msg + "; set raise_on_duplicate=False to allow")
            warnings.warn(msg, UserWarning, stacklevel=2)

    abs_diff = (subset["delta"] - target_delta).abs()
    closest_idx = abs_diff.idxmin()
    closest_diff = abs_diff.loc[closest_idx]

    if max_delta_diff is not None and closest_diff > max_delta_diff:
        raise ValueError(
            f"closest |delta - target_delta| = {closest_diff:.4f} > "
            f"max_delta_diff = {max_delta_diff:.4f}; no acceptable strike"
        )

    # idxmin() returns scalar label → subset.loc[label] is always a Series
    # (only DataFrame when label is a list). Cast for mypy / pandas-stubs.
    return cast("pd.Series", subset.loc[closest_idx])


def pivot_to_chain(
    long_df: pd.DataFrame,
    *,
    raise_on_duplicate: bool = False,
) -> pd.DataFrame:
    """Pivot long-format option rows into a wide strike-indexed chain view.

    Output:
        Index = ``strike``; columns = MultiIndex ``(expiry, option_type)``;
        values = ``settle``. Use raw long_df for bid/ask/iv/delta analysis;
        this view is for visualisation / quick inspection only.

    Args:
        long_df: Long-format chain for **a single trading day**. Must contain
            ``date``, ``expiry``, ``strike``, ``option_type``, ``settle``.
        raise_on_duplicate: If True, raise when (strike, expiry, option_type)
            duplicates exist (default False — pivot silently aggregates by
            ``aggfunc='first'``, which can hide silent bugs in concat'd
            chains; strict mode forces caller to dedup explicitly).

    Returns:
        Wide DataFrame.

    Raises:
        ValueError: If ``long_df`` contains rows from multiple dates (ambiguous
            strike index), is empty, or (strict mode) has duplicate
            (strike, expiry, option_type) rows.
        KeyError: If required columns are missing.
    """
    required = {"date", "expiry", "strike", "option_type", "settle"}
    missing = required - set(long_df.columns)
    if missing:
        raise KeyError(f"long_df missing columns: {sorted(missing)}")
    if long_df.empty:
        raise ValueError("long_df is empty")
    unique_dates = long_df["date"].unique()
    if len(unique_dates) > 1:
        raise ValueError(
            f"long_df spans multiple dates ({len(unique_dates)}); "
            f"filter to single date before pivot"
        )

    if raise_on_duplicate:
        dup_mask = long_df.duplicated(subset=["strike", "expiry", "option_type"], keep=False)
        n_dup = int(dup_mask.sum())
        if n_dup > 0:
            raise ValueError(
                f"long_df has {n_dup} duplicate (strike, expiry, option_type) row(s); "
                f"caller must dedup before pivot in strict mode"
            )

    return long_df.pivot_table(
        index="strike",
        columns=["expiry", "option_type"],
        values="settle",
        aggfunc="first",
    )
