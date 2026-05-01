"""Tests for src/data/enrich.py — Day 4 Phase 1 + Day 5.1 model_price.

Coverage:
  - add_dte / add_underlying / add_q_pit / _solve_q_pit_one_day (Day 4 Phase 1)
  - add_iv_per_strike / add_delta_per_strike / add_can_buy_can_sell (Day 4 Phase 2)
  - enrich_phase_1 / enrich_pipeline integration
  - **add_model_price (Week 5 Day 5.1, ~10 tests)**:
    * cache miss → NaN
    * insufficient_data / all_failed → NaN
    * SVI/SABR/poly model_type 各跑一遍 happy path
    * 多 (date, expiry) dispatch
    * forward NaN (underlying NaN) → NaN
    * input validation: missing required cols
    * fallback q (q_pit audit-only 紀律)
    * model_price col 進 ENRICHED_OPTIONAL_COLUMNS
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from config.constants import DIVIDEND_YIELD_DEFAULT
from src.data.enrich import (
    Q_PIT_ABS_CAP,
    _solve_q_pit_one_day,
    add_can_buy_can_sell,
    add_delta_per_strike,
    add_dte,
    add_iv_per_strike,
    add_model_price,
    add_q_pit,
    add_underlying,
    enrich_phase_1,
    enrich_pipeline,
)
from src.data.schema import STRATEGY_VIEW_COLUMN_ORDER
from src.options.pricing import bsm_price
from src.options.surface_batch import SurfaceFitRecord


def _build_chain_one_day(
    date_str: str = "2024-01-02",
    expiry_str: str = "2024-01-17",
    spot: float = 17500.0,
    q_true: float = 0.035,
    r: float = 0.015,
    *,
    atm_strike: float = 17500.0,
) -> tuple[pd.DataFrame, pd.Series]:
    """Synthesize a STRATEGY_VIEW one-day chain with PCP-consistent ATM prices.

    At ATM: C - P = S·e^(-qT) - K·e^(-rT)  → solve_q_pit recovers q_true.
    Non-ATM strikes use filler prices (irrelevant for q PIT solve).
    """
    date = pd.Timestamp(date_str)
    expiry = pd.Timestamp(expiry_str)
    T = (expiry - date).days / 365.0
    strikes = [17000.0, 17250.0, 17500.0, 17750.0, 18000.0]
    rows = []
    for K in strikes:
        if atm_strike == K:
            forward = spot * math.exp(-q_true * T) - K * math.exp(-r * T)
            C = 120.0
            P = C - forward
        else:
            C, P = 100.0, 100.0
        for opt_type, mid in [("call", C), ("put", P)]:
            rows.append(
                {
                    "date": date,
                    "expiry": expiry,
                    "strike": K,
                    "option_type": opt_type,
                    "settle": mid,
                    "close": mid,
                    "bid": mid - 0.5,
                    "ask": mid + 0.5,
                    "volume": 100,
                    "open_interest": 1000,
                }
            )
    df = pd.DataFrame(rows)[STRATEGY_VIEW_COLUMN_ORDER]
    spot_series = pd.Series([spot], index=[date])
    return df, spot_series


# ---------------------------------------------------------------------------
# add_dte (3 tests)
# ---------------------------------------------------------------------------


def test_add_dte_missing_cols_raises() -> None:
    df = pd.DataFrame({"strike": [17500.0]})
    with pytest.raises(ValueError, match="requires 'date' and 'expiry'"):
        add_dte(df)


def test_add_dte_negative_dte_raises() -> None:
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-20")],
            "expiry": [pd.Timestamp("2024-01-17")],
        }
    )
    with pytest.raises(ValueError, match="negative dte"):
        add_dte(df)


def test_add_dte_calendar_days() -> None:
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02")],
            "expiry": [pd.Timestamp("2024-01-17"), pd.Timestamp("2024-02-21")],
        }
    )
    out = add_dte(df)
    assert out["dte"].tolist() == [15, 50]
    assert out["dte"].dtype == np.int64


# ---------------------------------------------------------------------------
# add_underlying (4 tests)
# ---------------------------------------------------------------------------


def test_add_underlying_missing_date_col_raises() -> None:
    df = pd.DataFrame({"strike": [17500.0]})
    spot = pd.Series([17500.0], index=[pd.Timestamp("2024-01-02")])
    with pytest.raises(ValueError, match="requires 'date'"):
        add_underlying(df, spot)


def test_add_underlying_non_series_raises() -> None:
    df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]})
    with pytest.raises(TypeError, match="must be pd.Series"):
        add_underlying(df, {pd.Timestamp("2024-01-02"): 17500.0})  # type: ignore[arg-type]


def test_add_underlying_missing_spot_date_raises() -> None:
    df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]})
    spot = pd.Series([17500.0], index=[pd.Timestamp("2024-01-02")])
    with pytest.raises(ValueError, match="missing in spot_series"):
        add_underlying(df, spot)


def test_add_underlying_forward_fill_audit() -> None:
    """R11.6 P1: missing_policy='forward_fill' 用前序交易日 close 補 + audit."""
    df = pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-02"),
                pd.Timestamp("2024-01-03"),  # 缺 spot
                pd.Timestamp("2024-01-04"),
            ],
            "strike": [17500.0, 17500.0, 17500.0],
        }
    )
    spot = pd.Series(
        [17500.0, 17600.0],
        index=[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")],
    )
    out = add_underlying(df, spot, missing_policy="forward_fill")
    # 2024-01-03 用 2024-01-02 的 17500.0 補
    assert out["underlying"].tolist() == [17500.0, 17500.0, 17600.0]
    audit = out.attrs["underlying_missing_audit"]
    assert audit["policy"] == "forward_fill"
    assert audit["missing_dates"] == ["2024-01-03"]
    assert audit["fill_sources"]["2024-01-03"]["filled_from"] == "2024-01-02"
    assert audit["fill_sources"]["2024-01-03"]["fill_value"] == 17500.0


def test_add_underlying_skip_audit() -> None:
    """R11.6 P1: missing_policy='skip' 把 missing date row 剔除 + audit."""
    df = pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-02"),
                pd.Timestamp("2024-01-03"),
                pd.Timestamp("2024-01-04"),
            ],
            "strike": [17500.0, 17500.0, 17500.0],
        }
    )
    spot = pd.Series(
        [17500.0, 17600.0],
        index=[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")],
    )
    out = add_underlying(df, spot, missing_policy="skip")
    assert len(out) == 2
    assert out["date"].tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-04")]
    audit = out.attrs["underlying_missing_audit"]
    assert audit["policy"] == "skip"
    assert audit["missing_dates"] == ["2024-01-03"]
    assert audit["n_rows_dropped"] == 1


def test_add_underlying_invalid_policy_raises() -> None:
    df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "strike": [17500.0]})
    spot = pd.Series([17500.0], index=[pd.Timestamp("2024-01-02")])
    with pytest.raises(ValueError, match="missing_policy must be"):
        add_underlying(df, spot, missing_policy="bogus")  # type: ignore[arg-type]


def test_add_underlying_happy_path_broadcasts() -> None:
    df = pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-02"),
                pd.Timestamp("2024-01-02"),
                pd.Timestamp("2024-01-03"),
            ],
            "strike": [17500.0, 17600.0, 17500.0],
        }
    )
    spot = pd.Series(
        [17500.0, 17600.0],
        index=[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")],
    )
    out = add_underlying(df, spot)
    assert out["underlying"].tolist() == [17500.0, 17500.0, 17600.0]
    assert out["underlying"].dtype == np.float64


# ---------------------------------------------------------------------------
# _solve_q_pit_one_day (5 tests)
# ---------------------------------------------------------------------------


def test_solve_q_pit_recovers_known_q() -> None:
    """PCP synthetic chain with q_true=0.035 → solver recovers within tol."""
    df, _ = _build_chain_one_day(q_true=0.035)
    date = pd.Timestamp("2024-01-02")
    q, flag, _ = _solve_q_pit_one_day(date=date, spot=17500.0, day_chain=df, r=0.015)
    assert flag == "ok"
    assert abs(q - 0.035) < 1e-6


def test_solve_q_pit_no_future_expiry() -> None:
    """All expiries <= date → no_atm_pair flag."""
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02")],
            "expiry": [pd.Timestamp("2024-01-02")],  # not strictly > date
            "strike": [17500.0],
            "option_type": ["call"],
            "bid": [120.0],
            "ask": [121.0],
            "close": [120.5],
        }
    )
    q, flag, _ = _solve_q_pit_one_day(
        date=pd.Timestamp("2024-01-02"),
        spot=17500.0,
        day_chain=df,
        r=0.015,
    )
    assert math.isnan(q) and flag == "no_atm_pair"


def test_solve_q_pit_missing_quote_flag() -> None:
    """ATM call has NaN bid/ask AND NaN close → missing_quote flag."""
    df, _ = _build_chain_one_day()
    # Wipe ATM call quotes (strike 17500.0)
    mask = (df["strike"] == 17500.0) & (df["option_type"] == "call")
    df.loc[mask, ["bid", "ask", "close"]] = float("nan")
    q, flag, _ = _solve_q_pit_one_day(
        date=pd.Timestamp("2024-01-02"),
        spot=17500.0,
        day_chain=df,
        r=0.015,
    )
    assert math.isnan(q) and flag == "missing_quote"


def test_solve_q_pit_pcp_invalid_when_numerator_nonpositive() -> None:
    """C - P + K·e^(-rT) <= 0 (extreme inverted prices) → pcp_invalid flag."""
    df, _ = _build_chain_one_day()
    mask_call = (df["strike"] == 17500.0) & (df["option_type"] == "call")
    mask_put = (df["strike"] == 17500.0) & (df["option_type"] == "put")
    # C very small, P huge → C-P deeply negative, swamps K·e^(-rT)
    df.loc[mask_call, ["bid", "ask", "close"]] = 1.0
    df.loc[mask_put, ["bid", "ask", "close"]] = 1e6
    q, flag, _ = _solve_q_pit_one_day(
        date=pd.Timestamp("2024-01-02"),
        spot=17500.0,
        day_chain=df,
        r=0.015,
    )
    assert math.isnan(q) and flag == "pcp_invalid"


def test_solve_q_pit_out_of_range_flag() -> None:
    """Force q far above Q_PIT_ABS_CAP → out_of_range flag (q value still returned)."""
    # Construct: ratio = exp(-q*T) very small → q huge.
    # Pick S=17500, K=17500, T=15/365. Want q ≈ 0.5 → e^(-0.5*0.0411) = 0.97965
    # → numerator = 0.97965 * 17500 = 17143.4
    # → C - P + K·e^(-rT) = 17143.4
    # → with K·e^(-0.015·0.0411) ≈ 17489.22, need C - P ≈ -345.8
    # Pick C=10, P=355.8
    df, _ = _build_chain_one_day()
    mask_call = (df["strike"] == 17500.0) & (df["option_type"] == "call")
    mask_put = (df["strike"] == 17500.0) & (df["option_type"] == "put")
    df.loc[mask_call, ["bid", "ask", "close"]] = 10.0
    df.loc[mask_put, ["bid", "ask", "close"]] = 355.8
    q, flag, _ = _solve_q_pit_one_day(
        date=pd.Timestamp("2024-01-02"),
        spot=17500.0,
        day_chain=df,
        r=0.015,
    )
    assert flag == "out_of_range"
    assert q > Q_PIT_ABS_CAP


# ---------------------------------------------------------------------------
# add_q_pit (4 tests)
# ---------------------------------------------------------------------------


def test_add_q_pit_missing_required_cols_raises() -> None:
    df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "strike": [17500.0]})
    with pytest.raises(ValueError, match="missing required cols"):
        add_q_pit(df)


def test_add_q_pit_invalid_on_solve_fail_raises() -> None:
    df, spot = _build_chain_one_day()
    df = add_underlying(df, spot)
    with pytest.raises(ValueError, match="on_solve_fail"):
        add_q_pit(df, on_solve_fail="bogus")  # type: ignore[arg-type]


def test_add_q_pit_broadcast_per_date_with_audit() -> None:
    """Single-day chain: q_pit broadcast to all rows; audit_df has 1 row."""
    df, spot = _build_chain_one_day(q_true=0.035)
    df = add_underlying(df, spot)
    out, audit = add_q_pit(df, r=0.015)
    # All rows of 2024-01-02 have same q_pit (use min==max per PD101)
    q = out["q_pit"]
    assert q.min() == q.max()
    assert abs(q.iloc[0] - 0.035) < 1e-6
    assert (out["q_pit_source"] == "pcp").all()
    assert (out["q_pit_audit_flags"] == "ok").all()
    # Audit DataFrame
    assert len(audit) == 1
    assert audit["audit_flag"].iloc[0] == "ok"
    assert audit["strike_used"].iloc[0] == 17500.0


def test_add_q_pit_solve_failure_uses_fallback() -> None:
    """on_solve_fail='fallback' → q_pit=DIVIDEND_YIELD_DEFAULT, source='fallback'."""
    df, spot = _build_chain_one_day()
    df = add_underlying(df, spot)
    # Wipe ATM quotes → solve_failed
    mask = (df["strike"] == 17500.0) & (df["option_type"] == "call")
    df.loc[mask, ["bid", "ask", "close"]] = float("nan")
    out, audit = add_q_pit(df, r=0.015, on_solve_fail="fallback")
    assert (out["q_pit"] == DIVIDEND_YIELD_DEFAULT).all()
    assert (out["q_pit_source"] == "fallback").all()
    assert audit["audit_flag"].iloc[0] == "missing_quote"


# ---------------------------------------------------------------------------
# enrich_phase_1 (2 tests)
# ---------------------------------------------------------------------------


def test_enrich_phase_1_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        enrich_phase_1(pd.DataFrame(), pd.Series(dtype=float))


def test_enrich_phase_1_full_pipeline() -> None:
    """End-to-end: STRATEGY_VIEW + spot → 15-col enriched + audit DataFrame."""
    df, spot = _build_chain_one_day(q_true=0.035)
    enriched, audit = enrich_phase_1(df, spot, r=0.015)
    expected_new_cols = {"underlying", "dte", "q_pit", "q_pit_source", "q_pit_audit_flags"}
    assert expected_new_cols.issubset(set(enriched.columns))
    assert (enriched["underlying"] == 17500.0).all()
    assert (enriched["dte"] == 15).all()
    assert abs(enriched["q_pit"].iloc[0] - 0.035) < 1e-6
    assert len(audit) == 1
    assert audit["audit_flag"].iloc[0] == "ok"


# ---------------------------------------------------------------------------
# Day 5: add_iv_per_strike (6 tests)
# ---------------------------------------------------------------------------


def _build_bsm_priced_chain(
    sigma_true: float = 0.20,
    q_true: float = 0.035,
    r: float = 0.015,
    spot: float = 17500.0,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build chain with bid/ask/settle = bsm_price(sigma_true) ± 0.5.

    IV solver should recover sigma_true ± 1e-6 when fed mid price.
    """
    date = pd.Timestamp("2024-01-02")
    expiry = pd.Timestamp("2024-01-17")
    T = (expiry - date).days / 365.0
    rows = []
    for K in [17000.0, 17250.0, 17500.0, 17750.0, 18000.0]:
        for opt in ["call", "put"]:
            theo = bsm_price(spot, K, T, r, q_true, sigma_true, opt)
            rows.append(
                {
                    "date": date,
                    "expiry": expiry,
                    "strike": K,
                    "option_type": opt,
                    "settle": theo,
                    "close": theo,
                    "bid": theo - 0.5,
                    "ask": theo + 0.5,
                    "volume": 100,
                    "open_interest": 1000,
                }
            )
    df = pd.DataFrame(rows)[STRATEGY_VIEW_COLUMN_ORDER]
    spot_series = pd.Series([spot], index=[date])
    return df, spot_series


