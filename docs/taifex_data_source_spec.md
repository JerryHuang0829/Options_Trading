# TAIFEX Data Source Spec — Phase 1 Week 3 Pre-1

> **目的**：把 TAIFEX option 資料的 endpoint / 編碼 / schema / contract list / expiry 規則寫死成正式規格，避免 Day 1 起的 loader 實作憑記憶猜（v1 / v2 多次踩雷的 root cause）。
>
> **實證日期**：2026-04-27（curl POST 抓 13.5 MB annual ZIP + 305 KB daily CSV，CP950 解碼確認）
>
> **主要對應檔**：
> - `src/data/taifex_loader.py`（Day 1-3 實作 4 stub 函式按本 spec）
> - `src/data/schema.py`（Pre-1 連動新增；schema contract frozenset）
> - `tests/data/fixtures/taifex_2024_01_02_pre_20251208_sample.csv`（pre 2025-12-08 ground-truth fixture）
> - `tests/data/fixtures/taifex_2025_12_15_post_20251208_TXO_sample.csv`（post 2025-12-08 ground-truth fixture）

---

## 1. Endpoints（curl POST 實證）

TAIFEX 公開資料 download 入口在 `https://www.taifex.com.tw/cht/3/optDailyMarketView`（GET HTML form）。Form submit 時 JS 把 action 改成 `/optDataDown`，發 POST 拿檔。

### 1.1 Daily mode（單日 CSV）

```
POST https://www.taifex.com.tw/cht/3/optDataDown
Headers:
  Referer: https://www.taifex.com.tw/cht/3/optDailyMarketView
  User-Agent: Mozilla/5.0 (任一通用 UA 即可，無此 header server 不一定 reject 但建議帶)
Body (form-urlencoded):
  down_type=1
  queryStartDate=YYYY/MM/DD     # 注意斜線 + 4-2-2 補零；不接受 ISO YYYY-MM-DD
  queryEndDate=YYYY/MM/DD       # 同上；end - start ≤ 1 month（server 端限制）
  commodity_id=TXO              # 商品代號；TXO/CAO/CBO/.../TEO/TFO；不能多商品同時
  commodity_id2=                # 個股選擇權第二層 select 用，TXO 留空
Response:
  Content-Type: text/html;charset=MS950（檔案實為 CSV，content-type 標 html 是 server bug）
  Content (CP950 encoded CSV)
```

**實證**：2025-12-15 TXO daily CSV → HTTP 200, 305 KB, 31 lines（含 header + 30 data rows in fixture）。

### 1.2 Annual mode（整年 ZIP）

```
POST https://www.taifex.com.tw/cht/3/optDataDown
Headers: 同上
Body:
  down_type=2
  his_year=YYYY                 # 2001 - 最近完整年份（2025 跨年後才會有）
Response:
  Content-Type: application/octet-stream;charset=UTF-8
  Content (ZIP, 內含 12 個月份 CSV: YYYY_opt_MM.csv)
```

**實證**：2024 annual ZIP → HTTP 200, **13.5 MB ZIP / 132 MB 解壓 / 12 個月份 CSV / 各 ~10 MB / 各 ~136K rows**。

### 1.3 7yr backfill (2018-2025) 主路徑：Annual mode

> **2026-04-28 user pivot 5yr → 7yr**：原計畫 5yr (2021-2025)；現擴 7yr (2018-2025) for sensitivity（含 COVID 2020 stress test）。Main backtest 仍 5yr，7yr 對比 robustness。

| 模式 | 5yr 請求數 | 總下載量 | 限流風險 |
|------|----------|---------|---------|
| daily-loop | ~1240 req | ~370 MB | 高（每 req sleep 2s 估 41 min；server 可能 rate-limit） |
| **annual** | **5 req** | **~65 MB** | **低（每 req 數秒）** |

**結論**：5yr backfill **必用 annual mode**；daily mode 留給「最新 N 天增量更新」場景。

