"""TAIFEX daily option bulletin POST downloader + CP950 parser.

D-soft Day 1 (2026-04-28). Loads 5yr TAIFEX TXO + 個股選擇權 raw chain into
the data pipeline. Production stages downstream:

  - parse_bulletin(path) → 20/21-col raw DataFrame (含全 contracts，TXO filter
    在 Day 2 to_strategy_view)
  - download_daily_bulletin(...) → POST `/cht/3/optDataDown` 雙模式 daily/annual
  - to_strategy_view(raw_df) → 10-col TXO regular session (Day 2)
  - backfill_range(...) → Day 3 annual mode 5yr backfill

## Endpoint (R10.8 實證, curl POST 13.5 MB ZIP, 2026-04-27)

POST https://www.taifex.com.tw/cht/3/optDataDown
Headers: Referer: https://www.taifex.com.tw/cht/3/optDailyMarketView
Daily mode payload: {down_type=1, queryStartDate=YYYY/MM/DD,
                     queryEndDate=YYYY/MM/DD, commodity_id=TXO,
                     commodity_id2=''}; result = .csv
Annual mode payload: {down_type=2, his_year=YYYY}; result = .zip

## Schema (CP950 解碼實證)

  - Pre 2025-12-08: 20 cols (中文 see src/data/schema.py)
  - Post 2025-12-08: 21 cols (上 + `契約到期日`)
  - Annual ZIP CSV: header 20 / data 21 (trailing comma) → silent index
    shift → 必用 index_col=False (Codex R10.7 F2 修法)
  - Daily POST CSV: header 22 / data 21 (trailing comma 在 header) → 需 drop
    'Unnamed:' col

詳見 docs/taifex_data_source_spec.md。
"""

from __future__ import annotations

import shutil
import time
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import holidays
import pandas as pd
import requests  # type: ignore[import-untyped]

from src.data.cache import is_cached, save_chain
from src.data.schema import (
    RAW_COLUMN_RENAME,
    RAW_TAIFEX_COLUMNS_CHINESE_OLDEST,
    RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208,
    RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208,
    STRATEGY_VIEW_COLUMN_ORDER,
    STRATEGY_VIEW_COLUMNS,
    VALUE_NORMALIZATION,
)

# R10.8 P2-C decision a (user 拍板「holidays 套件」, 4 case 對齊 R10.10):
# TXO 月選結算 = 該月第三個週三 + 假日順延.
# Range = 2018-2028 covers 7yr pivot (2026-04-28 user §0a) backfill
# (2018-01 → 2025-12 main + 2026 forward expiries) + 2027-2028 buffer.
# Widen via holidays.TW(years=range(...)) if extending range.
# (holidays>=0.50 ships py.typed; `TW` is recognised — no `type: ignore` needed.)
_TW_HOLIDAYS = holidays.TW(years=range(2018, 2029))


def _txo_monthly_settlement_date(year: int, month: int) -> pd.Timestamp:
    """TXO monthly option settlement = 3rd Wednesday of month + holiday rollover.

    R10.8 P2-C 實證 4 cases (R10.10):
      2024-01 → 2024-01-17 (3rd Wed, no rollover)
      2024-02 → 2024-02-21 (3rd Wed, 春節 not on this date)
      2024-05 → 2024-05-15
      2025-12 → 2025-12-17 (對照 fixture 2 contract_date=20251217)
    """
    first = pd.Timestamp(year=year, month=month, day=1)
    days_to_wed = (2 - first.weekday()) % 7  # Wed=2 (Mon=0)
    third_wed = first + pd.Timedelta(days=days_to_wed + 14)
    while third_wed.date() in _TW_HOLIDAYS:
        third_wed += pd.Timedelta(days=1)
    return third_wed


if TYPE_CHECKING:
    pass

# R10.8 實證 endpoint (curl POST 13.5 MB ZIP, 2026-04-27)
TAIFEX_OPT_DATA_DOWN_URL = "https://www.taifex.com.tw/cht/3/optDataDown"
TAIFEX_REFERER = "https://www.taifex.com.tw/cht/3/optDailyMarketView"


