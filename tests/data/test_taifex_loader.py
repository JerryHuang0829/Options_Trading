"""Tests for src/data/taifex_loader.py — Day 1B parse_bulletin (D-soft).

R10.7「實測再寫」紀律：用 Pre-1 抽的真實 fixtures (CP950 byte-stream from
2024 annual ZIP + 2025-12-15 daily POST) — NOT 手寫 mock byte literal.

Fixtures (Pre-1 done):
  - taifex_2024_01_02_pre_20251208_sample.csv
    從 2024.zip > 2024_opt_01.csv 抽 2024-01-02: 15 TXO + 5 CAO，header 20 / data 21
    (annual ZIP trailing comma — silent shift trap)
  - taifex_2025_12_15_post_20251208_TXO_sample.csv
    daily POST 2025-12-15: 30 TXO 含一般+盤後，header 22 / data 21 (header
    trailing comma — Unnamed col)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data.schema import (
    RAW_TAIFEX_COLUMNS_OLDEST,
    RAW_TAIFEX_COLUMNS_POST_20251208,
    RAW_TAIFEX_COLUMNS_PRE_20251208,
)
from src.data.taifex_loader import parse_bulletin

FIXTURE_DIR = Path(__file__).parent / "fixtures"
OLDEST_FIXTURE = FIXTURE_DIR / "taifex_2018_01_oldest_sample.csv"
PRE_FIXTURE = FIXTURE_DIR / "taifex_2024_01_02_pre_20251208_sample.csv"
POST_FIXTURE = FIXTURE_DIR / "taifex_2025_12_15_post_20251208_TXO_sample.csv"


# ---------------------------------------------------------------------------
# parse_bulletin (Day 1B)
# ---------------------------------------------------------------------------


def test_parse_bulletin_oldest_18_col_returns_OLDEST_columns_set() -> None:
    """v6 §2 + 7yr backfill 實證: 2018 era 18-col schema (no 漲跌價/漲跌%).

    Drift gate 三向度 set comparison：parse_bulletin 對 18 cols header auto-detect
    為 OLDEST schema 並通過。Real fixture 抽自 annual_2018.zip > 2018_opt_01.csv
    前 30 行 (header + 29 data row).
    """
    df = parse_bulletin(str(OLDEST_FIXTURE))
    actual_cols = set(df.columns)
    assert actual_cols == set(RAW_TAIFEX_COLUMNS_OLDEST), (
        f"Expected {RAW_TAIFEX_COLUMNS_OLDEST}, got {actual_cols}"
    )
    # 反證: 不應有 change / change_pct / contract_date col
    assert "change" not in actual_cols
    assert "change_pct" not in actual_cols
    assert "contract_date" not in actual_cols


def test_parse_bulletin_pre_20251208_returns_PRE_columns_set() -> None:
    """v6 §2 schema: pre-2025-12-08 fixture parse → 20 normalised English cols."""
    df = parse_bulletin(str(PRE_FIXTURE))
    actual_cols = set(df.columns)
    assert actual_cols == set(RAW_TAIFEX_COLUMNS_PRE_20251208), (
        f"Expected {RAW_TAIFEX_COLUMNS_PRE_20251208}, got {actual_cols}"
    )


def test_parse_bulletin_post_20251208_returns_POST_columns_set() -> None:
    """v6 §2 schema: post-2025-12-08 fixture parse → 21 cols 含 contract_date."""
    df = parse_bulletin(str(POST_FIXTURE))
    actual_cols = set(df.columns)
    assert actual_cols == set(RAW_TAIFEX_COLUMNS_POST_20251208), (
        f"Expected {RAW_TAIFEX_COLUMNS_POST_20251208}, got {actual_cols}"
    )
    assert "contract_date" in df.columns


def test_parse_bulletin_keeps_non_txo_contracts() -> None:
    """Codex R10.5 P2-#3: parse_bulletin 不過濾 contract (TXO filter 在 to_strategy_view)."""
    df = parse_bulletin(str(PRE_FIXTURE))
    contracts = set(df["contract"].unique())
    # PRE fixture 含 TXO + CAO (個股選擇權)
    assert "TXO" in contracts
    assert "CAO" in contracts, f"Expected CAO 個股選擇權; got contracts={contracts}"


