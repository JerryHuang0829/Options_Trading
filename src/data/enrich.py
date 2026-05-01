"""STRATEGY_VIEW → engine-ready enrichment (D-soft Day 4, R10.13 v6 Phase 1).

Phase 1 cols added (this module): dte / underlying / q_pit / q_pit_source /
q_pit_audit_flags. Phase 2 (Day 5) adds iv / delta / can_buy / can_sell.

q PIT (R10.5 P2 Codex caveat acknowledged):
  - Solved daily from put-call parity at front-month ATM strike:
        C - P = S·e^(-qT) - K·e^(-rT)   →   q = -ln((C-P + K·e^(-rT)) / S) / T
  - **q_pit feeds AUDIT ONLY**. Tradable signal generation MUST use
    q_source='fallback' = DIVIDEND_YIELD_DEFAULT (config/constants.py 0.035).
  - PIT validity caveats:
    1. ^TWII is PRICE INDEX (not total return) → systematic q upward bias
    2. cash close ≠ TXO settle time → intraday gap unmodelled
    3. ATM mid uses (bid+ask)/2 → wide spread days have noisy q
    4. Front-month bias: short DTE T → variance ↑ (small numerator amplified)
  - Audit flags per day: 'ok' / 'missing_quote' / 'pcp_invalid' /
    'negative_q' / 'out_of_range' / 'no_atm_pair'

Schema:
  - In: STRATEGY_VIEW 10 col (date/expiry/strike/option_type/settle/close/
    bid/ask/volume/open_interest)
  - Out (after enrich_phase_1): + dte, underlying, q_pit, q_pit_source,
    q_pit_audit_flags  (15 col total — Phase 2 Day 5 adds iv/delta/can_*)
"""

from __future__ import annotations

import contextlib
import math
from typing import Literal, cast

import numpy as np
import pandas as pd

from config.constants import (
    CALENDAR_DAYS_PER_YEAR,
    DIVIDEND_YIELD_DEFAULT,
    RISK_FREE_RATE_DEFAULT,
)
from src.options.greeks import delta as bsm_delta
from src.options.pricing import bsm_price, implied_vol
from src.options.surface_batch import SurfaceFitRecord
from src.options.vol_surface import sabr_lognormal_iv, svi_raw

# Out-of-range guard for solved q: |q| > Q_PIT_ABS_CAP → flag 'out_of_range'.
# 0.15 chosen because TAIEX trailing 5yr div yield range is ~2-5%; |0.15|
# is 3× the upper bound, large enough to catch data errors without false
# positives on ex-dividend cluster days.
Q_PIT_ABS_CAP: float = 0.15