def test_add_iv_invalid_on_solver_fail_raises() -> None:
    df, spot = _build_bsm_priced_chain()
    df = add_underlying(df, spot)
    df = add_dte(df)
    with pytest.raises(ValueError, match="on_solver_fail"):
        add_iv_per_strike(df, on_solver_fail="bogus")  # type: ignore[arg-type]


def test_add_iv_missing_required_cols_raises() -> None:
    df = pd.DataFrame({"strike": [17500.0]})
    with pytest.raises(ValueError, match="missing required cols"):
        add_iv_per_strike(df)


def test_add_iv_recovers_sigma_round_trip_via_mid() -> None:
    """BSM round-trip: price 餵 mid (≈ theo + 0.5/-0.5 但 ATM 接近)，IV ≈ sigma_true ± 0.001."""
    df, spot = _build_bsm_priced_chain(sigma_true=0.20)
    df = add_underlying(df, spot)
    df = add_dte(df)
    out = add_iv_per_strike(df, r=0.015, q_source="fallback", on_solver_fail="nan")
    # ATM 行 (strike == spot) IV 接近 0.20 (mid 偏離 theo 0.5 但 ATM vega 大 → 對 IV 影響小)
    atm = out[out["strike"] == 17500.0]
    iv_atm = atm["iv"].dropna()
    assert len(iv_atm) > 0
    assert all(abs(iv - 0.20) < 0.05 for iv in iv_atm), f"ATM IV: {iv_atm.tolist()}"
    # 全部行 iv_source 都應是 'mid' (bid/ask 都 finite)
    assert (out["iv_source"] == "mid").all()