def test_parse_bulletin_no_silent_index_shift_first_col_is_date() -> None:
    """R10.7 F2 critical: pre-fixture (header 20 / data 21) 用 default pd.read_csv 會 silent
    shift index → first col 變 'TXO'. parse_bulletin 用 index_col=False 應正確 → first
    row date col == '2024-01-02' Timestamp."""
    df = parse_bulletin(str(PRE_FIXTURE))
    first_date = df.iloc[0]["date"]
    assert first_date == pd.Timestamp("2024-01-02"), (
        f"silent index shift detected; expected 2024-01-02, got {first_date}"
    )


def test_parse_bulletin_normalises_chinese_enum_values() -> None:
    """Step 7: 買權→call / 賣權→put / 一般→regular / 盤後→after_hours."""
    df = parse_bulletin(str(POST_FIXTURE))
    # POST fixture 是 TXO daily POST 含一般+盤後
    assert set(df["option_type"].unique()).issubset({"call", "put"})
    assert set(df["trading_session"].unique()).issubset({"regular", "after_hours"})
    # 確認**真有兩個 session** (not just regular)
    assert "after_hours" in set(df["trading_session"].unique())


def test_parse_bulletin_strike_is_float64() -> None:
    """Step 8: strike 必 float64 (個股選擇權含小數 55.0000 真實實證)."""
    df = parse_bulletin(str(PRE_FIXTURE))
    assert df["strike"].dtype == "float64"


def test_parse_bulletin_dash_to_nan_for_illiquid() -> None:
    """na_values=['-']: 真實 illiquid row 的 '-' 會解碼為 NaN, 不是字串 '-'."""
    df = parse_bulletin(str(PRE_FIXTURE))
    # CAO 個股選擇權通常 illiquid → bid/ask 大量 '-' → NaN
    cao_rows = df[df["contract"] == "CAO"]
    assert cao_rows["bid"].isna().any(), "CAO illiquid rows 應該有 NaN bid"


def test_parse_bulletin_post_contract_date_parsed_as_datetime() -> None:
    """Step 8: post-2025-12-08 contract_date YYYYMMDD string → datetime."""
    df = parse_bulletin(str(POST_FIXTURE))
    cd = df["contract_date"].iloc[0]
    # 對照 fixture: 2025-12-15 抓 daily, expiry should be 20251217 (third Wed)
    assert cd == pd.Timestamp("2025-12-17"), (
        f"contract_date parse failed; expected 2025-12-17, got {cd}"
    )


def test_parse_bulletin_missing_file_raises() -> None:
    """Boundary: nonexistent path → FileNotFoundError (not silent default)."""
    with pytest.raises(FileNotFoundError, match="csv_path does not exist"):
        parse_bulletin("/nonexistent/path/dummy.csv")


# ---------------------------------------------------------------------------
# Stubs for Day 1C / Day 2 / Day 3 (still NotImplementedError)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# download_daily_bulletin (Day 1C) — POST 雙模式 mock-based tests (NOT 真網路)
# R10.7「實測再寫」+ Codex R10.8 P2 #2 修法: endpoint 從憑記憶 → 實證 POST optDataDown
# ---------------------------------------------------------------------------


class _MockResponse:
    """Mimic minimal requests.Response interface for offline tests."""

    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests as _r  # type: ignore[import-untyped]

            raise _r.HTTPError(f"status_code={self.status_code}")


class _MockSession:
    """Mimic requests.Session.post; record args for assertion."""

    def __init__(self, response_body: bytes = b"X" * 200) -> None:
        self.calls: list[dict] = []
        self.response_body = response_body

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _MockResponse(self.response_body)


