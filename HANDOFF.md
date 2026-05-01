# HANDOFF.md — Options_Trading

**Last updated**: 2026-04-29（Week 5 + R11.15-R11.17 修法：P5 _OpenOnceHoldIC plumbing e2e 真觸發；**R11.17 Codex 反駁 finding (c)**：surface_fallback 並非「永遠不被觸發」— 持倉期 quote 變 missing 仍會用 fallback；只是 5.4b A/B/C/D scenario 沒打到。Week 6+ 真 backtest 5yr/7yr 含 illiquid 月份才能真驗 functional 價值；plumbing e2e ≠ real-strategy proof。Self-audit 升級 19 條 (R11.17 加 P17(e) plumbing-vs-real-strategy + P18 absolute claim stress-test + doc drift sweep)）
**Product Phase**: Phase 1 Week 1 ✅ + Week 2 ✅ + Week 3 ✅ + Week 4 ✅ + **Week 5 全完工 ✅ (R11.15-R11.18 累積 11 件 P 全修完)** → **下一步：Week 6+ 真 backtest 5yr/7yr (GO-WITH-CAVEATS — 含 6 項 monitor metric 真驗 surface fallback functional 價值)**
**最後一次 Codex audit**: R11.21 **Week 6+ true backtest GO** (連 11 輪 audit 後首次 GO)；R11.21 P1 docstring count drift (12 vs 13) 已修；audit_doc_drift.py PASS / 13 unit tests PASS / hard gate 全綠；**注意**: GO 是「開始 5yr/7yr 真回測驗證」的 GO，不是 live trading / strategy edge GO — Week 6 報告必列 6 項 monitor metric 真驗 surface fallback functional 價值
<!-- ================ TEST BASELINE — SINGLE SOURCE OF TRUTH ================
     2026-04-29: R11.x baseline 同步連 5 輪重犯 (R11.5/R11.7/R11.8/R11.9 +
     R11.9 audit 過程第 5 次) → self-audit Pattern 13 觸發 architectural fix。
     baseline **只在「Test baseline」這一行**維護；其他段落寫
     「見 §Test baseline」即可，不引用絕對 line 號 (R11.11 P3 fix —
     line 號本身會 drift；reference 用 anchor 名而非行號才是真結構性解)。
     ====================================================================== -->
**Test baseline**: 447 passed, 2 skipped (~205 sec on Claude env; R12.13 完工 — Codex R12.12 抓 5 件 P/caveat: (P1 prompt error) Claude 說 import-only 應 add stderr handler 但實測 import 後 root.handlers=[] (P2 embedded) main() embedded 呼叫 in PowerShell cp950 仍 UnicodeDecodeError (re-exec gate 不觸發) (P3) R12.11 skip-if-missing 舊 test 還在 + R12.12 contract sim only 不夠 strict (caveat 1) scenarios.csv 沒 cost_model_disabled 欄 (caveat 2) phase1_conclusion.md 寫 stale 445/R12.11 數字. R12.13 fix: (P1+P2) main() 入口加 `_ensure_utf8_runtime_for_embedded_caller` 印警告告 embedded caller 必 CLI / PYTHONUTF8 startup (P3) 移除 R12.11 skip test, 換 grep contract test 直接 verify production code line 存在 (caveat 1) `_scenario_aggregate_row_with_cost_flag` 加 cost_model_disabled per-row col (caveat 2) phase1_conclusion.md 更新 R12.13/449+. Skill R12.13 升 P18(g)「prompt 自宣稱 evidence 對齊真實 code path」 + P14(h)「embedded library use-case UTF-8 必 cover」 + P17(n)「audit trail 三 surface 同步 (CSV/JSON/doc)」 + P18(h)「changelog 數字 stale 自查」)