def download_daily_bulletin(
    *,
    mode: Literal["daily", "annual"],
    cache_dir: str,
    date: str | None = None,
    year: int | None = None,
    timeout_sec: float = 60.0,
    max_retries: int = 3,
    backoff_base_sec: float = 2.0,
    session: requests.Session | None = None,
) -> str:
    """POST TAIFEX optDataDown for daily CSV or annual ZIP; return cached local path.

    R10.8 實證 endpoint (Codex P2 #2 修法 — endpoint 從憑記憶改實證):
      - URL: ``POST https://www.taifex.com.tw/cht/3/optDataDown``
      - Headers: Referer must be https://www.taifex.com.tw/cht/3/optDailyMarketView
      - mode='daily' payload:
        {down_type=1, queryStartDate=YYYY/MM/DD, queryEndDate=YYYY/MM/DD,
         commodity_id=TXO, commodity_id2=''} → result = .csv
      - mode='annual' payload:
        {down_type=2, his_year=YYYY} → result = .zip

    Args:
        mode: 'daily' (single-day CSV, ≤1mo range) or 'annual' (full-year ZIP).
        cache_dir: Local cache root (creates <cache_dir>/raw_zip/ if needed).
        date: ISO 'YYYY-MM-DD' (mode='daily' required; converted to 'YYYY/MM/DD'
              for TAIFEX query).
        year: int year (mode='annual' required, e.g. 2024).
        timeout_sec: per-request HTTP timeout.
        max_retries: 3 attempts on transient ConnectionError / Timeout (exponential
                     backoff backoff_base_sec * 2^n).
        session: optional pre-built requests.Session (test injection / mock).

    Returns:
        Local absolute path string. Cache hit on second call (O(1) file
        existence check) — does NOT re-download.

    Raises:
        ValueError: invalid mode/date/year combo, or HTTP non-2xx, or empty body.
        requests.exceptions.RequestException: after exhausted max_retries.
    """
    # ---- Argument validation (R10.7 紀律：邊界 raise，無 silent default) ----
    if mode not in ("daily", "annual"):
        raise ValueError(f"mode must be 'daily'|'annual', got {mode!r}")
    if mode == "daily" and not date:
        raise ValueError("mode='daily' requires date='YYYY-MM-DD'")
    if mode == "annual" and not year:
        raise ValueError("mode='annual' requires year=YYYY (int)")

    # ---- Cache path ----
    cache_root = Path(cache_dir) / "raw_zip"
    cache_root.mkdir(parents=True, exist_ok=True)
    if mode == "daily":
        # date is e.g. '2025-12-15' → cache filename 'daily_2025-12-15_TXO.csv'
        # arg-validation above guarantees date is non-None when mode='daily'
        assert date is not None
        cache_path = cache_root / f"daily_{date}_TXO.csv"
        # TAIFEX query expects YYYY/MM/DD slashes
        query_date = date.replace("-", "/")
        payload = {
            "down_type": "1",
            "queryStartDate": query_date,
            "queryEndDate": query_date,
            "commodity_id": "TXO",
            "commodity_id2": "",
        }
    else:
        cache_path = cache_root / f"annual_{year}.zip"
        payload = {"down_type": "2", "his_year": str(year)}

    # ---- Cache hit (O(1) — does NOT re-download) ----
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return str(cache_path.resolve())

    # ---- POST with retry/backoff ----
    sess = session if session is not None else requests.Session()
    headers = {
        "Referer": TAIFEX_REFERER,
        "User-Agent": "Mozilla/5.0",
    }
    last_exc: Exception | None = None
    response: requests.Response | None = None
    for attempt in range(max_retries):
        try:
            response = sess.post(
                TAIFEX_OPT_DATA_DOWN_URL,
                data=payload,
                headers=headers,
                timeout=timeout_sec,
                allow_redirects=True,
            )
            response.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(backoff_base_sec * (2**attempt))
                continue
            raise
    if response is None:
        # Should be unreachable (exhausted retries → raise above)
        raise RuntimeError(f"download_daily_bulletin: no response (last_exc={last_exc})")

    # ---- Body sanity check (holiday / no-data → server returns short body) ----
    body = response.content
    if len(body) < 100:
        raise ValueError(
            f"download_daily_bulletin: server returned short body ({len(body)} bytes); "
            f"likely holiday or invalid query (mode={mode}, date={date}, year={year})"
        )
    # Annual mode magic-bytes guard: TAIFEX 對未滿一年 / 無資料的 his_year 會回
    # ~600 byte HTML 錯誤頁，body > 100 過上面 gate 但 zipfile.ZipFile 開不了.
    # 必須在寫 cache 前 raise，否則下次 cache hit 會用 corrupt ZIP.
    if mode == "annual" and not body.startswith(b"PK\x03\x04"):
        raise ValueError(
            f"download_daily_bulletin: server returned non-ZIP body for year={year} "
            f"({len(body)} bytes; first4={body[:4]!r}); likely no data for this year. "
            f"Try daily mode if year is in-progress."
        )

    # ---- Atomic write: tmp → rename ----
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_bytes(body)
    tmp_path.replace(cache_path)
    return str(cache_path.resolve())