def test_download_daily_url_pattern_via_mock_session(tmp_path) -> None:
    """R10.8 實證 endpoint: daily mode POST optDataDown payload 對齊 spec."""
    from src.data.taifex_loader import (
        TAIFEX_OPT_DATA_DOWN_URL,
        TAIFEX_REFERER,
        download_daily_bulletin,
    )

    mock = _MockSession(response_body=b"date,contract\n" + b"X" * 500)
    path = download_daily_bulletin(
        mode="daily",
        cache_dir=str(tmp_path),
        date="2025-12-15",
        session=mock,
    )
    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert call["url"] == TAIFEX_OPT_DATA_DOWN_URL
    # Payload 對齊 R10.8 實證: down_type=1 / queryStartDate=YYYY/MM/DD / TXO
    assert call["data"]["down_type"] == "1"
    assert call["data"]["queryStartDate"] == "2025/12/15"
    assert call["data"]["queryEndDate"] == "2025/12/15"
    assert call["data"]["commodity_id"] == "TXO"
    assert call["headers"]["Referer"] == TAIFEX_REFERER
    # 寫入 cache 真實存在
    assert Path(path).is_file()
    assert Path(path).name == "daily_2025-12-15_TXO.csv"


def test_download_annual_url_pattern_via_mock_session(tmp_path) -> None:
    """R10.8 實證 endpoint: annual mode POST optDataDown payload."""
    from src.data.taifex_loader import download_daily_bulletin

    mock = _MockSession(response_body=b"PK\x03\x04" + b"X" * 500)  # ZIP magic
    path = download_daily_bulletin(
        mode="annual",
        cache_dir=str(tmp_path),
        year=2024,
        session=mock,
    )
    call = mock.calls[0]
    assert call["data"]["down_type"] == "2"
    assert call["data"]["his_year"] == "2024"
    assert "queryStartDate" not in call["data"]  # annual 不含 daily-only fields
    assert Path(path).name == "annual_2024.zip"


def test_download_uses_cache_on_second_call(tmp_path) -> None:
    """O(1) cache hit: 第二次呼叫不打網路 (mock.calls 仍只 1)."""
    from src.data.taifex_loader import download_daily_bulletin

    # ZIP magic prefix needed since annual mode now guards against non-ZIP body
    mock = _MockSession(response_body=b"PK\x03\x04" + b"X" * 500)
    path1 = download_daily_bulletin(
        mode="annual",
        cache_dir=str(tmp_path),
        year=2024,
        session=mock,
    )
    path2 = download_daily_bulletin(
        mode="annual",
        cache_dir=str(tmp_path),
        year=2024,
        session=mock,
    )
    assert path1 == path2
    assert len(mock.calls) == 1, "second call should hit cache, not POST"


def test_download_annual_non_zip_body_raises(tmp_path) -> None:
    """Bug 4 守護：TAIFEX 對未滿一年的 his_year 回 HTML → magic-bytes guard raise."""
    from src.data.taifex_loader import download_daily_bulletin

    html_body = b"<!DOCTYPE HTML PUBLIC>" + b"X" * 500  # > 100 bytes 過 short-body gate
    mock = _MockSession(response_body=html_body)
    with pytest.raises(ValueError, match="non-ZIP body"):
        download_daily_bulletin(
            mode="annual",
            cache_dir=str(tmp_path),
            year=2026,
            session=mock,
        )
    # corrupt body 不應寫進 cache
    cache_path = Path(tmp_path) / "raw_zip" / "annual_2026.zip"
    assert not cache_path.exists()


def test_download_short_body_raises_holiday_or_invalid(tmp_path) -> None:
    """Body < 100 bytes → ValueError (holiday / no-data 防 silent corrupt)."""
    from src.data.taifex_loader import download_daily_bulletin

    mock = _MockSession(response_body=b"")  # empty body
    with pytest.raises(ValueError, match="short body"):
        download_daily_bulletin(
            mode="daily",
            cache_dir=str(tmp_path),
            date="2024-01-01",
            session=mock,
        )