def add_dte(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'dte' col = (expiry - date).days (calendar days, BSM convention).

    Args:
        df: STRATEGY_VIEW DataFrame with 'date' and 'expiry' datetime64 cols.

    Returns: copy with int64 'dte' col appended.

    Raises:
        ValueError: missing date/expiry, or any negative dte (expiry before date).
    """
    if "date" not in df.columns or "expiry" not in df.columns:
        raise ValueError("add_dte: requires 'date' and 'expiry' columns")
    out = df.copy()
    out["dte"] = (out["expiry"] - out["date"]).dt.days.astype("int64")
    if (out["dte"] < 0).any():
        bad = out[out["dte"] < 0][["date", "expiry", "dte"]].head(3)
        raise ValueError(f"add_dte: negative dte found (expiry < date). sample={bad!r}")
    return out


def add_underlying(
    df: pd.DataFrame,
    spot_series: pd.Series,
    *,
    missing_policy: Literal["raise", "forward_fill", "skip"] = "raise",
) -> pd.DataFrame:
    """Left-join 'underlying' col from spot_series indexed by date.

    Codex R11.6 P1: TAIFEX cache 有 1963 days，TAIEX (yfinance ^TWII) 漏抓 3 天
    (2018-12-22 / 2019-09-09 / 2021-04-06，多為週六調整補上班 / yfinance漏)
    → full-range pipeline raise. 加 missing_policy enum 處理。

    Args:
        df: STRATEGY_VIEW DataFrame with 'date' datetime64 col.
        spot_series: pd.Series indexed by date (datetime64), values = spot close.
        missing_policy:
            - 'raise' (預設, R10.10 strict): 任何 missing date raise (production 紅線)
            - 'forward_fill': 用最近一個前序交易日 close 補；audit log 紀錄
            - 'skip': 把 missing date 整 row 從 df 剔除；audit log 紀錄

    Returns:
        copy with float64 'underlying' col + (forward_fill/skip 模式) audit dict
        attached to ``out.attrs['underlying_missing_audit']``.

    Raises:
        ValueError: missing_policy='raise' 時有 date 缺；invalid policy.
    """
    if "date" not in df.columns:
        raise ValueError("add_underlying: df requires 'date' column")
    if not isinstance(spot_series, pd.Series):
        raise TypeError(
            f"add_underlying: spot_series must be pd.Series, got {type(spot_series).__name__}"
        )
    if missing_policy not in ("raise", "forward_fill", "skip"):
        raise ValueError(
            f"add_underlying: missing_policy must be 'raise'|'forward_fill'|'skip', "
            f"got {missing_policy!r}"
        )

    out = df.copy()
    spot_aligned = spot_series.reindex(out["date"]).reset_index(drop=True)
    missing_mask = spot_aligned.isna()

    if not missing_mask.any():
        out["underlying"] = spot_aligned.astype("float64").values
        return out

    missing_dates = sorted(out.loc[missing_mask, "date"].dt.strftime("%Y-%m-%d").unique())

    if missing_policy == "raise":
        raise ValueError(
            f"add_underlying: {len(missing_dates)} dates missing in spot_series "
            f"(missing_policy='raise'). first_missing={missing_dates[:5]}. "
            f"For production full-range pipeline use missing_policy='forward_fill' "
            f"with explicit audit log review."
        )

    if missing_policy == "skip":
        keep_mask = ~missing_mask
        keep_idx = np.where(keep_mask.to_numpy())[0]
        out = out.iloc[keep_idx].reset_index(drop=True)
        out["underlying"] = spot_aligned.iloc[keep_idx].astype("float64").to_numpy()
        out.attrs["underlying_missing_audit"] = {
            "policy": "skip",
            "missing_dates": missing_dates,
            "n_rows_dropped": int(missing_mask.sum()),
        }
        return out

    # forward_fill: 用 spot_series 前一個交易日 close 補；audit 記原 NaN 哪幾天
    spot_sorted = spot_series.sort_index()
    fill_map: dict[pd.Timestamp, tuple[pd.Timestamp, float]] = {}
    for missing_date_str in missing_dates:
        target = pd.Timestamp(missing_date_str)
        prior = spot_sorted.loc[spot_sorted.index < target]
        if prior.empty:
            raise ValueError(
                f"add_underlying: forward_fill failed for {missing_date_str} "
                f"(no prior date in spot_series; would extrapolate from nothing)"
            )
        fill_value = float(prior.iloc[-1])
        fill_map[target] = (prior.index[-1], fill_value)

    spot_filled = spot_aligned.copy()
    for i, missing_flag in enumerate(missing_mask):
        if missing_flag:
            target_date = pd.Timestamp(out["date"].iloc[i])
            _, fill_value = fill_map[target_date]
            spot_filled.iloc[i] = fill_value

    out["underlying"] = spot_filled.astype("float64").values
    out.attrs["underlying_missing_audit"] = {
        "policy": "forward_fill",
        "missing_dates": missing_dates,
        "n_rows_filled": int(missing_mask.sum()),
        "fill_sources": {
            d.strftime("%Y-%m-%d"): {
                "filled_from": src.strftime("%Y-%m-%d"),
                "fill_value": val,
            }
            for d, (src, val) in fill_map.items()
        },
    }
    return out


def _atm_mid(row: pd.Series) -> float:
    """Mid price for ATM solve: (bid+ask)/2 if both finite, else 'close' fallback."""
    bid = row.get("bid", float("nan"))
    ask = row.get("ask", float("nan"))
    if pd.notna(bid) and pd.notna(ask):
        return (float(bid) + float(ask)) / 2.0
    close = row.get("close", float("nan"))
    return float(close) if pd.notna(close) else float("nan")


def _solve_q_pit_one_day(
    *,
    date: pd.Timestamp,
    spot: float,
    day_chain: pd.DataFrame,
    r: float,
) -> tuple[float, str, dict]:
    """Solve q from put-call parity at front-month ATM strike.

    Returns (q, audit_flag, audit_detail). audit_flag ∈
    {'ok', 'no_atm_pair', 'missing_quote', 'pcp_invalid', 'negative_q', 'out_of_range'}.

    Algorithm:
      1. Filter to front-month: smallest expiry > date with both call+put
         present at ≥1 shared strike.
      2. ATM strike: shared strike whose |strike - spot| is minimum.
      3. C, P = _atm_mid(call_row), _atm_mid(put_row).
      4. T = (expiry - date).days / 365  (calendar days, BSM convention)
      5. q = -ln((C - P + K·e^(-rT)) / S) / T

    Failure modes set audit_flag and return q=NaN.
    """
    detail: dict = {
        "date": date,
        "expiry_used": pd.NaT,
        "strike_used": float("nan"),
        "S": spot,
        "C": float("nan"),
        "P": float("nan"),
        "T": float("nan"),
    }

    # Step 1: candidate expiries with shared call+put strikes
    future = day_chain[day_chain["expiry"] > date]
    if future.empty:
        return float("nan"), "no_atm_pair", detail

    expiries_sorted = sorted(future["expiry"].unique())
    chosen_expiry = None
    chosen_strike = None
    for exp in expiries_sorted:
        exp_chain = future[future["expiry"] == exp]
        calls = set(exp_chain[exp_chain["option_type"] == "call"]["strike"].unique())
        puts = set(exp_chain[exp_chain["option_type"] == "put"]["strike"].unique())
        shared = calls & puts
        if not shared:
            continue
        atm_strike = min(shared, key=lambda k: abs(k - spot))
        chosen_expiry = exp
        chosen_strike = atm_strike
        break

    if chosen_expiry is None or chosen_strike is None:
        return float("nan"), "no_atm_pair", detail

    detail["expiry_used"] = chosen_expiry
    detail["strike_used"] = chosen_strike

    # Step 2/3: pick C/P mid prices at ATM strike
    exp_chain = future[future["expiry"] == chosen_expiry]
    call_row = exp_chain[
        (exp_chain["option_type"] == "call") & (exp_chain["strike"] == chosen_strike)
    ]
    put_row = exp_chain[
        (exp_chain["option_type"] == "put") & (exp_chain["strike"] == chosen_strike)
    ]
    if call_row.empty or put_row.empty:
        return float("nan"), "no_atm_pair", detail
    C = _atm_mid(call_row.iloc[0])
    P = _atm_mid(put_row.iloc[0])
    detail["C"] = C
    detail["P"] = P
    if not (math.isfinite(C) and math.isfinite(P)):
        return float("nan"), "missing_quote", detail

    # Step 4: T (calendar days)
    T_days = (chosen_expiry - date).days
    if T_days <= 0:
        return float("nan"), "pcp_invalid", detail
    T = T_days / 365.0
    detail["T"] = T

    # Step 5: q = -ln((C-P + K·e^(-rT)) / S) / T
    discount_K = chosen_strike * math.exp(-r * T)
    numerator = C - P + discount_K
    if numerator <= 0 or spot <= 0:
        return float("nan"), "pcp_invalid", detail
    ratio = numerator / spot
    if ratio <= 0:
        return float("nan"), "pcp_invalid", detail
    q = -math.log(ratio) / T

    if q < 0:
        return q, "negative_q", detail
    if abs(q) > Q_PIT_ABS_CAP:
        return q, "out_of_range", detail
    return q, "ok", detail


def add_q_pit(
    df: pd.DataFrame,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    on_solve_fail: Literal["nan", "fallback"] = "nan",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add q_pit / q_pit_source / q_pit_audit_flags cols + return audit DataFrame.

    Per-date solve from put-call parity at front-month ATM strike (see
    _solve_q_pit_one_day). Same q_pit value broadcast to all rows of that date.

    Args:
        df: must contain 'date', 'expiry', 'strike', 'option_type', 'underlying',
            'bid', 'ask', 'close' cols (i.e. add_underlying already applied).
        r: annualised risk-free rate (decimal).
        on_solve_fail: 'nan' (default; q_pit=NaN, source='solve_failed') or
                       'fallback' (q_pit=DIVIDEND_YIELD_DEFAULT,
                       source='fallback'). 'nan' is preferred — caller decides
                       downstream; 'fallback' is convenience for smoke runs.

    Returns:
        (enriched_df, audit_df).
        enriched_df: copy of df + 3 cols.
        audit_df: one row per date with cols [date, expiry_used, strike_used,
                  S, C, P, T, q_pit, audit_flag].

    Raises:
        ValueError: missing required cols.
    """
    required = {"date", "expiry", "strike", "option_type", "underlying", "bid", "ask", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_q_pit: missing required cols: {sorted(missing)}")
    if on_solve_fail not in ("nan", "fallback"):
        raise ValueError(
            f"add_q_pit: on_solve_fail must be 'nan'|'fallback', got {on_solve_fail!r}"
        )

    audit_rows: list[dict] = []
    q_per_date: dict[pd.Timestamp, float] = {}
    source_per_date: dict[pd.Timestamp, str] = {}
    flag_per_date: dict[pd.Timestamp, str] = {}

    for date_raw, day_chain in df.groupby("date"):
        date_ts = cast(pd.Timestamp, date_raw)  # groupby key 實際是 Timestamp 但 stub 推 union
        spot = float(day_chain["underlying"].iloc[0])
        q, flag, detail = _solve_q_pit_one_day(date=date_ts, spot=spot, day_chain=day_chain, r=r)
        if not math.isfinite(q):
            if on_solve_fail == "fallback":
                q_value = DIVIDEND_YIELD_DEFAULT
                source = "fallback"
            else:
                q_value = float("nan")
                source = "solve_failed"
        else:
            q_value = q
            source = "pcp"
        q_per_date[date_ts] = q_value
        source_per_date[date_ts] = source
        flag_per_date[date_ts] = flag
        audit_rows.append(
            {
                "date": date_ts,
                "expiry_used": detail["expiry_used"],
                "strike_used": detail["strike_used"],
                "S": detail["S"],
                "C": detail["C"],
                "P": detail["P"],
                "T": detail["T"],
                "q_pit": q_value,
                "audit_flag": flag,
            }
        )

    out = df.copy()
    out["q_pit"] = out["date"].map(q_per_date).astype("float64")
    out["q_pit_source"] = out["date"].map(source_per_date).astype("string")
    out["q_pit_audit_flags"] = out["date"].map(flag_per_date).astype("string")
    audit_df = pd.DataFrame(audit_rows)
    return out, audit_df


def enrich_phase_1(
    strategy_view_df: pd.DataFrame,
    spot_series: pd.Series,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    on_q_solve_fail: Literal["nan", "fallback"] = "nan",
    spot_missing_policy: Literal["raise", "forward_fill", "skip"] = "raise",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Day 4 enrich Phase 1 pipeline: + dte, underlying, q_pit*.

    Steps:
      1. add_underlying(df, spot_series, missing_policy=spot_missing_policy)
      2. add_dte(df)                      → 'dte' col
      3. add_q_pit(df, r=r, ...)          → q_pit / q_pit_source / q_pit_audit_flags
                                            + per-date audit DataFrame

    Args:
        spot_missing_policy: R11.6 P1 修法。'raise' (預設 strict) /
            'forward_fill' (前序交易日 close 補 + audit) / 'skip' (剔除 row).
            Full-range pipeline (1963 days TXO + 2017 days TAIEX) 有 3 day gap
            (2018-12-22 / 2019-09-09 / 2021-04-06)，production 應用 forward_fill.

    Returns: (enriched_df, q_pit_audit_df). enriched_df.attrs[
    'underlying_missing_audit'] 含 spot fill 紀錄 (若 policy != raise).
    """
    if strategy_view_df.empty:
        raise ValueError("enrich_phase_1: input strategy_view_df is empty")
    df = add_underlying(strategy_view_df, spot_series, missing_policy=spot_missing_policy)
    df = add_dte(df)
    df, audit = add_q_pit(df, r=r, on_solve_fail=on_q_solve_fail)
    return df, audit


# ============================================================================
# Day 5 — per-strike IV + Δ + can_buy/can_sell (純 execution gate)
# ============================================================================


def _iv_price(row: pd.Series) -> tuple[float, str]:
    """Pick IV source price per row. Mid → settle fallback.

    Returns (price, source_flag). source_flag ∈ {'mid', 'settle', 'no_price'}.
    """
    bid = row.get("bid", float("nan"))
    ask = row.get("ask", float("nan"))
    settle = row.get("settle", float("nan"))
    if pd.notna(bid) and pd.notna(ask):
        mid = (float(bid) + float(ask)) / 2.0
        if mid > 0:
            return mid, "mid"
    if pd.notna(settle) and float(settle) > 0:
        return float(settle), "settle"
    return float("nan"), "no_price"


def _resolve_q_for_iv(row: pd.Series, q_source: str) -> float:
    """q lookup per row. q_source='fallback' → DIVIDEND_YIELD_DEFAULT;
    q_source='pit' → row['q_pit'] if finite else fallback.
    """
    if q_source == "fallback":
        return DIVIDEND_YIELD_DEFAULT
    if q_source == "pit":
        q = row.get("q_pit", float("nan"))
        if pd.notna(q) and math.isfinite(float(q)):
            return float(q)
        return DIVIDEND_YIELD_DEFAULT  # 退回 fallback (audit log 已在 add_q_pit)
    raise ValueError(f"_resolve_q_for_iv: q_source must be 'fallback'|'pit', got {q_source!r}")


def add_iv_per_strike(
    df: pd.DataFrame,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    q_source: Literal["fallback", "pit"] = "fallback",
    on_solver_fail: Literal["nan", "raise"] = "nan",
) -> pd.DataFrame:
    """Per-row implied vol via implied_vol(). Adds 'iv' (float64) + 'iv_source' (string).

    Price source priority: bid-ask mid > settle (Codex R10.11 hybrid spirit).
    No price (both NaN) → iv = NaN, iv_source = 'no_price'.
    Solver raise (no-arb violation / noise floor / Brent fail):
      - on_solver_fail='nan': iv = NaN, iv_source = 'solver_fail'
      - on_solver_fail='raise': re-raise (debug only)

    Args:
        df: must contain underlying / strike / dte / option_type / bid / ask /
            settle. q_source='pit' additionally needs 'q_pit'.
        r: annualised risk-free rate.
        q_source: 'fallback' (DIVIDEND_YIELD_DEFAULT) or 'pit' (per-date q_pit).
        on_solver_fail: 'nan' (default) or 'raise'.

    Returns: copy with 'iv' / 'iv_source' cols.

    Raises:
        ValueError: missing required cols / invalid q_source / invalid on_solver_fail.
    """
    if on_solver_fail not in ("nan", "raise"):
        raise ValueError(
            f"add_iv_per_strike: on_solver_fail must be 'nan'|'raise', got {on_solver_fail!r}"
        )
    required = {"underlying", "strike", "dte", "option_type", "bid", "ask", "settle"}
    if q_source == "pit":
        required.add("q_pit")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_iv_per_strike: missing required cols: {sorted(missing)}")

    n = len(df)
    iv_arr = np.full(n, float("nan"), dtype="float64")
    src_arr: np.ndarray = np.empty(n, dtype=object)

    # numpy column access — 比 itertuples()._asdict() 快且 mypy 友善 (不踩 pandas-stubs union)
    bid_col = df["bid"].to_numpy()
    ask_col = df["ask"].to_numpy()
    settle_col = df["settle"].to_numpy()
    underlying_col = df["underlying"].to_numpy()
    strike_col = df["strike"].to_numpy()
    dte_col = df["dte"].to_numpy()
    opt_col = df["option_type"].to_numpy()
    q_pit_col = df["q_pit"].to_numpy() if q_source == "pit" and "q_pit" in df.columns else None

    for i in range(n):
        # mid → settle fallback (2-tier spec; 不退到 close — close ≠ market mid)
        bid, ask = bid_col[i], ask_col[i]
        if pd.notna(bid) and pd.notna(ask):
            mid = (float(bid) + float(ask)) / 2.0
            if mid > 0:
                price, src = mid, "mid"
            else:
                price, src = float("nan"), "no_price"
        elif pd.notna(settle_col[i]) and float(settle_col[i]) > 0:
            price, src = float(settle_col[i]), "settle"
        else:
            price, src = float("nan"), "no_price"
        if not math.isfinite(price):
            src_arr[i] = "no_price"
            continue
        T = float(dte_col[i]) / 365.0
        if T <= 0:
            src_arr[i] = "no_price"
            continue
        S = float(underlying_col[i])
        K = float(strike_col[i])
        opt = str(opt_col[i])
        if q_pit_col is not None and pd.notna(q_pit_col[i]):
            q = float(q_pit_col[i])
        else:
            q = DIVIDEND_YIELD_DEFAULT
        try:
            iv = implied_vol(price=price, S=S, K=K, T=T, r=r, q=q, option_type=opt)
            iv_arr[i] = iv
            src_arr[i] = src
        except ValueError:
            if on_solver_fail == "raise":
                raise
            src_arr[i] = "solver_fail"

    out = df.copy()
    out["iv"] = iv_arr
    out["iv_source"] = pd.array(src_arr, dtype="string")
    return out


def add_delta_per_strike(
    df: pd.DataFrame,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    q_source: Literal["fallback", "pit"] = "fallback",
) -> pd.DataFrame:
    """Per-row Δ via greeks.delta(). Requires 'iv' col already populated.

    Skip rows with NaN iv → delta = NaN. q follows same q_source as iv.
    """
    required = {"underlying", "strike", "dte", "option_type", "iv"}
    if q_source == "pit":
        required.add("q_pit")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_delta_per_strike: missing required cols: {sorted(missing)}")

    n = len(df)
    delta_arr = np.full(n, float("nan"), dtype="float64")
    iv_col = df["iv"].to_numpy()
    underlying_col = df["underlying"].to_numpy()
    strike_col = df["strike"].to_numpy()
    dte_col = df["dte"].to_numpy()
    opt_col = df["option_type"].to_numpy()
    q_pit_col = df["q_pit"].to_numpy() if q_source == "pit" and "q_pit" in df.columns else None

    for i in range(n):
        iv = iv_col[i]
        if pd.isna(iv) or not math.isfinite(float(iv)) or float(iv) <= 0:
            continue
        T = float(dte_col[i]) / 365.0
        if T <= 0:
            continue
        S = float(underlying_col[i])
        K = float(strike_col[i])
        opt = str(opt_col[i])
        if q_pit_col is not None and pd.notna(q_pit_col[i]):
            q = float(q_pit_col[i])
        else:
            q = DIVIDEND_YIELD_DEFAULT
        with contextlib.suppress(ValueError):
            # bsm_delta raise on T==0 / sigma==0 — leave delta_arr[i] as NaN
            delta_arr[i] = bsm_delta(S=S, K=K, T=T, r=r, q=q, sigma=float(iv), option_type=opt)
    out = df.copy()
    out["delta"] = delta_arr
    return out


def _reconstruct_iv_from_record(record: SurfaceFitRecord, k: float) -> float:
    """Reconstruct single IV at log-moneyness k from a fitted surface record.

    Trust upstream cache contract gate (R11.11/12/13/14 5 layers) — record
    is already validated for: model_type ∈ valid set, params keys correct,
    converged → forward>0/T>0/finite, SVI/SABR domain守, Lee bound守.
    Don't re-validate (Pattern 14 producer/consumer parity — cache is producer,
    here is consumer; cache load gate enforces, consumer trusts).

    Returns NaN if:
      - record not converged, or
      - model_type not in {svi, sabr, poly} (insufficient_data / all_failed)
      - SVI total variance w(k) < 0 at this k (rare; numerical edge)

    Args:
        record: validated SurfaceFitRecord from load_surface_records.
        k: log-moneyness ln(K/F).

    Returns:
        IV (annualised vol decimal) or NaN.
    """
    if not record.converged or record.model_type not in {"svi", "sabr", "poly"}:
        return float("nan")
    p = record.params
    if record.model_type == "svi":
        # w(k) = a + b·{ρ·(k-m) + sqrt((k-m)² + σ²)}; IV = sqrt(w/T)
        w = svi_raw(np.array([k]), p["a"], p["b"], p["rho"], p["m"], p["sigma"])[0]
        if w < 0:
            return float("nan")
        return float(math.sqrt(w / record.T))
    if record.model_type == "sabr":
        # SABR β=1: K = F · exp(k); call sabr_lognormal_iv
        strike = record.forward * math.exp(k)
        iv_arr = sabr_lognormal_iv(
            np.array([strike]),
            record.forward,
            record.T,
            p["alpha"],
            p["rho"],
            p["nu"],
            beta=p["beta"],
        )
        return float(iv_arr[0])
    # poly: σ(k) = a + b·k + c·k²
    return float(p["a"] + p["b"] * k + p["c"] * k * k)


def add_model_price(
    df: pd.DataFrame,
    surface_records: list[SurfaceFitRecord],
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    q: float = DIVIDEND_YIELD_DEFAULT,
) -> pd.DataFrame:
    """Add 'model_price' col via cached vol surface IV → BSM-Merton invert.

    Per (date, expiry) group:
      1. Lookup surface_records dict by (date_str, expiry_str)
      2. Cache miss → model_price = NaN
      3. Cache hit but model_type ∈ {insufficient_data, all_failed} → NaN
      4. Else: compute k = ln(K/F), dispatch IV reconstruction by model_type,
         then BSM-Merton invert with fallback q

    **q_pit audit-only 紀律 (R10.x)**: 反算固定用 fallback q (q kwarg
    default = DIVIDEND_YIELD_DEFAULT 0.035), 不接受 q_pit — q_pit feeds
    audit only, BSM-invert from market IV must use stable q to avoid
    PCP-derived signal leak.

    **Forward (Phase 1 simplification)**: F ≈ underlying. Week 6+ before真
    backtest 切換 PCP forward (F = call_mid - put_mid + K·exp(-rT) +
    S·(1-exp(-qT))).

    Args:
        df: enriched chain (must have date / expiry / strike / option_type /
            underlying / dte cols).
        surface_records: list[SurfaceFitRecord] from load_surface_records.
            Empty list → all model_price = NaN (degenerate but allowed).
        r: annualised risk-free rate (decimal).
        q: annualised continuous dividend yield (decimal; **fallback only**,
            q_pit audit-only 不入).

    Returns:
        Copy of df with 'model_price' col (float64, nullable).

    Raises:
        ValueError: missing required cols.
    """
    required = {"date", "expiry", "strike", "option_type", "underlying", "dte"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_model_price: missing required cols: {sorted(missing)}")

    # Build dict for O(1) lookup (date_str, expiry_str) → record
    record_lookup: dict[tuple[str, str], SurfaceFitRecord] = {
        (rec.date, rec.expiry): rec for rec in surface_records
    }

    n = len(df)
    model_price_arr = np.full(n, float("nan"), dtype="float64")

    # numpy column access (mypy-friendly, faster than itertuples)
    date_col = df["date"].to_numpy()
    expiry_col = df["expiry"].to_numpy()
    strike_col = df["strike"].to_numpy()
    underlying_col = df["underlying"].to_numpy()
    dte_col = df["dte"].to_numpy()
    opt_col = df["option_type"].to_numpy()

    for i in range(n):
        # Format dates as ISO strings to match SurfaceFitRecord.date / .expiry format
        date_str = pd.Timestamp(date_col[i]).strftime("%Y-%m-%d")
        expiry_str = pd.Timestamp(expiry_col[i]).strftime("%Y-%m-%d")
        record = record_lookup.get((date_str, expiry_str))
        if record is None:
            continue  # cache miss → NaN
        # Forward = underlying (Phase 1 simplification)
        S = underlying_col[i]
        if pd.isna(S):
            continue
        S = float(S)
        if S <= 0:
            continue
        K = float(strike_col[i])
        if K <= 0:
            continue
        T = float(dte_col[i]) / CALENDAR_DAYS_PER_YEAR
        if T <= 0:
            continue
        # Reconstruct IV
        k = math.log(K / S)
        iv = _reconstruct_iv_from_record(record, k)
        if not math.isfinite(iv) or iv <= 0:
            continue
        # BSM-Merton invert (fallback q strict — q_pit audit-only 紀律)
        opt = str(opt_col[i])
        try:
            model_price_arr[i] = bsm_price(S=S, K=K, T=T, r=r, q=q, sigma=iv, option_type=opt)
        except ValueError:
            # bsm_price raises on invalid inputs (e.g. option_type not in {call,put})
            # — leave model_price = NaN (silent skip is acceptable here; downstream
            # mark policy decides what to do with NaN)
            continue

    out = df.copy()
    out["model_price"] = model_price_arr
    return out


def add_can_buy_can_sell(df: pd.DataFrame) -> pd.DataFrame:
    """Pure execution gate (R10.10 3ii):
      can_buy  = ask.notna()    (要買 → 需要 ask 報價)
      can_sell = bid.notna()    (要賣 → 需要 bid 報價)

    Bid/ask = 0 是 valid quote 不過濾 (TAIFEX deep OTM 收盤 quote 可能 0 但仍可成交).
    """
    if "bid" not in df.columns or "ask" not in df.columns:
        raise ValueError("add_can_buy_can_sell: requires 'bid' and 'ask' columns")
    out = df.copy()
    out["can_buy"] = out["ask"].notna()
    out["can_sell"] = out["bid"].notna()
    return out


def enrich_pipeline(
    strategy_view_df: pd.DataFrame,
    spot_series: pd.Series,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    q_source: Literal["fallback", "pit"] = "fallback",
    on_q_solve_fail: Literal["nan", "fallback"] = "nan",
    on_iv_solver_fail: Literal["nan", "raise"] = "nan",
    spot_missing_policy: Literal["raise", "forward_fill", "skip"] = "raise",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full Day 4 + Day 5 pipeline:

      enrich_phase_1 (underlying / dte / q_pit*)
        ↓
      add_iv_per_strike (iv / iv_source)
        ↓
      add_delta_per_strike (delta)
        ↓
      add_can_buy_can_sell (can_buy / can_sell)

    Returns (enriched_df, q_pit_audit_df). enriched_df has 10 SV + 5 phase-1 +
    iv/iv_source/delta/can_buy/can_sell = 19 cols. ENGINE_REQUIRED 13-col
    subset is guaranteed present.
    """
    df, audit = enrich_phase_1(
        strategy_view_df,
        spot_series,
        r=r,
        on_q_solve_fail=on_q_solve_fail,
        spot_missing_policy=spot_missing_policy,
    )
    df = add_iv_per_strike(df, r=r, q_source=q_source, on_solver_fail=on_iv_solver_fail)
    df = add_delta_per_strike(df, r=r, q_source=q_source)
    df = add_can_buy_can_sell(df)
    return df, audit