### 1.4 跨模式 invariant
- 兩 mode 同一 endpoint URL（`/optDataDown`），靠 `down_type` 區分
- 兩 mode response 格式不同（CSV vs ZIP），下游 parser 必先根據 `Content-Type` 或 magic bytes 判別

---

## 2. File Format

### 2.1 Encoding：**CP950**（不是 Big5）

實測 `iconv -f cp950 -t utf-8` 解碼成功；表頭中文完整。CP950 是 Big5 的 Microsoft 擴充版本（Big5 + Microsoft 自定字符），TAIFEX 用此。

**python**：`pd.read_csv(path, encoding='cp950', na_values=['-'])`

`encoding='big5'` 大部分情況可用但**遇罕見字會 UnicodeDecodeError**（CP950 superset 含某些 Big5 不在的字符），務必用 `cp950`。

### 2.2 Line endings：**CRLF**

Windows-style `\r\n`。pandas read_csv 自動處理。

### 2.3 Sentinel value：`-` = NaN

無交易 / illiquid row 的數值欄填 `-`（dash 字符）。`pd.read_csv(na_values=['-'])` 自動轉 NaN。

### 2.4 Trailing comma + width mismatch（**Codex R10.7 F2 抓到的 silent shift bug**）

兩種 trailing comma 場景，**兩種都需 `index_col=False` 否則 pandas silent column shift**：

**Case A: Post 2025-12-08 daily POST 來源 — header > data 1 col mismatch（不 silent shift 但留 NaN）**

實證 2025-12-15 daily CSV：

```bash
$ awk -F',' '{print NF}' tests/data/fixtures/taifex_2025_12_15_post_20251208_TXO_sample.csv | sort | uniq -c
     30 21
      1 22
```

**header 22 col / data 21 col**（R10.8 P2-A 修正：v3 spec 寫「兩種都 22」是錯）。Header 末尾的 trailing comma 多算 1 col；data row 沒 trailing comma 所以 21 col。

```python
$ pd.read_csv(fixture2, encoding='cp950', na_values=['-'])
shape: (30, 22)  # header 多的 1 col 變 'Unnamed: 21'，全 NaN
first row col 1: '2025/12/15'  # 正確（不 silent shift，因 header > data 而非反向）
```

**不會像 Case B silent column shift**（pandas 看 header 比 data 多 → 把 header 多的當空欄而非把 data 第一欄當 index）。但 parse_bulletin 仍需 drop 'Unnamed: 21' 才符合 21 col 預期。

**Case B: Pre 2025-12-08 header/data WIDTH MISMATCH（annual ZIP monthly CSV 來源）— DANGEROUS**

實證 fixture 1 (2024_opt_01.csv 抽出)：
```
header line: 交易日期,契約,...,漲跌% (20 cols, 無 trailing comma)
data row 1:  2024/01/02,TXO,202401W1,...,-, (21 cols, 有 trailing comma)
```

**header 20 / data 21 不一致 → pandas 預設 silent shift index**：
```python
$ pd.read_csv(fixture1, encoding='cp950', na_values=['-'])
shape: (20, 20)
first row col 1: 'TXO'  # ❌ silent shift! 把 '2024/01/02' 當 index
columns last 3: ['交易時段', '漲跌價', '漲跌%']  # 看似正確但全部資料左移一欄
```

**用 `index_col=False` 才安全**：
```python
$ pd.read_csv(fixture1, encoding='cp950', na_values=['-'], index_col=False)
shape: (20, 20)
first row col 1: '2024/01/02'  # ✅ 正確
```

**parse_bulletin 必做**：
1. 先讀 raw header 跟 data row 1 計 col 數 → 不一致 log warning
2. 永遠用 `index_col=False`（不依賴 pandas 自動推斷）
3. 若有 trailing 'Unnamed: NN' col → drop

