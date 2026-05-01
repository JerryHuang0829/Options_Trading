"""TAIFEX TXO schema contract (v6 D-soft, R10.13 GO-WITH-CAVEATS).

Single source of truth for column names / dtypes / nullability across the data
pipeline (raw → strategy_view → enriched → engine). Replaces "column count"
acceptance criteria with frozenset[str] + dtype + nullability triple-gate
(Codex R10.5 P2 #5 修法).

Schema versioning:
  - Pre 2025-12-08: 20 raw cols (TAIFEX 全歷史)
  - Post 2025-12-08: 21 raw cols (含 `契約到期日`)
  See docs/taifex_data_source_spec.md for empirical evidence.

v6 廢 (R10.10 → R10.11 hybrid pivot):
  - mark_source enum (連 5 輪 P1 root cause)
  - mark_price_basis col (內化進 portfolio._mid_price_with_basis)
  - drop_unmarkable (Codex R10.11 抓 60% drop 不可行)

v6 保留 (R10.10 3ii side-specific execution gate):
  - can_buy / can_sell pure execution gate (mark 由 portfolio.mark_to_market
    hybrid mark_policy 處理，與 execution 分離)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# RAW (CSV header) — 中文原始欄位名（CP950 實證取得）
# ---------------------------------------------------------------------------

RAW_TAIFEX_COLUMNS_CHINESE_OLDEST: frozenset[str] = frozenset(
    {
        "交易日期",
        "契約",
        "到期月份(週別)",
        "履約價",
        "買賣權",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
        "成交量",
        "結算價",
        "未沖銷契約數",
        "最後最佳買價",
        "最後最佳賣價",
        "歷史最高價",
        "歷史最低價",
        "是否因訊息面暫停交易",
        "交易時段",
    }
)  # 18 cols (2018 era 實證 — 缺 `漲跌價` / `漲跌%`；換版時點未實證)

RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208: frozenset[str] = (
    RAW_TAIFEX_COLUMNS_CHINESE_OLDEST | frozenset({"漲跌價", "漲跌%"})
)  # 20 cols (含 `漲跌價` / `漲跌%`，→ 2025-12-07)

RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208: frozenset[str] = (
    RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208 | frozenset({"契約到期日"})
)  # 21 cols (2025-12-08 起，TAIFEX 公告)

# ---------------------------------------------------------------------------
# RAW (normalised English) — 中→英 mapping
# ---------------------------------------------------------------------------

RAW_COLUMN_RENAME: dict[str, str] = {
    "交易日期": "date",
    "契約": "contract",  # TXO / CAO / CBO / ...（非 TXF — 期貨在另檔）
    "到期月份(週別)": "contract_month_week",  # YYYYMM 月選 / YYYYMMWn 週選
    "履約價": "strike",
    "買賣權": "option_type_zh",  # 買權 / 賣權 → call / put (見 VALUE_NORMALIZATION)
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "收盤價": "close",  # NOTE: != settle (TAIFEX 計算 fair value)
    "成交量": "volume",
    "結算價": "settle",  # TAIFEX fair value，無交易日仍有值
    "未沖銷契約數": "open_interest",
    "最後最佳買價": "bid",
    "最後最佳賣價": "ask",
    "歷史最高價": "historical_high",
    "歷史最低價": "historical_low",
    "是否因訊息面暫停交易": "halt_flag",
    "交易時段": "trading_session_zh",  # 一般 / 盤後 → regular / after_hours
    "漲跌價": "change",
    "漲跌%": "change_pct",
    "契約到期日": "contract_date",  # post 2025-12-08 only; YYYYMMDD string
}

RAW_TAIFEX_COLUMNS_OLDEST: frozenset[str] = frozenset(
    {
        "date",
        "contract",
        "contract_month_week",
        "strike",
        "option_type",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "settle",
        "open_interest",
        "bid",
        "ask",
        "historical_high",
        "historical_low",
        "halt_flag",
        "trading_session",
    }
)  # 18 cols normalised (2018 era)

RAW_TAIFEX_COLUMNS_PRE_20251208: frozenset[str] = RAW_TAIFEX_COLUMNS_OLDEST | frozenset(
    {"change", "change_pct"}
)  # 20 cols normalised

RAW_TAIFEX_COLUMNS_POST_20251208: frozenset[str] = RAW_TAIFEX_COLUMNS_PRE_20251208 | frozenset(
    {"contract_date"}
)  # 21 cols normalised

# ---------------------------------------------------------------------------
# STRATEGY_VIEW (Day 2 to_strategy_view 輸出) — TXO regular session only
# ---------------------------------------------------------------------------

STRATEGY_VIEW_COLUMNS: frozenset[str] = frozenset(
    {
        "date",
        "expiry",
        "strike",
        "option_type",
        "settle",
        "close",
        "bid",
        "ask",
        "volume",
        "open_interest",
    }
)  # 10 cols (TXO only, regular session, 不含 iv/delta/dte/underlying — 留 enrich)

# Fixed column order for to_strategy_view output (frozenset 無序，需 explicit list)
STRATEGY_VIEW_COLUMN_ORDER: list[str] = [
    "date",
    "expiry",
    "strike",
    "option_type",
    "settle",
    "close",
    "bid",
    "ask",
    "volume",
    "open_interest",
]

SCHEMA_VIEW_VERSION = "sv-v1"

# ---------------------------------------------------------------------------
# ENGINE_REQUIRED (Day 5 enrich 後 → engine.run_backtest 期待) — 12 cols
# ---------------------------------------------------------------------------

ENGINE_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "date",
        "expiry",
        "strike",
        "option_type",
        "settle",
        "bid",
        "ask",
        "iv",
        "delta",
        "dte",
        "underlying",
        "can_buy",  # R10.10 3ii: ask.notna() (純 execution gate)
        "can_sell",  # R10.10 3ii: bid.notna()
        # 廢 mark_price_basis (R10.10 / R10.11: portfolio._mid_price_with_basis 內化)
        # 廢 mark_source (R10.10 連 5 輪 P1 root cause)
    }
)

ENRICHED_OPTIONAL_COLUMNS: frozenset[str] = frozenset(
    {
        "q_pit",
        "q_pit_source",
        "q_pit_audit_flags",
        "iv_source",
        "delta_source",
        "close",
        "halt_flag",
        "volume",
        "open_interest",
        # Week 5 Day 5.1: vol surface 反算 model_price (cache miss / insufficient_data
        # / all_failed → NaN; SVI/SABR/poly 反算 IV → BSM-Merton invert)
        "model_price",
    }
)

# ---------------------------------------------------------------------------
# DTYPE 期待 (Day 2 _validate_schema gate)
# ---------------------------------------------------------------------------

COLUMN_DTYPES: dict[str, str] = {
    "date": "datetime64[ns]",
    "expiry": "datetime64[ns]",
    "contract_date": "datetime64[ns]",
    "contract": "string",
    "contract_month_week": "string",
    "strike": "float64",  # TAIFEX 履約價含小數（個股選擇權 55.0000）
    "option_type": "string",
    "trading_session": "string",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "int64",
    "open_interest": "int64",
    "settle": "float64",
    "bid": "float64",
    "ask": "float64",
    "historical_high": "float64",
    "historical_low": "float64",
    "halt_flag": "string",
    "change": "float64",
    "change_pct": "float64",
    "iv": "float64",
    "delta": "float64",
    "dte": "int64",
    "underlying": "float64",
    "can_buy": "bool",
    "can_sell": "bool",
    "q_pit": "float64",
    "q_pit_source": "string",
    "model_price": "float64",
}

COLUMN_NULLABILITY: dict[str, bool] = {
    # not nullable (key cols)
    "date": False,
    "expiry": False,
    "strike": False,
    "option_type": False,
    "contract": False,
    "contract_month_week": False,
    "underlying": False,
    "trading_session": False,
    "can_buy": False,
    "can_sell": False,
    # nullable (illiquid / quote-缺 場景)
    "settle": True,
    "close": True,
    "bid": True,
    "ask": True,
    "iv": True,  # noise floor 可 NaN
    "delta": True,
    "halt_flag": True,
    "open": True,
    "high": True,
    "low": True,
    "volume": True,
    "open_interest": True,
    "historical_high": True,
    "historical_low": True,
    "change": True,
    "change_pct": True,
    "model_price": True,  # cache miss / insufficient_data / all_failed → NaN
}

# ---------------------------------------------------------------------------
# VALUE NORMALIZATION (中→英 enum value mapping; R10.8 P2-B + 實證)
# ---------------------------------------------------------------------------

VALUE_NORMALIZATION: dict[str, dict[str, str]] = {
    "option_type_zh_to_en": {"買權": "call", "賣權": "put"},
    "trading_session_zh_to_en": {"一般": "regular", "盤後": "after_hours"},
}

# ---------------------------------------------------------------------------
# Cache layer versioning (Day 3 cache.py)
# ---------------------------------------------------------------------------

RAW_CACHE_VERSION = "raw-v1"
STRATEGY_VIEW_CACHE_VERSION = "sv-v1"