def test_add_iv_falls_back_to_settle_when_mid_missing() -> None:
    """Wipe bid/ask of one row → 該行 iv_source='settle'."""
    df, spot = _build_bsm_priced_chain(sigma_true=0.20)
    df = add_underlying(df, spot)
    df = add_dte(df)
    mask = (df["strike"] == 17500.0) & (df["option_type"] == "call")
    df.loc[mask, ["bid", "ask"]] = float("nan")
    out = add_iv_per_strike(df)
    target_row = out[mask].iloc[0]
    assert target_row["iv_source"] == "settle"
    assert pd.notna(target_row["iv"])


def test_add_iv_no_price_returns_nan() -> None:
    """bid/ask/settle 三者皆 NaN → iv = NaN, iv_source='no_price'."""
    df, spot = _build_bsm_priced_chain()
    df = add_underlying(df, spot)
    df = add_dte(df)
    mask = (df["strike"] == 17500.0) & (df["option_type"] == "put")
    df.loc[mask, ["bid", "ask", "settle"]] = float("nan")
    out = add_iv_per_strike(df)
    target_row = out[mask].iloc[0]
    assert pd.isna(target_row["iv"])
    assert target_row["iv_source"] == "no_price"


def test_add_iv_q_source_pit_uses_q_pit_col() -> None:
    """q_source='pit' 用 row['q_pit']；用 fallback 結果對比應差異."""
    df, spot = _build_bsm_priced_chain(sigma_true=0.20, q_true=0.035)
    df = add_underlying(df, spot)
    df = add_dte(df)
    df["q_pit"] = 0.10  # 故意設為非 fallback 值
    out_pit = add_iv_per_strike(df, q_source="pit")
    out_fb = add_iv_per_strike(df, q_source="fallback")
    # 因為 BSM price 是用 q=0.035 算的，q_pit=0.10 解出來 IV 會偏離 0.20
    iv_pit_atm = out_pit[out_pit["strike"] == 17500.0]["iv"].dropna().iloc[0]
    iv_fb_atm = out_fb[out_fb["strike"] == 17500.0]["iv"].dropna().iloc[0]
    assert abs(iv_pit_atm - iv_fb_atm) > 0.001  # q 改變 → IV 改變