def parse_bulletin(csv_path: str) -> pd.DataFrame:
    """Parse a CP950-encoded TAIFEX option CSV → raw DataFrame.

    **Includes ALL contracts** (TXO/CAO/CBO/.../TEO/TFO; TXF 期貨不在 opt CSV)
    AND BOTH 週月選. TXO filter is to_strategy_view's job (Day 2).

    Schema versioning: Auto-detect pre/post 2025-12-08 by `契約到期日` presence.

    Steps (R10.8 P2-A + R10.10 / Codex R10.13 守):
      1. Width validation: peek raw header vs data line lengths (logs mismatch)
      2. read_csv(encoding='cp950', na_values=['-'], index_col=False)
         (R10.8 P2-A: index_col=False 強制不推斷 index → 否則 silent column shift)
      3. Drop trailing 'Unnamed:' col (post-20251208 trailing-comma 副產物)
      4. Detect schema version by `契約到期日` col presence
      5. Validate Chinese header: set equality with PRE/POST_20251208 frozenset
      6. Rename columns 中→英 via RAW_COLUMN_RENAME
      7. Normalise enum values: 買權→call, 賣權→put, 一般→regular, 盤後→after_hours
      8. Parse types: 交易日期 → datetime; strike → float; contract_date → datetime YYYYMMDD
      9. Return DataFrame with 20/21 normalised English cols.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"parse_bulletin: csv_path does not exist: {csv_path}")

    # Step 1: width validation (R10.8 P2-A defense, Codex 抓 silent shift)
    with path.open("rb") as f:
        header_line = f.readline().decode("cp950").rstrip("\r\n")
        data_line = f.readline().decode("cp950").rstrip("\r\n")
    header_n = len(header_line.split(","))
    data_n = len(data_line.split(",")) if data_line else header_n

    # Step 2: read_csv with index_col=False (critical guard, R10.8 P2-A)
    df = pd.read_csv(
        csv_path,
        encoding="cp950",
        na_values=["-"],
        index_col=False,  # 不推斷 index — 防 header/data width mismatch silent shift
    )

    # Step 3: Drop trailing 'Unnamed:' cols (post-20251208 trailing comma)
    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed:")]
    if unnamed_cols:
        df = df.drop(columns=unnamed_cols)

    # Step 4: schema version auto-detect (3-way: OLDEST 18 / PRE 20 / POST 21)
    actual_cols_set = {str(c).strip() for c in df.columns}
    has_contract_date = "契約到期日" in actual_cols_set
    has_change = "漲跌價" in actual_cols_set
    if has_contract_date:
        expected = RAW_TAIFEX_COLUMNS_CHINESE_POST_20251208
        version = "post-2025-12-08"
    elif has_change:
        expected = RAW_TAIFEX_COLUMNS_CHINESE_PRE_20251208
        version = "pre-2025-12-08"
    else:
        expected = RAW_TAIFEX_COLUMNS_CHINESE_OLDEST
        version = "oldest"

    # Step 5: validate Chinese header (R10.8 P2-#5 set comparison NOT count)
    missing = expected - actual_cols_set
    extra = actual_cols_set - expected
    if missing or extra:
        raise ValueError(
            f"parse_bulletin: schema drift ({version}) at {csv_path}. "
            f"missing={sorted(missing)}, extra={sorted(extra)}, "
            f"header_n={header_n}, data_n={data_n}"
        )

    # Step 6: rename 中→英
    rename_map = {c: RAW_COLUMN_RENAME[str(c).strip()] for c in df.columns}
    df = df.rename(columns=rename_map)

    # Step 7: normalise enum values (中→英)
    df["option_type"] = df["option_type_zh"].map(VALUE_NORMALIZATION["option_type_zh_to_en"])
    df["trading_session"] = df["trading_session_zh"].map(
        VALUE_NORMALIZATION["trading_session_zh_to_en"]
    )
    df = df.drop(columns=["option_type_zh", "trading_session_zh"])

    # Step 8: parse types
    df["date"] = pd.to_datetime(df["date"], format="%Y/%m/%d")
    # strike 個股選擇權含小數 (e.g. 55.0000) → float64
    df["strike"] = df["strike"].astype(float)
    # contract / contract_month_week 在某些年份 (e.g. 2018) 是 mixed int+str
    # → pyarrow.parquet 寫入 raise. 強制 string (not-null cols, 安全).
    df["contract"] = df["contract"].astype(str)
    df["contract_month_week"] = df["contract_month_week"].astype(str)
    if has_contract_date:
        # 真資料實證 (2025-12-08 換版後 ZIP)：contract_date 在 CSV 內可能是
        # float-with-NaN (e.g. 20251217.0) — 直接 .astype(str) 會留 ".0" 尾巴
        # → strptime '%Y%m%d' raise. 修法: 先 to_numeric coerce → Int64
        # nullable → string，最後 to_datetime 帶 errors='coerce' 容 NaN.
        cd_int = pd.to_numeric(df["contract_date"], errors="coerce").astype("Int64")
        df["contract_date"] = pd.to_datetime(
            cd_int.astype("string"), format="%Y%m%d", errors="coerce"
        )

    return df


def to_strategy_view(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Project raw 20/21-col TAIFEX DataFrame → STRATEGY_VIEW (10 cols).

    TXO regular session only; monthly options only (週選 dropped per Phase 1
    scope; 週選 expiry rule 留 Phase 2). Codex P2-#3 fix: parse_bulletin
    保留全 contracts，filter 在這一層.

    Steps:
      1. Filter contract == 'TXO'
      2. Filter trading_session == 'regular' (R10.8 P2-B 必加;
         同 strike 一般+盤後 兩 row 會破 dedup invariant)
      3. Filter monthly options only (contract_month_week 為 6-digit YYYYMM;
         週選 YYYYMMWn drop — Phase 1 scope, 週選結算規則 Phase 2)
      4. Derive expiry:
         - post-2025-12-08 (has contract_date): use parsed contract_date
         - pre-2025-12-08: holidays 第三個週三 + 順延 (R10.8 P2-C 方案 a)
      5. Drop strike NaN rows
      6. Project to STRATEGY_VIEW_COLUMN_ORDER (固定順序; frozenset 無序)
      7. Validate output: all 10 cols present, no extras

    Returns: 10-col DataFrame ordered per STRATEGY_VIEW_COLUMN_ORDER.
    """
    if raw_df.empty:
        raise ValueError("to_strategy_view: input raw_df is empty")

    df = raw_df.copy()

    # Step 1: TXO filter
    df = df[df["contract"] == "TXO"]
    if df.empty:
        raise ValueError(
            "to_strategy_view: no TXO rows after contract filter "
            "(input may not contain TXO contracts)"
        )

    # Step 2: regular session filter (R10.8 P2-B)
    df = df[df["trading_session"] == "regular"]
    if df.empty:
        raise ValueError("to_strategy_view: no rows after trading_session=='regular' filter")

    # Step 3: monthly only (週選 drop per Phase 1)
    cmw = df["contract_month_week"].astype(str).str.strip()
    is_monthly = cmw.str.fullmatch(r"\d{6}")  # YYYYMM only, exclude YYYYMMWn
    df = df[is_monthly].copy()
    if df.empty:
        raise ValueError(
            "to_strategy_view: no monthly TXO rows after week-option filter "
            "(Phase 1 scope = monthly only; 週選 future Phase 2)"
        )
    df["contract_month_week"] = cmw[is_monthly]

    # Step 4: derive expiry
    has_contract_date_col = "contract_date" in df.columns
    if has_contract_date_col:
        # Post-2025-12-08: parse_bulletin 已 datetime, just rename to canonical 'expiry'
        df["expiry"] = df["contract_date"]
    else:
        # Pre-2025-12-08: holidays 月選結算 (R10.8 P2-C decision a)
        df["expiry"] = df["contract_month_week"].apply(
            lambda s: _txo_monthly_settlement_date(int(s[:4]), int(s[4:]))
        )

    # Step 5: drop strike NaN (illiquid raw rows where TAIFEX wrote '-')
    df = df.dropna(subset=["strike"])
    if df.empty:
        raise ValueError("to_strategy_view: no rows after dropping NaN strike")

    # Step 6: project to STRATEGY_VIEW_COLUMN_ORDER
    missing = set(STRATEGY_VIEW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"to_strategy_view: missing required cols after derivation: {sorted(missing)}"
        )
    out = df[STRATEGY_VIEW_COLUMN_ORDER].copy().reset_index(drop=True)

    # Step 7: validate output (set equality, R10.10 P2-#5 三維度 gate)
    actual_cols_set = set(out.columns)
    if actual_cols_set != set(STRATEGY_VIEW_COLUMNS):
        raise ValueError(
            f"to_strategy_view: output schema drift. "
            f"expected={sorted(STRATEGY_VIEW_COLUMNS)}, got={sorted(actual_cols_set)}"
        )
    return out


