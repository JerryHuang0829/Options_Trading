"""Tests for src/data/schema.py — v6 D-soft schema contract.

R10.10 P2-#5 修法：用 frozenset[str] + dtype + nullability 取代「欄位數」
acceptance criterion. 這些 sanity tests 守護 schema 不被 silent 改動 (Pattern 3
grep sweep 紀律的 enforcement)。
"""

from __future__ import annotations

from src.data.schema import (
    COLUMN_DTYPES,
    COLUMN_NULLABILITY,
    ENGINE_REQUIRED_COLUMNS,
    RAW_COLUMN_RENAME,
    RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208,
    RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208,
    RAW_TAIFEX_COLUMNS_POST_20251208,
    RAW_TAIFEX_COLUMNS_PRE_20251208,
    STRATEGY_VIEW_COLUMNS,
    VALUE_NORMALIZATION,
)


def test_raw_chinese_pre_20251208_has_20_cols() -> None:
    """Pre 2025-12-08 raw schema = 20 cols (Pre-1 spec 實證)."""
    assert len(RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208) == 20


def test_raw_chinese_post_20251208_adds_only_contract_date() -> None:
    """Post 2025-12-08 raw schema = pre + 1 col (`契約到期日` only).

    TAIFEX 公告：自2025年12月8日一般交易時段起資料增加「契約到期日」欄位。
    """
    diff = RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208 - RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208
    assert diff == frozenset({"契約到期日"})
    assert len(RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208) == 21


def test_raw_column_rename_covers_all_chinese_post_columns() -> None:
    """RAW_COLUMN_RENAME must map every post-20251208 中文欄位 to English."""
    chinese_keys = set(RAW_COLUMN_RENAME.keys())
    expected = set(RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208)
    missing = expected - chinese_keys
    assert not missing, f"RAW_COLUMN_RENAME missing keys: {missing}"


def test_engine_required_v6_has_can_buy_can_sell_no_mark_source() -> None:
    """v6 D-soft: ENGINE_REQUIRED 含 can_buy/can_sell (R10.10 3ii) 不含 mark_source/mark_price_basis."""
    assert "can_buy" in ENGINE_REQUIRED_COLUMNS
    assert "can_sell" in ENGINE_REQUIRED_COLUMNS
    assert "mark_source" not in ENGINE_REQUIRED_COLUMNS  # v3 root cause 已廢
    assert "mark_price_basis" not in ENGINE_REQUIRED_COLUMNS  # v4 廢，內化 portfolio


def test_strategy_view_v6_10_cols_no_iv_delta() -> None:
    """v6 STRATEGY_VIEW = 10 cols (raw → projected; iv/delta 留 enrich)."""
    assert len(STRATEGY_VIEW_COLUMNS) == 10
    assert "iv" not in STRATEGY_VIEW_COLUMNS
    assert "delta" not in STRATEGY_VIEW_COLUMNS
    assert "can_buy" not in STRATEGY_VIEW_COLUMNS  # 留 enrich.add_can_buy_can_sell


def test_value_normalization_chinese_to_english_mapping() -> None:
    """VALUE_NORMALIZATION 含 option_type + trading_session 兩層 mapping."""
    assert VALUE_NORMALIZATION["option_type_zh_to_en"] == {"買權": "call", "賣權": "put"}
    assert VALUE_NORMALIZATION["trading_session_zh_to_en"] == {
        "一般": "regular",
        "盤後": "after_hours",
    }


def test_column_nullability_key_cols_not_nullable() -> None:
    """Key cols (date/expiry/strike/option_type) 必 not-nullable (schema invariant)."""
    for key_col in ("date", "expiry", "strike", "option_type"):
        assert COLUMN_NULLABILITY[key_col] is False, f"{key_col} should be not nullable"


def test_column_nullability_quote_cols_nullable() -> None:
    """bid/ask/iv/delta 必 nullable (illiquid 真實場景)."""
    for nullable_col in ("bid", "ask", "iv", "delta"):
        assert COLUMN_NULLABILITY[nullable_col] is True


def test_column_dtypes_strike_float64() -> None:
    """strike 必 float64 (個股選擇權含小數 55.0000，TAIFEX 真實實證)."""
    assert COLUMN_DTYPES["strike"] == "float64"


def test_column_dtypes_can_buy_sell_bool() -> None:
    """v6 R10.10 3ii: can_buy / can_sell 必 bool (純 execution gate)."""
    assert COLUMN_DTYPES["can_buy"] == "bool"
    assert COLUMN_DTYPES["can_sell"] == "bool"


def test_pre_post_20251208_english_diff_only_contract_date() -> None:
    """English normalised: post 比 pre 多 1 col 'contract_date'."""
    diff = RAW_TAIFEX_COLUMNS_POST_20251208 - RAW_TAIFEX_COLUMNS_PRE_20251208
    assert diff == frozenset({"contract_date"})