**⚠️ 環境限制（已記錄，非 code bug）**：本機 user 環境跑著 **Trend Micro Apex One**（公司資安 EDR，4 個 service：Data Protection / Unauthorized Change Prevention / Application Control + Defender 已被接管）。Apex One real-time scan 在 pytest 寫 tmp 檔時會鎖檔 → Codex 環境曾出 21 個 PermissionError WinError 5 fail。`Add-MpPreference` 對 Apex One 無效（那是 Windows Defender 命令）；本機 user GUI 排除清單通常被 IT policy 鎖住。**唯一根本解**：請 IT / 資安團隊在 Apex One server / Apex Central web console 加掃描排除路徑 `tests/_tmp/`、`.pytest_cache/`。**現況**：user 接受公司環境限制，未來換家機觀察是否解。Setup helper [scripts/setup_windows_defender_exclusions.ps1](scripts/setup_windows_defender_exclusions.ps1) 為 Defender 環境（家機 / GitHub Actions）預備。Code 端已盡力（repo-internal tmp + ignore_errors cleanup），剩下是 OS-layer 限制。

---

## 🚀 新 Session 五分鐘上手

### 1) 環境啟動 + 完整 baseline
```bash
conda activate options
cd e:/Data/chongweihuang/Desktop/project/Options_Trading

ruff check . && ruff format --check .
mypy src tests config scripts
pytest tests/ -q                                           # 見 §Test baseline — 數字 single source of truth
python scripts/_dummy_backtest_pipeline_check.py           # D-soft 通管路 OK
```

### 2) 進入 session 必讀（依序）
1. **本檔 §3 Week 4-5 Vol Surface 起手** — 你今天要做什麼
2. **本檔 §4 不可破壞的 invariant** — Codex R1-R10 + R10.5-R10.13 連續 audit 修出來的紀律
3. **`feedback_silent_bugs.md` memory** — 7 條原 silent bug pattern（修 code 前必讀）
4. `CLAUDE.md` — repo 守則 / SOP / 文件慣例
5. `docs/roadmap.md` — Phase 1 / Phase 2 完整路線圖（Week 4-5 vol surface 已標 D-soft 提前）

### 3) 真 cache 已就緒（不要重抓）
```
data/taifex_cache/
├── raw_zip/                   # 9 個 annual ZIP + 2 small CSV (152 MB)
├── raw/                       # 1963 shards (149 MB)，2018-04-02 → 2026-04-28
└── strategy_view/             # 1963 shards (37 MB)，同範圍
```

---

## 1. 已完工進度（不要重做）

### Week 1（2026-04-25 完工）— Options 數學核心 (R1-R5)
- [src/options/pricing.py](src/options/pricing.py)：BSM-Merton (含 q) + Newton-Raphson + Brent fallback IV solver + R5 noise floor `max(1e-4, 1e-7×S)`
- [src/options/greeks.py](src/options/greeks.py)：5 Greeks Merton form (per 1.0 內部慣例 / py_vollib 4 規則單位換算)
- [src/options/chain.py](src/options/chain.py)：filter_by_dte / select_by_delta (R7 NaN guard) / pivot_to_chain
- [src/data/synthetic.py](src/data/synthetic.py)：fixed monthly 3rd-Wed expiry calendar (R5)；25-col enriched DataFrame
- [docs/bsm_derivation.md](docs/bsm_derivation.md)：BSM 推導 + R1-R3 教訓錨定