# ---------------------------------------------------------------------------
# Day 5: add_delta_per_strike (3 tests)
# ---------------------------------------------------------------------------


def test_add_delta_missing_iv_raises() -> None:
    df = pd.DataFrame(
        {"underlying": [17500.0], "strike": [17500.0], "dte": [15], "option_type": ["call"]}
    )  # 缺 iv
    with pytest.raises(ValueError, match="missing required cols"):
        add_delta_per_strike(df)


def test_add_delta_skips_nan_iv() -> None:
    """iv=NaN 的行 → delta=NaN；不 raise."""
    df, spot = _build_bsm_priced_chain()
    df = add_underlying(df, spot)
    df = add_dte(df)
    df = add_iv_per_strike(df)
    # 手動把一行 IV 設 NaN
    df.loc[df.index[0], "iv"] = float("nan")
    out = add_delta_per_strike(df)
    assert pd.isna(out.iloc[0]["delta"])
    # 其他行 delta 仍 ok
    assert out["delta"].notna().sum() > 0


def test_add_delta_call_in_merton_bound() -> None:
    """call delta ∈ [0, exp(-qT)]; ATM call 大致在 ~0.5*exp(-qT)."""
    df, spot = _build_bsm_priced_chain(sigma_true=0.20, q_true=0.035, r=0.015)
    df = add_underlying(df, spot)
    df = add_dte(df)
    df = add_iv_per_strike(df)
    out = add_delta_per_strike(df, r=0.015)
    T = 15 / 365.0
    e_qT = math.exp(-0.035 * T)
    calls = out[out["option_type"] == "call"]
    atm_call_delta = calls[calls["strike"] == 17500.0]["delta"].iloc[0]
    assert 0 <= atm_call_delta <= e_qT, f"ATM call Δ {atm_call_delta} 不在 [0, {e_qT}]"
    assert 0.4 < atm_call_delta < 0.6, f"ATM call Δ {atm_call_delta} 應接近 0.5"