def backfill_range(
    start_date: str,
    end_date: str,
    cache_dir: str,
    *,
    mode: Literal["annual"] = "annual",
    skip_cached: bool = True,
    sleep_between_sec: float = 2.0,
    progress_callback: Callable[[str, str], None] | None = None,
) -> pd.DataFrame:
    """Annual-mode batch backfill: year ZIP → monthly CSV → two-layer cache.

    For each year overlapping [start_date, end_date]:
      1. download_daily_bulletin(mode='annual', year=...) — cache hit O(1) if
         already downloaded.
      2. Extract every .csv from the ZIP into a TemporaryDirectory.
      3. parse_bulletin per monthly CSV (auto pre/post 2025-12-08 schema).
      4. to_strategy_view on the monthly raw_df once (silently empty df if no
         TXO regular monthly rows in this monthly file — e.g. weekly-only batch).
      5. groupby('date') → for each trading date in window:
         - skip if skip_cached=True and raw shard already exists (idempotent)
         - save_chain(raw_slice, layer='raw')
         - save_chain(sv_slice, layer='strategy_view') if non-empty
      6. sleep_between_sec between successive year downloads.

    Args:
        start_date / end_date: ISO 'YYYY-MM-DD' inclusive.
        cache_dir: cache root.
        mode: only 'annual' supported (Day 3 scope; daily mode reserved Phase 2).
        skip_cached: O(1) is_cached(raw) gate to skip work if shard exists.
        sleep_between_sec: pause between annual ZIP downloads (TAIFEX courtesy).
        progress_callback: optional fn(date_str, status) — status ∈
            {'saved', 'skipped_cached'}; for CLI / notebook progress.

    Returns: summary DataFrame, one row per processed date with cols
        [date, year, raw_saved, sv_saved, n_raw_rows, n_sv_rows].

    Raises:
        ValueError: invalid mode / start > end / TAIFEX schema drift mid-batch.
    """
    if mode != "annual":
        raise ValueError(
            f"backfill_range: only mode='annual' supported (Day 3 scope); got {mode!r}"
        )
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    if start_ts > end_ts:
        raise ValueError(f"backfill_range: start_date > end_date ({start_date} > {end_date})")

    summary_rows: list[dict] = []
    years = list(range(start_ts.year, end_ts.year + 1))

    # R11.5 P1 修法 (Codex 抓 backfill_range 仍用 tempfile.TemporaryDirectory
    # 落系統 %TEMP% → 4 fail in test_backfill_range_*; tmp_path fixture 解的是
    # test 端，主程式這條路徑是另一個獨立 vector):
    # 改用 cache_dir 內可控目錄 + shutil.rmtree(ignore_errors=True) cleanup.
    extract_base = Path(cache_dir) / "_extract_tmp"
    extract_base.mkdir(parents=True, exist_ok=True)

    for i, year in enumerate(years):
        zip_path = download_daily_bulletin(mode="annual", year=year, cache_dir=cache_dir)
        extract_root = extract_base / f"{year}_{uuid.uuid4().hex[:8]}"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                csv_names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
                zf.extractall(extract_root, members=csv_names)
            for csv_name in csv_names:
                csv_path = extract_root / csv_name
                raw_df = parse_bulletin(str(csv_path))
                # to_strategy_view raises on empty / no-TXO / no-monthly batches;
                # tolerate so backfill keeps progressing through edge-case files.
                try:
                    sv_df = to_strategy_view(raw_df)
                except ValueError:
                    sv_df = pd.DataFrame()
                for date_raw_key, date_raw in raw_df.groupby("date"):
                    date_ts = cast(pd.Timestamp, date_raw_key)
                    if not (start_ts <= date_ts <= end_ts):
                        continue
                    date_str = date_ts.strftime("%Y-%m-%d")
                    if skip_cached and is_cached(cache_dir, date_str, layer="raw"):
                        if progress_callback:
                            progress_callback(date_str, "skipped_cached")
                        continue
                    raw_path = save_chain(date_raw, cache_dir, date_str, layer="raw")
                    _append_manifest_row(
                        cache_dir,
                        date_str=date_str,
                        year=year,
                        layer="raw",
                        n_rows=len(date_raw),
                        n_cols=len(date_raw.columns),
                        size_kb=Path(raw_path).stat().st_size / 1024,
                    )
                    sv_slice = sv_df[sv_df["date"] == date_ts] if not sv_df.empty else sv_df
                    sv_saved = False
                    n_sv_rows = 0
                    if not sv_slice.empty:
                        sv_path = save_chain(sv_slice, cache_dir, date_str, layer="strategy_view")
                        _append_manifest_row(
                            cache_dir,
                            date_str=date_str,
                            year=year,
                            layer="strategy_view",
                            n_rows=len(sv_slice),
                            n_cols=len(sv_slice.columns),
                            size_kb=Path(sv_path).stat().st_size / 1024,
                        )
                        sv_saved = True
                        n_sv_rows = len(sv_slice)
                    summary_rows.append(
                        {
                            "date": date_str,
                            "year": year,
                            "raw_saved": True,
                            "sv_saved": sv_saved,
                            "n_raw_rows": len(date_raw),
                            "n_sv_rows": n_sv_rows,
                        }
                    )
                    if progress_callback:
                        progress_callback(date_str, "saved")
        finally:
            shutil.rmtree(extract_root, ignore_errors=True)
        if i < len(years) - 1 and sleep_between_sec > 0:
            time.sleep(sleep_between_sec)
    # 收尾把 _extract_tmp/ 整個刪 (ignore_errors 防 AV 鎖)
    shutil.rmtree(extract_base, ignore_errors=True)
    return pd.DataFrame(summary_rows)