### Week 2（2026-04-26 完工 + R1-R10）— Strategy + Backtest + Risk
- [src/common/types.py](src/common/types.py)：5 frozen dataclass（OptionQuote / Order / StrategySignal / PortfolioState / RiskConfig）+ `initial_capital` 顯式 (R5)
- [src/strategies/iron_condor.py](src/strategies/iron_condor.py) / [vertical.py](src/strategies/vertical.py)：IC 4-leg open + 3 credit metrics + 3 close trigger + single-roll adjust
- [src/risk/limits.py](src/risk/limits.py)：4 hard gate `check_risk` + R8 TWD basis `trigger_stop_loss`
- [src/backtest/portfolio.py](src/backtest/portfolio.py)：`_mid_price` strict / `_mid_price_with_basis` / `mark_to_market(mark_policy=...)` (R10.10/R10.11/R10.12)；`aggregate_greeks` strict
- [src/backtest/execution.py](src/backtest/execution.py)：4 FillModel + `_assert_executable` 4 處 side-specific NaN guard (R10.10 2c+3ii)
- [src/backtest/engine.py](src/backtest/engine.py)：daily loop + `run_backtest(mark_policy=)` + 3 forward + `mark_audit` DataFrame (R10.12 a)；`cum_pnl = realised + unrealised`
- [src/backtest/metrics.py](src/backtest/metrics.py)：`sharpe_ratio(initial_capital=)` (R10 F1)；`max_drawdown` 含 0 peak (R9)
- [scripts/smoke_test.py](scripts/smoke_test.py) / [scripts/stress_test.py](scripts/stress_test.py)：端對端 demo + 4 stress scenario

### Week 3（2026-04-28 完工，D-soft pivot + R10.5→R10.13）

| Day | 主題 | 主檔 |
|-----|------|------|
| Pre-1 | spec 文檔 + 2 fixtures + .gitignore | docs/taifex_data_source_spec.md / tests/data/fixtures/ |
| Pre-2 | TAIEX ^TWII 8yr fetch (q PIT audit-only) | scripts/fetch_taiex.py / data/taiex_daily.csv (160 KB, 2017 row) |
| Day 1 | schema 三向度 (OLDEST 18 / PRE 20 / POST 21) + parse_bulletin 9 步 + download_daily_bulletin POST 雙模式 | src/data/schema.py / src/data/taifex_loader.py |
| Day 2 | to_strategy_view 7 步 (TXO + regular + monthly only + holidays expiry) | src/data/taifex_loader.py |
| Day 3 | parquet 兩層 cache (year-folder split) + backfill_range annual | src/data/cache.py / backfill_range |
| Day 4 | enrich Phase 1 (q PIT audit-only via PCP + add_underlying + add_dte) | src/data/enrich.py |
| Day 5 | per-strike IV (mid→settle fallback) + delta + can_buy/can_sell pure execution gate (R10.10 3ii) | src/data/enrich.py |
| Day 6 | D-soft dummy run 通管路 (NOT 5yr 真 backtest) — GatedIronCondor + engine.run_backtest mark_policy + mark_audit 整合測試 | scripts/_gated_strategy.py / scripts/_dummy_backtest_pipeline_check.py |
| Day 7 | 本檔 + Codex R11 prompt + roadmap 確認 | HANDOFF.md / Codex-Prompt.md |

### 累計 Tests（見 §Test baseline — single source of truth）

| 模組 | Tests |
|------|-------|
| tests/options/{pricing, greeks, chain} | 6 + 5 + 20 |
| tests/data/test_synthetic | 7 |
| tests/data/test_schema | 11 (Day 1A) |
| tests/data/test_taifex_loader | 30 (含 OLDEST 18-col + ZIP magic guard) |
| tests/data/test_cache | 13 (含 year-folder isolation) |
| tests/data/test_enrich | 30 (Day 4 + Day 5) |
| tests/common/test_types | 6 |
| tests/strategies/{iron_condor, vertical} | 14 + 5 |
| tests/risk/test_limits | 11 |
| tests/backtest/{execution, portfolio, metrics, engine} | 9+ + 11+ + 12 + 9+ (含 R10.10/R10.11/R10.12 共 ~25 新) |
| tests/stress/test_stress | 7 |
| tests/integration/test_dummy_pipeline_check | 5 (Day 6 D-soft) |