def test_download_invalid_args_raises(tmp_path) -> None:
    """Boundary: invalid mode / date / year combo → ValueError 不 silent default."""
    from src.data.taifex_loader import download_daily_bulletin

    with pytest.raises(ValueError, match="mode must be"):
        download_daily_bulletin(mode="weekly", cache_dir=str(tmp_path))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires date"):
        download_daily_bulletin(mode="daily", cache_dir=str(tmp_path))
    with pytest.raises(ValueError, match="requires year"):
        download_daily_bulletin(mode="annual", cache_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# to_strategy_view (Day 2) — TXO regular monthly only + holidays expiry
# ---------------------------------------------------------------------------


def test_to_strategy_view_pre_20251208_returns_10_col_set() -> None:
    """Day 2: pre fixture → STRATEGY_VIEW (10 cols)."""
    from src.data.schema import STRATEGY_VIEW_COLUMNS
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    sv = to_strategy_view(raw)
    assert set(sv.columns) == set(STRATEGY_VIEW_COLUMNS)
    assert len(sv.columns) == 10


def test_to_strategy_view_post_20251208_returns_10_col_set() -> None:
    """Day 2: post fixture (含 contract_date) → STRATEGY_VIEW (10 cols)."""
    from src.data.schema import STRATEGY_VIEW_COLUMNS
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(POST_FIXTURE))
    sv = to_strategy_view(raw)
    assert set(sv.columns) == set(STRATEGY_VIEW_COLUMNS)


def test_to_strategy_view_filters_out_non_txo_contracts() -> None:
    """Step 1: contract != 'TXO' (e.g. CAO 個股選擇權) 必過濾."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    # PRE fixture 含 CAO; sv 不該保留
    assert "CAO" in set(raw["contract"].unique())
    sv = to_strategy_view(raw)
    # sv 沒有 contract col (10-col schema 不含)，所以從 expiry/strike 觀察
    # 改檢 sv row count 比 raw TXO row count 一致
    # Strip cmw trailing spaces (fixture 真實有 "202401  " trailing 空白)
    # to align with to_strategy_view step 3 internal strip + fullmatch.
    cmw_stripped = raw["contract_month_week"].astype(str).str.strip()
    raw_txo_regular_monthly_count = len(
        raw[
            (raw["contract"] == "TXO")
            & (raw["trading_session"] == "regular")
            & (cmw_stripped.str.fullmatch(r"\d{6}"))
        ].dropna(subset=["strike"])
    )
    assert len(sv) == raw_txo_regular_monthly_count


def test_to_strategy_view_filters_out_after_hours_session() -> None:
    """Step 2 (R10.8 P2-B 必加): trading_session='盤後' (after_hours) 必過濾;
    POST fixture 含同 strike 一般+盤後 兩 row → sv 只該保 一般."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(POST_FIXTURE))
    assert "after_hours" in set(raw["trading_session"].unique())
    sv = to_strategy_view(raw)
    # sv 沒 trading_session col, 但 (date, expiry, strike, option_type) 必 unique
    # (若 after_hours 沒 filter, 同 strike 會有兩 row)
    key_cols = ["date", "expiry", "strike", "option_type"]
    n_unique = sv[key_cols].drop_duplicates().shape[0]
    assert n_unique == len(sv), (
        f"after_hours dedup leak: {len(sv)} rows but only {n_unique} unique keys"
    )


def test_to_strategy_view_filters_out_weekly_options() -> None:
    """Step 3: 週選 (YYYYMMWn) 必過濾 (Phase 1 月選 only)."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    # PRE fixture 是 2024-01-02, contains 月選 + 週選 (W1 etc.)
    has_weekly = raw["contract_month_week"].astype(str).str.contains("W").any()
    if not has_weekly:
        pytest.skip("PRE fixture has no weekly option rows; skip 週選 filter assertion")
    sv = to_strategy_view(raw)
    # sv 沒 contract_month_week col, 但所有 expiry 必對應月選結算日 (第 3 週三 + 順延)
    # 至少 expiry 不該有 fixture 內任何「W1 推算結果」
    assert len(sv) > 0


def test_to_strategy_view_pre_expiry_uses_holidays_third_wednesday() -> None:
    """Step 4 pre: holidays 第三個週三 (2024-01 → 2024-01-17 對照 R10.10 4 case)."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    sv = to_strategy_view(raw)
    # PRE fixture 是 2024-01-02 trade date; cmw='202401' → expiry 2024-01-17
    expiry_2024_01 = sv[sv["expiry"] == pd.Timestamp("2024-01-17")]
    assert not expiry_2024_01.empty, (
        f"expected 2024-01 monthly TXO with expiry 2024-01-17, "
        f"got expiries: {sorted(set(sv['expiry'].astype(str)))}"
    )