**測試守護**：`test_parse_bulletin_handles_trailing_empty_column_without_index_shift`

### 2.5 thousands separator：**無**

數值欄無千分位 `,` 分隔（fixtures 內所有數值都是純數字 + 小數點）。但 spec 保留 cleaning 邏輯（plan v3 §4 Day 1 步驟 6）以防 TAIFEX 後續加 — defensive parsing。

---

## 3. Schema

### 3.1 Pre 2025-12-08（20 cols 中文，2001 - 2025-12-07）

```
交易日期 | 契約 | 到期月份(週別) | 履約價 | 買賣權
開盤價 | 最高價 | 最低價 | 收盤價 | 成交量
結算價 | 未沖銷契約數 | 最後最佳買價 | 最後最佳賣價
歷史最高價 | 歷史最低價 | 是否因訊息面暫停交易
交易時段 | 漲跌價 | 漲跌%
```

### 3.2 Post 2025-12-08（21 cols 中文，含 `契約到期日`）

上 + `契約到期日`（2025-12-08 起新增；TAIFEX 公告：「資料增加『契約到期日』欄位，為契約上市時預定的最後交易日」）。

### 3.3 中→英 normalised mapping（src/data/schema.py:RAW_COLUMN_RENAME）

| 中文 | 英文 | 備註 |
|------|------|------|
| 交易日期 | date | YYYY/MM/DD string → datetime |
| 契約 | contract | TXO / CAO / CBO / ... / TEO / TFO |
| 到期月份(週別) | contract_month_week | YYYYMM 月選 / YYYYMMWn 週選 |
| 履約價 | strike | float64（個股選擇權含小數，如 55.0000） |
| 買賣權 | option_type_zh | '買權' / '賣權'（normalize → 'call' / 'put'）|
| 開盤價 | open | float64 |
| 最高價 | high | float64 |
| 最低價 | low | float64 |
| 收盤價 | close | float64（注意 != settle）|
| 成交量 | volume | int64 |
| 結算價 | settle | float64（TAIFEX 計算 fair value，無交易日也有值）|
| 未沖銷契約數 | open_interest | int64 |
| 最後最佳買價 | bid | float64 |
| 最後最佳賣價 | ask | float64 |
| 歷史最高價 | historical_high | float64 |
| 歷史最低價 | historical_low | float64 |
| 是否因訊息面暫停交易 | halt_flag | string（一般 row 為空字串）|
| 交易時段 | trading_session_zh | '一般' / '盤後'（normalize → 'regular' / 'after_hours'）|
| 漲跌價 | change | float64 |
| 漲跌% | change_pct | float64 |
| 契約到期日 | contract_date | string YYYYMMDD（注意：**不是 ISO**，post 2025-12-08 only）|

### 3.4 重要 schema quirks（必標 caveat）

1. **`close` ≠ `settle`**：TXO 結算價是 TAIFEX 計算的 fair value，無交易日也有值；收盤價是最後成交價，無交易日為 NaN。**Strategy 端用 `settle`**（v3 plan ENGINE_REQUIRED_COLUMNS = `settle`），`close` 留 ENRICHED_OPTIONAL 供 audit。

2. **同 strike 同 type 有兩 row**：trading_session = '一般' + '盤後' 各一 row（fixture 2 實證）。`STRATEGY_VIEW_COLUMNS` 不含 trading_session 但 raw 必有 → **Day 2 `to_strategy_view` 必先 filter trading_session='regular' 才 dedup**。

3. **`contract_date` 格式 = YYYYMMDD string，不是 ISO**：例 `20251217` 而非 `2025-12-17`。`pd.to_datetime(format='%Y%m%d')` 才對。

4. **`strike` 是 float64 不是 int64**：個股選擇權 strike 含小數（如 55.0000）。Synthetic chain `strike = int` 跟真資料不一致 — 紅線：不動 synthetic.py，TAIFEX schema 改 `strike: float64`。