唯一 1 skip：[tests/backtest/test_engine.py:242](tests/backtest/test_engine.py#L242)，position 在 window end 仍 open 時 invariant 條件不適用。

### 真 TAIFEX 8yr cache（不要重抓）

```
raw/         1963 shards / 149 MB / 2018-04-02 → 2026-04-28
strategy_view/ 1963 shards / 37 MB
raw_zip/     9 annual ZIP + 2 daily CSV / 152 MB

by year: 2018=189 / 2019=242 / 2020=245 / 2021=244 / 2022=246 /
         2023=239 / 2024=242 / 2025=243 / 2026=73 (Q1 + Apr)
```

---

## 2. Codex Audit R1-R10 + R10.5-R10.13 累積修法（每條都不可回歸）

### R1-R10（Week 1+2 主修）

| Round | 修了什麼 | Pattern |
|-------|---------|------|
| R1 | BSM 加 q (Merton) / Python 3.12 / enriched schema | 領域死背 |
| R2-R3 | docstring q sync / theta sign 不硬斷 / day-count | 未實測 |
| R4 | sigma=0 forward intrinsic / synthetic group contract_date | 邊界 |
| R5 | synthetic fixed expiry calendar / Portfolio.close fill_model + 到期 payoff / IV solver noise floor | 多面向 |
| R6 | mark_to_market strict / Position.realised_pnl_accumulated / aggregate_greeks strict (sibling sweep) | 兩會計視角 |
| R7 | engine `cum_pnl = realised + unrealised`（不再 cash-initial 雙重計）+ open-day=0 | 兩視角 |
| R8 | `trigger_stop_loss` TWD basis / IC `.iloc[0]` empty guard | 邊界 |
| R9 | `max_drawdown` 含初始 0 peak | 邊界 |
| R10 | `sharpe_ratio(initial_capital=)` (TWD→returns 後再扣 rf) | 單位 |

### R10.5-R10.13（Week 3 D-soft pivot 主修）

| Round | 修了什麼 |
|-------|------|
| R10.5 | `_mid_price(strict=True)` + execution `_assert_executable` 4 FillModel side-specific |
| R10.7 | `parse_bulletin` `index_col=False`（防 silent column shift trap） |
| R10.8 | TAIFEX endpoint 從憑記憶 → curl POST 13.5 MB ZIP **實證**；holidays 套件 4 case 對齊 |
| R10.10 | `drop_unmarkable` 廢棄；`can_buy/can_sell` 純 execution gate (3ii) |
| R10.11 | `_mid_price_with_basis` + `mark_to_market(mark_policy='strict_mid'\|'mid_with_settle_fallback')` hybrid |
| R10.12 a/b/c | engine `run_backtest(mark_policy=)` + 3 forward + `mark_audit` DataFrame；settle finite guard；aggregate_greeks NaN raise |
| R10.13 | C1/C2/C3 patches；GO-WITH-CAVEATS B-/82 |

### Week 3 真資料 silent bug 4 連發（D-soft session 才暴露）

| # | 症狀 | 修法 |
|---|------|------|
| 1 | `parse_bulletin: schema drift, missing=['漲跌價', '漲跌%']`（2018 ZIP）| OLDEST 18-col schema + 3-way auto-detect |
| 2 | `pyarrow.lib.ArrowTypeError: Expected bytes, got int (contract_month_week)` | parse_bulletin Step 8 `astype(str)` |
| 3 | `unconverted data remains: ".0"` (contract_date `%Y%m%d`) | NaN-safe `to_numeric → Int64 → string → to_datetime(errors='coerce')` |
| 4 | `zipfile.BadZipFile` (annual_2026.zip 是 586-byte HTML) | `download_daily_bulletin` ZIP magic-bytes guard `PK\x03\x04` + delete corrupt + test |

**這 4 個 silent bug 全是「fixture 寫太小、unit test 沒覆蓋真資料邊界」典型** — Week 4-5 / Week 6+ 還會冒新 bug（Codex R11 重點檢視）。

---

## 3. Week 4-5 詳細規劃 — Vol Surface (SVI/SABR) 提前（D-soft pivot）

### Week 4-5 北星目標
建 vol surface 把 60% NaN bid/ask rows 用 model price 補滿 → 100% markable → Week 6+ 才能用乾淨 mark 跑真 backtest。

### Week 4 — SVI fit
| 步驟 | 內容 |
|------|------|
| 4.1 | 新建 [src/options/vol_surface.py](src/options/vol_surface.py)：SVI 5 param `(a, b, ρ, m, σ)` per (date, expiry) fit |
| 4.2 | OOS validation：每月 fit 用前 N 天 → out-of-sample R² 評估 |
| 4.3 | 錯誤處理：fit 不收斂 → 退回 SABR / polynomial smile (degree 2)；3 model fallback chain |
| 4.4 | 對 1963 days × 8-12 expiries × 30+ strikes 跑 batch fit；存 `data/taifex_cache/vol_surface/<YYYY-MM-DD>.parquet` |

### Week 5 — surface mark + enrich Phase 3
| 步驟 | 內容 |
|------|------|
| 5.1 | enrich.py 加 `add_model_price(df, surface_cache)` → 對 NaN bid/ask 行用 surface 反算 BSM price |
| 5.2 | portfolio.py 加 mark_policy `'mid_with_surface_fallback'`（取代 R10.11 settle fallback）|
| 5.3 | engine.run_backtest 接受新 mark_policy；mark_audit 多一欄 `n_fallback_surface` |
| 5.4 | 跑 1 年 sub-set 真 backtest 驗證 surface mark 結果合理性（vs settle fallback / vs strict） |

### Week 6+ — 真 backtest 5yr / 7yr sensitivity
- 用 vol surface mark 跑 2021-04 → 2026-04 主 backtest
- 7yr (2018-2026) sensitivity 對比 5yr → diff > 30% 標 regime-dependent caveat
- **R11.17 Codex 要求 monitor metric 必列入報告 (避免 hollow PASS / plumbing-vs-real-strategy 混淆)**:
  1. `n_fallback_surface_total` (整段 backtest 累計)
  2. fallback days count (有任一 leg 用 surface fallback 的天數)
  3. fallback legs / total marked legs ratio (per-day 分佈)
  4. GatedIC `rejected_reason` counts (open-side execution gate 過濾原因 — 知道為何 1-year 1 trade)
  5. 0-trade days count + skipped entry reasons (檢視 strategy 在 illiquid 真資料行為)
  6. A/B/C scenario PnL 是否真分歧 (B vs C 差距 > 0 才算 surface mark 有 functional 價值)

---

## 4. **不可破壞的 Invariants**（R1-R10 + R10.5-R10.13）

### Layer 1：Options 數學
- **Put-Call Parity (Merton)**：`|C - P - (S·e^(-qT) - K·e^(-rT))| < 1e-10`（synthetic）
- **Greeks vs py_vollib**：5 Greek + price 50 sample max diff < 1e-8（含 vega×0.01 / theta×365 / rho×0.01 單位換算）
- **bsm_price(sigma=0, T>0)** = forward intrinsic（R4 P1）
- **implied_vol** 對 price < `max(1e-4, 1e-7×S)` raise（R5 P2 noise floor）

### Layer 2：Synthetic / Real Chain
- **Synthetic 持倉期間 expiry 連續可 mark**：fixed monthly 3rd-Wed calendar 保證（R5 P1）
- **0 active expiry → ValueError**（R6 F3，不是 KeyError）
- **change/historical group key 含 contract_date**（R4 P2）
- **Real chain schema 三向度 set 接受**：OLDEST(18) / PRE(20) / POST(21)；其他 → drift raise
- **Real chain `contract / contract_month_week` astype(str)** 防 mixed-type pyarrow raise
- **Real chain `contract_date` NaN-safe parse**：`to_numeric → Int64 → string → to_datetime(errors='coerce')`
- **Real chain ZIP magic-bytes guard**：annual mode body 不以 `PK\x03\x04` 開頭 → raise，**不寫 cache**
- **Cache year-folder split**：`<cache_dir>/<layer>/<YYYY>/<YYYY-MM-DD>.parquet`

### Layer 3：Risk
- **`PortfolioState.initial_capital` 由 engine 顯式注入**（不可從 cash + realised 推；R5 P2）
- **`trigger_stop_loss` 用 TWD basis**：`-entry_credit_pts × multiple × TXO_MULTIPLIER`（R8 F1）
- **`check_risk` 4 條 short-circuit 順序**：max_loss → max_concurrent → max_capital_at_risk → portfolio_loss_cap

### Layer 4：Portfolio + Engine
- **`mark_policy='strict_mid'`**：缺 bid/ask 必 raise（R10.10 1A，預設）
- **`mark_policy='mid_with_settle_fallback'`**：mid 缺 → settle；audit 紀錄 `fallback_rate` per day（R10.11 hybrid 1+3）
- **engine.run_backtest 3 forward sites**：pre_open / eod / final 全傳 mark_policy（R10.12 a）
- **`Position.realised_pnl_accumulated` 累積 adjust legs**（R6 F2）
- **engine `cum_pnl = realised + unrealised`**（R7 F1）
- **open-day Mid fill → daily_pnl[0] = 0**（R7 invariant）
- **invariant：sum(daily_pnl) == realised + final_unrealised**
- **execution `_assert_executable` 4 FillModel 全 side-specific NaN guard**（R10.10 2c+3ii）
- **aggregate_greeks iv/delta NaN strict raise**（R10.12 c）
- **mark_audit DataFrame 三欄齊**：`fallback_rate`, `n_legs_marked`, `n_fallback_settle`（R10.12 a）

### Layer 5：Metrics
- **`max_drawdown` cummax 含初始 0 peak**：第一天虧也要算 DD（R9 P2）
- **`sharpe_ratio(twd_pnl, initial_capital=cap)`** 必傳 capital；engine 自動傳（R10 F1）

### Layer 6：D-soft 哲學
- **Pro 量化「先建 mark machinery 才 backtest」**：Week 3 不跑 5yr Sharpe；Week 4-5 vol surface；Week 6+ 真 backtest
- **`q_pit` audit-only**：tradable signal 用 fallback q=0.035；q_pit 不入 IV → signal 鏈
- **`enrich_pipeline` 輸出含 ENGINE_REQUIRED 13 col**：date/expiry/strike/option_type/settle/bid/ask/iv/delta/dte/underlying/can_buy/can_sell

### 寫 code 必先 Pre-flight Check
1. **修 code 前**：todo 第一項為「Pre-flight: 對 7 Pattern + 4 Week 3 silent bug pattern 自審」
2. **修 code 後**：對「反向信號表」逐行檢查；中任一紅旗 → 不可回報「修好」
3. **改文件測試數字前**：必先實際跑 pytest 拿真實數字（R10 起硬規則）
4. **每輪 Codex audit 後**：立刻寫對應 R\<N\> 補充段進 `feedback_silent_bugs.md`

---

## 5. 環境 / 工具鏈狀態

| 項目 | 值 |
|------|---|
| Python | 3.12.13 |
| Conda env | `options`（conda-forge channel） |
| 套件 | numpy 2.4 / pandas 3.0 / scipy 1.17 / py_vollib 1.0.1 / pytest 9.0 / pyarrow 24.0 / matplotlib 3.10 / ipykernel 7.2 / ruff 0.15.11 / mypy 1.20 / pandas-stubs 3.0 / holidays 0.50 / requests / tempfile / zipfile（內建）|
| Source files | ruff ✓ / mypy ✓（最後驗證 Day 6 完工時）|
| Tests | 見 §Test baseline — single source of truth |

Week 4-5 進場前可能要新裝：
- `quantlib-python` 或自寫 SVI fit（看 Codex R11 建議）
- `multiprocessing` / `joblib` 加速 surface fit（內建）

---

## 6. 已知遺留 / Phase 2 backlog

進 Week 4-5 不阻擋，但 Week 6+ / Phase 2 必補：

| 項目 | 何時需要 |
|------|---------|
| Vol surface 建模（SVI/SABR + fallback） | **Week 4-5（next）** |
| `add_iv_per_strike` / `add_delta_per_strike` 用 `df.itertuples()` Python loop（8M row 估 30 min-1 hr） | Week 6+ 真 backtest 前要 vectorize |
| `GatedIronCondor.should_close` / `should_adjust` close-side gate（持倉中遇 NaN bid/ask 仍 raise）| Week 6+ |
| `backfill_range` 不支援 daily mode 自動切換（2026 用了 inline script 補）| Week 4-5 整理 |
| `_solve_q_pit_one_day` ATM `min(abs(k - spot))` 沒 max_distance_pct gate（稀疏月份挑「假 ATM」）| Week 4-5 vol surface 完成後可廢 q PIT |
| Margin / collateral model | Phase 2 接 broker |
| Broker order retry / partial fill | Phase 2 |
| `synthetic.py` Python loop 慢（22s/31k row） | Phase 2 sensitivity sweep |
| `pricing.py::_vega` private duplicate | Phase 1 後期可抽 _internal.py |
| `scripts/*.py` sys.path bootstrap | Phase 2 加 `pip install -e .` 後可移除 |

---

## 7. 關鍵檔案指針

| 路徑 | 用途 |
|------|------|
| [CLAUDE.md](CLAUDE.md) §2 | SOP 三分級 + options layer + py_vollib 4 規則 |
| [docs/options_math_audit.md](docs/options_math_audit.md) | Layer 2 options-specific 4 項數學 reference (PCP / Greeks / py_vollib / no-arb) |
| [.claude/skills/](Options_Trading/.claude/skills/) | self-audit (12 條) / multi-perspective (7+1 personas) / forensic-sweep skill |
| [Codex-Prompt.md](Codex-Prompt.md) | **R11 Week 3 final audit prompt（覆蓋 Week 1+2+3 逐 Day）** |
| [docs/roadmap.md](docs/roadmap.md) | Phase 1 / Phase 2 完整 week-level 工作項目 |
| [docs/bsm_derivation.md](docs/bsm_derivation.md) | BSM-Merton 數學推導 |
| [docs/taifex_data_source_spec.md](docs/taifex_data_source_spec.md) | TAIFEX endpoint / schema / 7yr backfill 規格 |
| Plan 檔 | `C:\Users\chongweihuang\.claude\plans\week3-keen-babbage.md`（plan v6 D-soft）|
| Memory 檔 | `C:\Users\chongweihuang\.claude\projects\e--Data-chongweihuang-Desktop-project\memory\` |
| - `feedback_silent_bugs.md` | 7 原 Pattern + R6→R10 補充段（**修 code 前必讀**；R11 後待補 Week 3 silent bug pattern）|
| - `reference_taifex_data.md` | TAIFEX endpoint / Big5 / canonical schema |

---

## 8. 給新 session 的提醒

- 你接手時 framework 已穩（**baseline 見 §Test baseline + 8yr 1963 shards 真 cache + Week 4 Day 1-4 (SVI / SABR / poly+3-tier / batch+MP+cache) 完工 + self-audit Pattern 13 首次架構修法落地**），**請不要重寫 / 重構任何已通過的 module**
- Week 4-5 進場前**必先讓 Codex 跑 R11 audit**（[Codex-Prompt.md](Codex-Prompt.md)），收 P1/P2/P3 patch list 後再開 Week 4
- Week 4-5 vol surface 是新邏輯區塊，per CLAUDE.md §3 必先**寫完整 plan + 通過 user 拍板**才動 code
- 真資料還會帶出 silent bug（schema drift / stale quote / 假日 / wide spread / NaN delta）— 不要當意外，這正是 D-soft 的核心 thesis
- 進 Week 6+ 真 backtest 跑出第一份 5yr 結果後，**對比 synthetic Sharpe 1.22**，看真資料的真實 edge / 是否 settle fallback 污染