def test_to_strategy_view_post_expiry_uses_contract_date_col() -> None:
    """Step 4 post: post-2025-12-08 用 parse_bulletin 已 parsed contract_date (對照 fixture 2)."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(POST_FIXTURE))
    sv = to_strategy_view(raw)
    # POST fixture 2025-12-15 trade date; contract_date=20251217 (3rd Wed 2025-12)
    expected_expiry = pd.Timestamp("2025-12-17")
    assert (sv["expiry"] == expected_expiry).any(), (
        f"expected post-2025-12-08 expiry 2025-12-17 from contract_date col, "
        f"got expiries: {sorted(set(sv['expiry'].astype(str)))}"
    )


def test_to_strategy_view_empty_input_raises() -> None:
    """Boundary: empty raw_df → ValueError (no silent default)."""
    from src.data.taifex_loader import to_strategy_view

    with pytest.raises(ValueError, match="empty"):
        to_strategy_view(pd.DataFrame())


def test_to_strategy_view_no_txo_input_raises() -> None:
    """Boundary: raw 全是 CAO 沒 TXO → ValueError 不 silent return empty."""
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    raw_no_txo = raw[raw["contract"] != "TXO"].copy()
    if raw_no_txo.empty:
        pytest.skip("PRE fixture only TXO; skip non-TXO empty test")
    with pytest.raises(ValueError, match="no TXO rows"):
        to_strategy_view(raw_no_txo)


def test_to_strategy_view_column_order_is_canonical() -> None:
    """Step 6: output column order matches STRATEGY_VIEW_COLUMN_ORDER (frozenset 無序 → 必固定)."""
    from src.data.schema import STRATEGY_VIEW_COLUMN_ORDER
    from src.data.taifex_loader import to_strategy_view

    raw = parse_bulletin(str(PRE_FIXTURE))
    sv = to_strategy_view(raw)
    assert list(sv.columns) == STRATEGY_VIEW_COLUMN_ORDER


###############################################################################
# backfill_range — Day 3 annual mode batch (mocked download_daily_bulletin)
###############################################################################

import zipfile  # noqa: E402  (test-only import grouped with backfill section)


def _build_year_zip(zip_path: Path, csv_member_name: str, csv_bytes: bytes) -> None:
    """Build a single-CSV .zip mimicking TAIFEX annual ZIP layout."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_member_name, csv_bytes)


def test_backfill_range_invalid_mode_raises(tmp_path: Path) -> None:
    from src.data.taifex_loader import backfill_range

    with pytest.raises(ValueError, match="only mode='annual'"):
        backfill_range("2024-01-01", "2024-01-31", str(tmp_path), mode="daily")  # type: ignore[arg-type]


def test_backfill_range_invalid_date_range_raises(tmp_path: Path) -> None:
    from src.data.taifex_loader import backfill_range

    with pytest.raises(ValueError, match="start_date > end_date"):
        backfill_range("2024-02-01", "2024-01-01", str(tmp_path))