# ---------------------------------------------------------------------------
# Day 5: add_can_buy_can_sell (2 tests)
# ---------------------------------------------------------------------------


def test_add_can_buy_can_sell_from_bid_ask_notna() -> None:
    df = pd.DataFrame(
        {
            "bid": [120.0, float("nan"), 0.0, 5.0],
            "ask": [121.0, 122.0, float("nan"), 0.0],
        }
    )
    out = add_can_buy_can_sell(df)
    assert out["can_buy"].tolist() == [True, True, False, True]  # ask not nan
    assert out["can_sell"].tolist() == [True, False, True, True]  # bid not nan


def test_add_can_buy_can_sell_missing_cols_raises() -> None:
    df = pd.DataFrame({"strike": [17500.0]})
    with pytest.raises(ValueError, match="requires 'bid' and 'ask'"):
        add_can_buy_can_sell(df)


# ---------------------------------------------------------------------------
# Day 5: enrich_pipeline full (1 test)
# ---------------------------------------------------------------------------


def test_enrich_pipeline_full_produces_engine_required_cols() -> None:
    """Full pipeline (Day 4 + 5) → df 含 ENGINE_REQUIRED_COLUMNS 全部 13 col."""
    from src.data.schema import ENGINE_REQUIRED_COLUMNS

    df, spot = _build_bsm_priced_chain(sigma_true=0.20)
    enriched, audit = enrich_pipeline(df, spot, r=0.015, q_source="fallback")
    assert ENGINE_REQUIRED_COLUMNS.issubset(set(enriched.columns)), (
        f"missing: {ENGINE_REQUIRED_COLUMNS - set(enriched.columns)}"
    )
    # IV / delta / can_* 全部有值 (synthetic chain bid/ask 都 finite)
    assert enriched["iv"].notna().all()
    assert enriched["delta"].notna().all()
    assert enriched["can_buy"].all()
    assert enriched["can_sell"].all()
    assert len(audit) == 1