5. **`halt_flag` 一般 row 是空字串 `""`，不是 NaN**：pandas 預設 empty cell → NaN，但此欄 server 可能填空字串。dtype `string` + nullability=True 處理。

### 3.5 Contracts（opt ZIP 內含商品種類）

`opt_*.csv` **只含選擇權商品**：
- **TXO**：台指選擇權（主要 target；月選 + 週選）
- **個股選擇權**：CAO / CBO / CCO / CDA / CDO / CEO / CFO / CGO / CHO / CJO / ...（每股票一商品代號）
- **TEO**：電子類股選擇權（ETF）
- **TFO**：金融類股選擇權（ETF）
- **CAO**（同上，個股例如華碩）
- 其他類股 ETF 選擇權

**TXF / TXM / 其他期貨不在 opt CSV**！TXF 是台指期貨在 `fut_*.csv`（另外的 endpoint / ZIP）。Plan v3 描述「TXO/TXF/電子/金融」混檔是**錯**——opt ZIP 內無 TXF。

### 3.6 Schema versioning

| 版本 | 起訖 | 欄位數 | 主要差異 |
|------|------|--------|---------|
| **v1 pre 2025-12-08** | 2001-01-01 ~ 2025-12-07 | 20 | 無 contract_date；expiry 必從 contract_month_week 字串推導 |
| **v2 post 2025-12-08** | 2025-12-08 起 | 21 | 多 contract_date YYYYMMDD；expiry 直接讀此欄 |

**Detection**：parse 時看 header 是否含「契約到期日」決定版本。

---

## 4. Expiry 推導規則

### 4.1 Post 2025-12-08（簡單）
直接讀 `contract_date` 欄：`pd.to_datetime(s, format='%Y%m%d')`。

### 4.2 Pre 2025-12-08（複雜，需 TXO 結算規則）

從 `contract_month_week` 字串解析：

- **`YYYYMM` 月選**：到期日 = 該月第三個週三（TXO 月選結算日）
  - Python 算法：`pd.Timestamp(year=YYYY, month=MM, day=1) + pd.offsets.WeekOfMonth(week=2, weekday=2)`
- **`YYYYMMWn` 週選**：到期日 = 該月第 N 個週三
  - 例 `202401W1` = 2024-01 第 1 週週三 = 2024-01-03（若該月 1 號是週一→週三是 3 號）
  - Python 算法需手動：`first_wed_of_month + (n-1) * 7 days`

**邊界 case**（必測試）：
- 第三個週三遇假日 → TXO 結算規則順延到下一交易日（需 `holidays` 套件或硬寫台灣交易日）
- W5 週選某些月份不存在（5 週的月份才有）
- 週三剛好是除夕 / 春節 → 特殊規則

**簡化建議（Phase 1）**：用 `pd.offsets.WeekOfMonth(week=2, weekday=2)` 算第三個週三，不處理假日順延（accept 1-2% expiry 偏差作為 Phase 1 妥協）；Phase 2 補完整 holidays 表。

---

## 5. Schema Drift Policy

`src/data/schema.py` 提供三維度 frozenset gate（plan v3 §2 + Day 2 `_validate_schema`）：

```python
RAW_TAIFEX_COLUMNS_PRE_20251208: frozenset[str]    # 20 normalized en cols
RAW_TAIFEX_COLUMNS_POST_20251208: frozenset[str]   # 21 normalized en cols
STRATEGY_VIEW_COLUMNS: frozenset[str]              # 10 cols (TXO only, regular session)
ENGINE_REQUIRED_COLUMNS: frozenset[str]            # 12 cols (含 iv/delta/mark_source)
COLUMN_DTYPES: dict[str, str]                      # 各 col 預期 dtype
COLUMN_NULLABILITY: dict[str, bool]                # 哪些 col 不可 NaN
```

**Hard raise on**：
- missing col / extra col（set comparison NOT count）
- dtype mismatch
- nullability violation