def test_backfill_range_processes_year_zip_and_caches_two_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full integration: monkeypatch download → real unzip → parse → cache.save_chain × 2."""
    from src.data import taifex_loader
    from src.data.cache import is_cached, list_cached_dates

    # Build fake annual ZIP from PRE_FIXTURE (single 2024-01-02 CSV)
    fake_zip = tmp_path / "annual_2024.zip"
    _build_year_zip(fake_zip, "2024_opt_01.csv", PRE_FIXTURE.read_bytes())

    def fake_download(*, mode, year, cache_dir, **kwargs):  # noqa: ARG001
        return str(fake_zip)

    monkeypatch.setattr(taifex_loader, "download_daily_bulletin", fake_download)

    summary = taifex_loader.backfill_range(
        "2024-01-02",
        "2024-01-02",
        str(tmp_path),
        sleep_between_sec=0,
    )
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["date"] == "2024-01-02"
    assert bool(row["raw_saved"])  # numpy bool, not Python bool
    assert bool(row["sv_saved"])
    assert row["n_raw_rows"] > 0
    assert row["n_sv_rows"] > 0
    # Verify both layers materialised
    assert is_cached(str(tmp_path), "2024-01-02", layer="raw")
    assert is_cached(str(tmp_path), "2024-01-02", layer="strategy_view")
    assert list_cached_dates(str(tmp_path), layer="raw") == ["2024-01-02"]
    # R11.3 P2: manifest 進主流程 → 應寫進 _backfill_manifest.csv 7-col schema
    manifest = Path(tmp_path) / "_backfill_manifest.csv"
    assert manifest.exists()
    header_line = manifest.read_text(encoding="utf-8").splitlines()[0]
    assert header_line == "date,year,layer,n_rows,n_cols,size_kb,written_at", (
        f"R11.3 P2 manifest header schema mismatch: got {header_line!r}"
    )


def test_manifest_schema_drift_raises_on_append(tmp_path: Path) -> None:
    """R11.3 P2 守護：舊 6-col manifest header 存在 → _append_manifest_row raise."""
    from src.data.taifex_loader import _append_manifest_row

    # 種一個 R11.1 ad-hoc 寫的舊 6-col manifest
    manifest = tmp_path / "_backfill_manifest.csv"
    manifest.write_text("date,year,layer,n_rows,n_cols,size_kb\n2024-01-02,2024,raw,100,20,50\n")
    with pytest.raises(ValueError, match="manifest schema mismatch"):
        _append_manifest_row(
            str(tmp_path),
            date_str="2024-01-03",
            year=2024,
            layer="raw",
            n_rows=100,
            n_cols=20,
            size_kb=50.0,
        )


def test_backfill_range_skip_cached_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call with skip_cached=True should not re-save (summary empty)."""
    from src.data import taifex_loader

    fake_zip = tmp_path / "annual_2024.zip"
    _build_year_zip(fake_zip, "2024_opt_01.csv", PRE_FIXTURE.read_bytes())
    monkeypatch.setattr(
        taifex_loader,
        "download_daily_bulletin",
        lambda *, mode, year, cache_dir, **kw: str(fake_zip),  # noqa: ARG005
    )

    s1 = taifex_loader.backfill_range(
        "2024-01-02",
        "2024-01-02",
        str(tmp_path),
        sleep_between_sec=0,
    )
    s2 = taifex_loader.backfill_range(
        "2024-01-02",
        "2024-01-02",
        str(tmp_path),
        sleep_between_sec=0,
        skip_cached=True,
    )
    assert len(s1) == 1
    assert len(s2) == 0  # all skipped


def test_backfill_range_progress_callback_invoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """progress_callback fires per processed/skipped date with status string."""
    from src.data import taifex_loader

    fake_zip = tmp_path / "annual_2024.zip"
    _build_year_zip(fake_zip, "2024_opt_01.csv", PRE_FIXTURE.read_bytes())
    monkeypatch.setattr(
        taifex_loader,
        "download_daily_bulletin",
        lambda *, mode, year, cache_dir, **kw: str(fake_zip),  # noqa: ARG005
    )

    events: list[tuple[str, str]] = []
    taifex_loader.backfill_range(
        "2024-01-02",
        "2024-01-02",
        str(tmp_path),
        sleep_between_sec=0,
        progress_callback=lambda d, s: events.append((d, s)),
    )
    assert events == [("2024-01-02", "saved")]

    # Second pass: should emit 'skipped_cached'
    events.clear()
    taifex_loader.backfill_range(
        "2024-01-02",
        "2024-01-02",
        str(tmp_path),
        sleep_between_sec=0,
        progress_callback=lambda d, s: events.append((d, s)),
    )
    assert events == [("2024-01-02", "skipped_cached")]


def test_backfill_range_filters_dates_outside_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ZIP contains 2024-01-02 row; window 2024-02-01→2024-02-28 → 0 saved."""
    from src.data import taifex_loader

    fake_zip = tmp_path / "annual_2024.zip"
    _build_year_zip(fake_zip, "2024_opt_01.csv", PRE_FIXTURE.read_bytes())
    monkeypatch.setattr(
        taifex_loader,
        "download_daily_bulletin",
        lambda *, mode, year, cache_dir, **kw: str(fake_zip),  # noqa: ARG005
    )

    summary = taifex_loader.backfill_range(
        "2024-02-01",
        "2024-02-28",
        str(tmp_path),
        sleep_between_sec=0,
    )
    assert len(summary) == 0