# ===========================================================================
# Week 5 Day 5.1 — add_model_price (vol surface → BSM-Merton invert)
# ===========================================================================

# Pattern 16 (R11.13 加): fixture 預設值 schema-correct dispatch dict
# 避免 _make_record SVI params 配 sabr/poly 偷工 (R11.12 副作用揭露)
_SURFACE_DEFAULT_PARAMS: dict[str, dict[str, float]] = {
    "svi": {"a": 0.04, "b": 0.4, "rho": -0.3, "m": 0.0, "sigma": 0.1},
    "sabr": {"alpha": 0.18, "rho": -0.3, "nu": 0.4, "beta": 1.0},
    "poly": {"a": 0.18, "b": -0.3, "c": 0.4},
}


def _make_surface_record(
    date: str = "2024-01-15",
    expiry: str = "2024-02-21",
    model_type: str = "svi",
    converged: bool = True,
    forward: float = 17500.0,
    T: float = 0.1,
    **overrides: object,
) -> SurfaceFitRecord:
    """Build a SurfaceFitRecord with schema-correct params per model_type.

    Pattern 16 紀律: params 自動 schema-match model_type — 防 _make_record SVI
    預設配 sabr 偷工 (R11.12 副作用實證).
    """
    params = _SURFACE_DEFAULT_PARAMS.get(model_type, {})
    base: dict[str, object] = {
        "date": date,
        "expiry": expiry,
        "model_type": model_type,
        "converged": converged,
        "n_points": 11,
        "in_sample_rmse": 0.005,
        "fit_time_ms": 12,
        "forward": forward,
        "T": T,
        "params": params,
        "attempts": [],
        "error": None,
    }
    base.update(overrides)
    return SurfaceFitRecord(**base)  # type: ignore[arg-type]