# ---------------------------------------------------------------------------
# Backfill manifest (R11.2 Codex P5 修法)
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = "_backfill_manifest.csv"
_MANIFEST_HEADER = "date,year,layer,n_rows,n_cols,size_kb,written_at\n"
_MANIFEST_HEADER_COLS = ["date", "year", "layer", "n_rows", "n_cols", "size_kb", "written_at"]


def _validate_manifest_schema(path: Path) -> None:
    """R11.3 P2 修法：append 前驗 manifest header 對齊 7-col schema，否則 raise.

    防 R11.2 ad-hoc 寫的舊 6-col header (.../no written_at) 被 7-col row append
    汙染 → silent CSV corruption. 用 rebuild_manifest_from_cache() 重建.
    """
    if not path.exists():
        return  # 還沒寫，append 時自動寫新 header
    with path.open(encoding="utf-8") as f:
        first_line = f.readline().strip()
    actual_cols = first_line.split(",")
    if actual_cols != _MANIFEST_HEADER_COLS:
        raise ValueError(
            f"manifest schema mismatch: header={actual_cols} (n={len(actual_cols)}), "
            f"expected={_MANIFEST_HEADER_COLS} (n={len(_MANIFEST_HEADER_COLS)}). "
            f"Run rebuild_manifest_from_cache('{path.parent}') 重建 7-col manifest."
        )