**已知 acceptable drift**：trailing comma 空欄（drop 後對齊預期 frozenset）。

---

## 6. Fixtures

| 路徑 | 來源 | 內容 |
|------|------|------|
| `tests/data/fixtures/taifex_2024_01_02_pre_20251208_sample.csv` | annual ZIP `2024.zip > 2024_opt_01.csv` 抽 2024-01-02 day | 21 lines = 1 header + 15 TXO + 5 CAO；20 cols；CP950；含週選 `202401W1` |
| `tests/data/fixtures/taifex_2025_12_15_post_20251208_TXO_sample.csv` | daily POST `commodity_id=TXO` for 2025-12-15 | 31 lines = 1 header + 30 TXO；22 cols（21 + trailing comma 空欄）；CP950；含「一般」+「盤後」兩 trading_session |

**fixture 用途分配**：

| Test target | Fixture 用 |
|-------------|-----------|
| `parse_bulletin` 解 CP950 / 不過濾 contract / pre 20-col schema | fixture 1 |
| `parse_bulletin` 解 post 21-col schema / contract_date 格式 / trading_session 兩值 | fixture 2 |
| `to_strategy_view` TXO filter（過 CAO / 個股）+ regular session filter | fixture 1 + 2 |
| `to_strategy_view` schema versioning pre/post detect | fixture 1 vs 2 |

---

## 7. Limitations / Caveats

1. **2025 annual ZIP 還沒發**：TAIFEX annual ZIP 通常**跨年後**才包完整年（2024.zip 在 2025-01-02 發布）；2025.zip 預計 2026-01 才有。Pre-1 fixture 2 用 daily POST 補 post-2025-12-08 sample。
2. **Daily mode `commodity_id` 一次只能傳一個**：要多商品需分次 POST。Annual mode 自動含全部。
3. **Rate limit 經驗未實測上限**：plan v3 設 sleep_between_sec=2.0 保守值；user 自測上限後可調。
4. **Schema 後續再變的可能性**：TAIFEX 已改過一次（2025-12-08），未來可能再改。`_validate_schema` hard raise 確保 day 0 知道。
5. **Contract date 格式 YYYYMMDD 是字串不是 ISO**：跟其他 ISO date 欄位（plan 內 `expiry`）格式不同 — Day 1 parse 時要 `pd.to_datetime(format='%Y%m%d')` 顯式指定。
6. **「一般」+「盤後」同 row dedup 風險**：`STRATEGY_VIEW_COLUMNS` 不含 trading_session，必先 filter `trading_session='regular'` 才 project；否則 (date, expiry, strike, option_type) 不 unique。
7. **`close` vs `settle` 分用**：strategy 端用 `settle`（fair value 永遠有）；`close` 留 audit。
8. **TXF 不在 opt ZIP**：plan v3 描述「TXO/TXF」是錯，opt ZIP 只含選擇權商品（無期貨）。

---

## 8. Reference URLs

- TAIFEX option daily report 入口（HTML form）：https://www.taifex.com.tw/cht/3/optDailyMarketView
- TAIFEX 2025-12-08 schema change 公告（節錄於 form 頁面註4）：「自2025年12月8日一般交易時段起，資料增加『契約到期日』欄位，為契約上市時預定的最後交易日。」
- 月選擇權結算規則（TXO 月選結算）：每月第三個週三（official TAIFEX rule book，**TODO: 補官方 URL**）
- 週選擇權結算規則：每月第 N 個週三（**TODO: 補官方 URL**）
- 假日順延規則：若結算日為假日順延至下一交易日（**TODO: 補官方 URL**）

---

## 9. 變更紀錄

- **2026-04-27**：Pre-1 初版；endpoint + schema + 5 quirks + 2 fixtures 全 curl POST 實證（CP950 / 2024 annual ZIP 13.5 MB / 2025-12-15 daily 305 KB）。