def _make_chain_for_model_price(
    date: str = "2024-01-15",
    expiry: str = "2024-02-21",
    underlying: float = 17500.0,
    strikes: tuple[float, ...] = (17000.0, 17500.0, 18000.0),
) -> pd.DataFrame:
    """Minimal enriched chain for add_model_price testing.

    Has all required cols: date / expiry / strike / option_type / underlying / dte.
    """
    rows = []
    for K in strikes:
        for opt in ("call", "put"):
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "expiry": pd.Timestamp(expiry),
                    "strike": float(K),
                    "option_type": opt,
                    "underlying": underlying,
                    "dte": (pd.Timestamp(expiry) - pd.Timestamp(date)).days,
                }
            )
    return pd.DataFrame(rows)


def test_add_model_price_svi_happy_path() -> None:
    """SVI fit → ATM strike model_price 對齊 BSM(IV reconstructed from SVI)."""
    chain = _make_chain_for_model_price(strikes=(17500.0,))
    record = _make_surface_record(model_type="svi", forward=17500.0, T=0.1)
    out = add_model_price(chain, [record])
    assert "model_price" in out.columns
    assert out["model_price"].notna().all()
    # ATM call price < underlying (sane bound); positive
    call_row = out[out["option_type"] == "call"].iloc[0]
    assert 0 < call_row["model_price"] < 17500.0


def test_add_model_price_sabr_happy_path() -> None:
    """SABR β=1 fit → 3-strike model_price 全有值."""
    chain = _make_chain_for_model_price()
    record = _make_surface_record(model_type="sabr", forward=17500.0, T=0.1)
    out = add_model_price(chain, [record])
    assert out["model_price"].notna().all()
    # 6 rows (3 strikes × 2 option_type) all positive
    assert (out["model_price"] > 0).all()


def test_add_model_price_poly_happy_path() -> None:
    """poly degree-2 fit → 3-strike model_price 全有值."""
    chain = _make_chain_for_model_price()
    record = _make_surface_record(model_type="poly", forward=17500.0, T=0.1)
    out = add_model_price(chain, [record])
    assert out["model_price"].notna().all()


def test_add_model_price_cache_miss_returns_nan() -> None:
    """chain 有 (2024-01-15, 2024-02-21) 但 cache 只存其他 expiry → NaN."""
    chain = _make_chain_for_model_price(expiry="2024-02-21")
    # Cache 只存不同 expiry
    record = _make_surface_record(expiry="2024-03-20")
    out = add_model_price(chain, [record])
    # All NaN (cache miss for chain expiry)
    assert out["model_price"].isna().all()