def _append_manifest_row(
    cache_dir: str,
    *,
    date_str: str,
    year: int,
    layer: str,
    n_rows: int,
    n_cols: int,
    size_kb: float,
) -> None:
    """Append one shard row to <cache_dir>/_backfill_manifest.csv (Codex R11.2 P5).

    Replaces R11.1 ad-hoc snapshot script with backfill_range-integrated audit
    ledger. R11.3 P2 修法：append 前驗 schema 對齊 7-col；舊檔 schema 不對 raise
    要求先 rebuild_manifest_from_cache.
    """
    path = Path(cache_dir) / _MANIFEST_FILENAME
    _validate_manifest_schema(path)
    new_file = not path.exists()
    written_at = pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8") as f:
        if new_file:
            f.write(_MANIFEST_HEADER)
        f.write(f"{date_str},{year},{layer},{n_rows},{n_cols},{size_kb:.2f},{written_at}\n")


def rebuild_manifest_from_cache(cache_dir: str) -> int:
    """R11.3 P2 修法：一次性 rebuild manifest 對應 cache 內所有現有 shard.

    用 file mtime 為 written_at (歷史 shard 的真實寫入時間). Overwrites 舊
    manifest. backfill_range 之後 append 走相同 7-col schema.

    Returns: number of manifest rows written.
    """
    cache_root = Path(cache_dir)
    rows = []
    for layer in ("raw", "strategy_view"):
        layer_dir = cache_root / layer
        if not layer_dir.exists():
            continue
        for year_dir in sorted(layer_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for shard in sorted(year_dir.glob("*.parquet")):
                stat = shard.stat()
                # parquet 讀 metadata 取 row count + col count
                import pyarrow.parquet as pq

                md = pq.read_metadata(shard)
                mtime = pd.Timestamp(stat.st_mtime, unit="s", tz="UTC").strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                rows.append(
                    f"{shard.stem},{year_dir.name},{layer},{md.num_rows},"
                    f"{md.num_columns},{stat.st_size / 1024:.2f},{mtime}\n"
                )
    path = cache_root / _MANIFEST_FILENAME
    with path.open("w", encoding="utf-8") as f:
        f.write(_MANIFEST_HEADER)
        f.writelines(rows)
    return len(rows)