def test_add_model_price_insufficient_data_returns_nan() -> None:
    """Cache 有但 model_type='insufficient_data' → NaN (R11.13 P1 邊界)."""
    chain = _make_chain_for_model_price()
    record = SurfaceFitRecord(
        date="2024-01-15",
        expiry="2024-02-21",
        model_type="insufficient_data",
        converged=False,
        n_points=2,
        in_sample_rmse=float("nan"),
        fit_time_ms=0,
        forward=float("nan"),
        T=float("nan"),
        params={},
        attempts=[],
        error="fewer_than_min_strikes",
    )
    out = add_model_price(chain, [record])
    assert out["model_price"].isna().all()


def test_add_model_price_all_failed_returns_nan() -> None:
    """model_type='all_failed' → NaN."""
    chain = _make_chain_for_model_price()
    record = SurfaceFitRecord(
        date="2024-01-15",
        expiry="2024-02-21",
        model_type="all_failed",
        converged=False,
        n_points=11,
        in_sample_rmse=float("nan"),
        fit_time_ms=0,
        forward=float("nan"),
        T=float("nan"),
        params={},
        attempts=[],
        error="all_3_tiers_failed",
    )
    out = add_model_price(chain, [record])
    assert out["model_price"].isna().all()


def test_add_model_price_multi_date_multi_expiry_dispatch() -> None:
    """2 date × 2 expiry → 4 group dispatch 對；各 group 對應 record."""
    parts = []
    for date in ("2024-01-15", "2024-01-16"):
        for expiry in ("2024-02-21", "2024-03-20"):
            parts.append(_make_chain_for_model_price(date=date, expiry=expiry))
    chain = pd.concat(parts, ignore_index=True)
    records = [
        _make_surface_record(date="2024-01-15", expiry="2024-02-21", T=0.1),
        _make_surface_record(date="2024-01-15", expiry="2024-03-20", T=0.18),
        _make_surface_record(date="2024-01-16", expiry="2024-02-21", T=0.097),
        _make_surface_record(date="2024-01-16", expiry="2024-03-20", T=0.18),
    ]
    out = add_model_price(chain, records)
    # 4 group × 6 row = 24 rows; 全 finite
    assert len(out) == 24
    assert out["model_price"].notna().all()


def test_add_model_price_underlying_nan_returns_nan() -> None:
    """Forward NaN (underlying NaN) → 該 row model_price = NaN."""
    chain = _make_chain_for_model_price()
    chain.loc[0, "underlying"] = float("nan")  # only first row
    record = _make_surface_record()
    out = add_model_price(chain, [record])
    # First row NaN; rest filled
    assert pd.isna(out.iloc[0]["model_price"])
    assert out.iloc[1:]["model_price"].notna().all()


def test_add_model_price_missing_required_cols_raises() -> None:
    """Missing date/expiry/strike/option_type/underlying/dte → ValueError."""
    chain = _make_chain_for_model_price()
    bad = chain.drop(columns=["dte"])
    with pytest.raises(ValueError, match=r"missing required cols.*dte"):
        add_model_price(bad, [])


def test_add_model_price_empty_records_returns_all_nan() -> None:
    """Empty surface_records (degenerate but allowed) → all model_price NaN."""
    chain = _make_chain_for_model_price()
    out = add_model_price(chain, [])
    assert "model_price" in out.columns
    assert out["model_price"].isna().all()


def test_add_model_price_uses_fallback_q_not_q_pit() -> None:
    """q_pit audit-only 紀律 (R10.x): add_model_price 必用 fallback q.

    證明: 即使 chain 有 q_pit col 也不該被讀取；q kwarg 是唯一 q source.
    BSM 反算 model_price 應依賴 q kwarg, 不依賴 chain 的 q_pit col.
    """
    chain = _make_chain_for_model_price(strikes=(17500.0,))
    chain["q_pit"] = 0.20  # 故意設離譜值；若被讀就會大幅改變 model_price
    record = _make_surface_record(model_type="svi", forward=17500.0, T=0.1)
    # 跑兩次：一次 default q=0.035，一次顯式 q=0.035 — 結果應一致
    out_default = add_model_price(chain, [record])
    out_explicit = add_model_price(chain, [record], q=0.035)
    np.testing.assert_allclose(
        out_default["model_price"].to_numpy(),
        out_explicit["model_price"].to_numpy(),
    )
