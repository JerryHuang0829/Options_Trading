# Options_Trading

台灣期交所 TAIFEX TXO（台指選擇權）系統化策略研究框架，以 Iron Condor（鐵兀鷹）與 Vertical Spread（垂直價差）為主，透過 5 年真實市場資料的 walk-forward（滾動前進）回測進行驗證。

[![CI](https://github.com/JerryHuang0829/Options_Trading/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryHuang0829/Options_Trading/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-465%20passed-brightgreen)
![Type Check](https://img.shields.io/badge/mypy-passing-brightgreen)
![Lint](https://img.shields.io/badge/ruff-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

## TL;DR（30 秒版）

- **這是什麼**：一個個人量化研究專案 —— 用機構等級的方法論，嚴格檢驗「在台灣指數選擇權（TXO）、零售 NT$ 100 萬規模下，賣方溢價策略（Iron Condor / Vertical Spread）能不能在 5 年真實樣本外穩定賺錢？」
- **怎麼做**：自寫 BSM-Merton 定價核心（對 `py_vollib` 對齊到 1e-8）→ 8 年 TAIFEX 真期權鏈資料管線 → SVI/SABR vol surface 補乾淨 mark-to-market → 6 scenario × 15 個不重疊季度 OOS 的 walk-forward 回測 → Pro 統計判定（Bootstrap CI / sign-flip permutation / Deflated Sharpe）。每個結論都經 external review chain 反覆攻擊。
- **結論**：6 scenario 在 5 年 OOS 上 **Sharpe 全 < 0**；把零售成本整個關掉重跑，Sharpe `|Δ| ≤ 0.016` —— 不是手續費壓死的，是策略本身在這段 regime 沒有 edge。誠實記 **NO-GO**，不降標、不反向湊參數。同一套工程基礎設施延續到 Phase 2（換策略類別、重跑 fresh OOS，仍朝小額 paper → live）。
- **為什麼值得看**：嚴謹驗證下的負面結果 + 「敢殺自己假設」的研究紀律，比鬆散方法論下的漂亮 PASS 更有科學價值。**想看互動版** → `streamlit run dashboard/專案背景.py`（4 頁圖表：定價核心 / Walk-forward 結果 / Audit 紀律）。

> **只有 5 分鐘？** 讀這段 TL;DR → 跑 `streamlit run dashboard/專案背景.py` 看主頁與 Page 1 的 py_vollib 交叉驗證 → 翻 [`src/options/pricing.py`](src/options/pricing.py)（數學引擎）與 [`src/backtest/walk_forward.py`](src/backtest/walk_forward.py)（OOS 設計）。下方「結果與結論」是完整數字，「Key Design Decisions」是每個技術選擇的理由。

## 核心能力

- **自寫 Black-Scholes-Merton（BSM-Merton）定價核心**（含連續股利率 `q`）+ 5 個 Greeks（Δ Γ Θ ν ρ）+ Newton-Raphson IV solver（隱含波動率求解器）+ Brent fallback。對 50 random sample 用 `py_vollib.black_scholes_merton` 交叉驗證，價格差距 < 1e-8；4 種單位換算規則對齊（vega per 1.0 vs per 1%、theta per-day-365、rho per 1%）。

- **8 年 TAIFEX TXO 資料管線**（2018-04 → 2026-04，1963 個交易日）— Big5 / CP950 編碼、3 種 schema 自動偵測（OLDEST 18 欄 / PRE 20 欄 / POST 21 欄）、parquet 雙層 cache（raw + strategy_view）、ZIP magic-bytes 守衛、annual 與 daily 兩種下載模式。

- **Volatility Surface 波動率曲面 fit** — SVI 5 參數 + SABR 4 參數 + 多項式 fallback；Gatheral & Jacquier (2014) arb-free 與 Lee (2004) bound 檢查；5 年回測窗口 1227 shard 100% 日期覆蓋。用 model price fallback 解決 60% bid/ask 缺值問題，得到乾淨的 mark-to-market（每日定價）。

- **Walk-forward 回測引擎** — 252 日 train（訓練）/ 63 日 disjoint quarterly OOS（不重疊季度樣本外）folds。6 scenario =（Iron Condor + Vertical）×（vanilla（純策略）+ IV percentile gate（IV 百分位 gate）+ HMM 2-state regime gate（2 狀態 HMM 機制 gate））。嚴格 daily loop（每日迴圈）+ point-in-time（PIT，當下時點）正確性 — strategy factory 只看到 train 期 returns。

- **Pro 量化統計工具鏈** — Bootstrap percentile CI（百分位信賴區間）、sign-flip permutation test（符號翻轉排列檢定，Politis & Romano 2010）、Deflated Sharpe Ratio（去膨脹 Sharpe，López-de-Prado 2014）、Calmar ratio。Retail（散戶）成本模型（手續費 NT$12 + 期交稅 10 bps + 滑價 15 bps）+ worst-side fill（最差側成交，賣方 fill at bid、買方 fill at ask）。

## 結果與結論（Phase 1）

5 年（2021-04 → 2026-04，1227 個交易日）真實 walk-forward 回測，6 scenario × 15 個 disjoint quarterly OOS folds，**全部 aggregate Sharpe < 0**：

| Scenario | Agg Sharpe | Max DD | Trades / Folds |
|---|---|---|---|
| Iron Condor (vanilla) | −2.70 | −2.8% | 5 / 15 |
| Iron Condor (IV percentile gate) | −2.68 | −2.4% | 4 / 15 |
| Iron Condor (HMM 2-state regime gate) | 0.0 | — | **0** / 15（regime gate 幾乎全程 close） |
| Vertical (vanilla) | −2.15 | −6.9% | 12 / 15 |
| Vertical (IV percentile gate) | −2.12 | −5.4% | 10 / 15 |
| Vertical (HMM 2-state regime gate) | −2.86 | −1.0% | 1 / 15 |

**Phase 1 出口條件「OOS Sharpe > 1」FAIL** → Iron Condor / Vertical short-premium 假設在 5 年 OOS 上證偽，不啟動 paper trading。

兩個刻意做的紀律約束，避免把「失敗」洗成「成功」：

- **排除顯而易見的藉口**：把零售成本模型整個關掉（`--no-cost-model`）重跑，各 scenario Sharpe `|Δ| ≤ 0.016` —— 不是手續費 / 期交稅 / 滑價壓死的，是策略本身在這段 regime（含 2022 熊市與 2025 關稅震盪，short premium 易爆損）就沒有 edge。
- **不 data-snoop**：明知 5 年全 negative 後，不反向 sweep `short_delta` / `wing_delta` / DTE 去湊正 Sharpe —— 那是 lookback bias，不是研究。

> 假設被證偽是有效的研究產出，不是「待修的 bug」。同一套工程基礎設施 —— 自寫定價核心、8 年資料管線、vol surface、walk-forward + Pro 統計工具鏈、4 件 hard gate、external review chain —— 都可重用：Phase 2 換策略類別（calendar spread / long-premium overlay / 或 cross-asset factor model），重跑 fresh OOS，目標仍是驗證通過後走向小額 paper → live。

## 快速開始

```bash
git clone https://github.com/JerryHuang0829/Options_Trading.git
cd Options_Trading

# 建 conda 環境（Python 3.12, conda-forge channel）
conda create -c conda-forge --override-channels -n options python=3.12 -y
conda activate options
pip install -r requirements.txt

# 跑一次完整 regression 確認環境就緒
pytest tests/ -q                    # 預期：465 passed, 2 skipped, 約 3 分鐘

# （Optional）啟動 dashboard portfolio showcase
streamlit run dashboard/專案背景.py  # → http://localhost:8502，4 個 page
```

## 關鍵設計決策（Key Design Decisions）

每個技術選擇背後都有可驗證的理由，不是預設。

### 為何 BSM-Merton 不是純 BSM
TAIEX 是 price index（不是 total return），成分股配息會在除權日造成 schedule drop。純 BSM（`q=0`）會系統性偏置 ATM delta 約 `q·T·S`，並破壞 Put-Call Parity。Merton form 加入連續股利率 `q` 後，PCP 殘差降到 < 1e-10。
→ 見 [`src/options/pricing.py`](src/options/pricing.py) 的 module docstring。

### 為何預設 `WorstSideFillModel` 不是 mid
散戶實際下市價單時，賣方成交在 bid、買方成交在 ask；用 mid 系統性高估賣方收入約半個 spread × multiplier。對 short premium 策略尤其 critical — 整段 backtest 可能因此假性贏錢。`engine.run_backtest` 預設 `WorstSideFillModel()`，要其他模型必須顯式指定。
→ 見 [`src/backtest/engine.py`](src/backtest/engine.py) line 251 + [`src/backtest/execution.py`](src/backtest/execution.py) docstring。

### 為何 sign-flip permutation 不是 random shuffle
Sharpe = mean / std × √N 在 random shuffle 下完全不變（mean 與 std 都是排列不變量），原始 shuffle 的 p-value 沒意義。Sign-flip permutation（Politis & Romano 2010）對每筆 PnL 獨立翻轉 ±1，在 H0「對稱零飄移」假設下保留邊際分布同時讓 mean / Sharpe 真正變動。
→ 見 [`src/backtest/stats.py::permutation_test`](src/backtest/stats.py) docstring。

### 為何 walk-forward `step_days >= test_window_days`
若 step < test_window，相鄰 fold 的 OOS 窗會重疊，concat daily PnL 會在同一日重複計算，導致 aggregate Sharpe / max drawdown / Calmar 全部 inflate。`WalkForwardConfig.__post_init__` 強制 raise — 是 critical correctness gate。
→ 見 [`src/backtest/walk_forward.py`](src/backtest/walk_forward.py) line 85。

### 為何 max_drawdown 必須對 cumulative PnL 而非 daily PnL
`metrics.max_drawdown` contract 明定輸入為 cumulative PnL；對 daily PnL 跑 `cummax` 沒有經濟意義（會把單日最大盈拿來當 peak），會系統性低估 max DD 數倍。本 repo 在 2026-05-05 抓到並修復 `walk_forward._aggregate_folds` 將 daily PnL 直接傳入的 silent bug，修正後加 2 條 regression test 防止再犯。
→ 見 [`src/backtest/metrics.py::max_drawdown`](src/backtest/metrics.py) docstring + [`tests/backtest/test_walk_forward.py`](tests/backtest/test_walk_forward.py) `test_aggregate_max_drawdown_uses_cumulative`。

## Repository Tour（5 分鐘 onboarding）

如果你只有 5 分鐘讀懂本 repo，建議讀這 3 個檔：

| 檔案 | 為何先讀 |
|---|---|
| [`src/options/pricing.py`](src/options/pricing.py) | 數學引擎入口 — 自寫 BSM-Merton + IV solver；docstring 闡述 Merton form 與 PCP；對 py_vollib 交叉驗證的 contract |
| [`src/backtest/walk_forward.py`](src/backtest/walk_forward.py) | 回測 OOS 設計 — `WalkForwardConfig` 的 disjoint OOS 不變式（line 85）+ `_aggregate_folds` 的 PIT 正確性 + max_drawdown bug 修法位置（line 371）|
| [`tests/options/test_pricing.py`](tests/options/test_pricing.py) | 驗證紀律入口 — `test_bsm_matches_py_vollib` 50 random sample cross-validation；展示「自寫核心 + 業界 reference 對齊」的標準做法 |

如果還有 5 分鐘，加讀：
- [`src/backtest/stats.py`](src/backtest/stats.py) — Bootstrap CI / sign-flip permutation / Deflated Sharpe / Calmar 的引文與實作
- [`src/backtest/engine.py`](src/backtest/engine.py) — 主 daily loop 與 `cum_pnl = realised + unrealised` 不變式

或者啟動 dashboard 直接看視覺化：

```bash
streamlit run dashboard/專案背景.py    # http://localhost:8502
```

4 個 page：
- **專案背景** — Hero metric / 路線圖 / 5 個核心能力 expander
- **Page 1 定價核心** — BSM-Merton 公式 / 50-sample py_vollib 交叉驗證 / Strategy payoff diagram / Greeks 互動 slider / SVI Vol Surface 3D（1227 個 fit date 可選）
- **Page 2 Walk-forward 結果** — 6 scenario 摘要表 / cumulative PnL curves / Bootstrap CI / Permutation null distribution / Retail cost ablation / Walk-forward fold timeline
- **Page 3 Audit 紀律與 Bug 修法** — 14+1 輪 external review chain timeline / 4 件 hard gate / `agg_max_drawdown` silent bug deep dive / Pro methodology 4 badges

## 專案結構

```
src/
├── options/         # 定價核心 — BSM-Merton + Greeks + IV solver,
│                    #   chain helper, SVI/SABR vol surface, regime gate
├── strategies/      # 策略實作 — IC / Vertical / calendar hedge
├── backtest/        # 回測引擎 — walk-forward / portfolio / MtM /
│                    #   FillModel / metrics / Pro 統計工具
├── risk/            # 風控 gate — 4 條 hard limit + stop-loss
├── data/            # TAIFEX loader / schema 解析 / parquet cache / enrich
└── common/          # 凍結 domain 型別（OptionQuote / Order 等）

tests/               # 鏡像 src/ 結構，465 tests
scripts/             # CLI 入口（驗證管線）
dashboard/           # Streamlit + Plotly portfolio showcase（4 page）
config/              # 常數
notebooks/           # 探索性分析（gitignored）
data/taifex_cache/   # 本機 parquet cache（gitignored — 用 loader 重建）
```

## 各模組重點

**`src/options/`** — 定價核心：BSM-Merton 封閉解（`pricing.py`）、5 Greeks Merton form（`greeks.py`）、option chain 篩選 helper（`chain.py`）、SVI / SABR / 多項式 3-tier vol surface fit + arb-free 守衛（`vol_surface.py` + `surface_batch.py` + `surface_cache.py`）、IV percentile 與 2-state HMM regime gate（`regime_gate.py`）。

**`src/strategies/`** — 策略實作：4 腳 Iron Condor 含 3 平倉觸發 + single-roll 調整（`iron_condor.py`）、bull put / bear call vertical spread（`vertical.py`）、calendar spread overlay（`calendar_hedge.py`）、`RegimeWrappedStrategy` 將純策略與 regime gate 組合（`gate_wrap.py`）。

**`src/backtest/`** — 引擎與驗證：daily loop + PIT 正確性（`engine.py`）、`mark_to_market(mark_policy=...)` 三模式（`portfolio.py`）、4 種 FillModel + RetailCostModel（`execution.py`）、disjoint quarterly OOS folds + strategy-factory 注入（`walk_forward.py`）、Pro 統計四件組（`stats.py`）、Sharpe + max drawdown（`metrics.py`）。

**`src/data/`** — TAIFEX 資料管線：下載 + Big5 解碼 + 3 向 schema 偵測 + ZIP magic-bytes guard（`taifex_loader.py`）、標準化 schema 驗證（`schema.py`）、雙層 parquet cache（`cache.py`）、per-strike IV / delta 計算 + execution gate（`enrich.py`）。

## 技術棧

| 套件 | 版本 |
|---|---|
| Python | 3.12 |
| numpy | 2.4 |
| pandas | 3.0 |
| scipy | 1.17 |
| py_vollib（交叉驗證 reference）| 1.0.1 |
| pytest | 9.0 |
| ruff（linter + formatter）| 0.15.11 |
| mypy + pandas-stubs（靜態型別）| 1.20 / 3.0 |
| pyarrow（parquet I/O）| 24.0 |
| holidays（台灣假日）| 0.50 |

## 驗證與可重現性

每個 commit 必須通過 4 件 hard gate：

```bash
ruff check src tests config scripts          # Lint — All checks passed
ruff format --check src tests config scripts # Format — 97 files unchanged
mypy src tests config scripts                # 型別檢查 — no issues, 98 source files
pytest tests/ -q                             # 465 passed, 2 skipped, 約 190 秒
```

加上文件漂移審計（防止過時 baseline 數字 / 絕對宣稱誤導）：

```bash
python scripts/audit_doc_drift.py            # PASS, 0 drift
```

End-to-end smoke pipeline（端對端煙霧測試）：

```bash
python scripts/_dummy_backtest_pipeline_check.py
```

完整 5 年 walk-forward 回測（含 retail 成本，約 20 分鐘）：

```bash
python scripts/_validate_week6_5yr.py
```

## References

本 repo 的數學與統計實作引用以下文獻；對應的引文錨定在各 module docstring：

**Option pricing**
- Merton, R. C. (1973). *Theory of Rational Option Pricing*. Bell Journal of Economics and Management Science 4(1).

**Volatility surface**
- Gatheral, J., & Jacquier, A. (2014). *Arbitrage-free SVI volatility surfaces*. Quantitative Finance 14(1).
- Lee, R. W. (2004). *The moment formula for implied volatility at extreme strikes*. Mathematical Finance 14(3).
- Hagan, P. S., Kumar, D., Lesniewski, A. S., & Woodward, D. E. (2002). *Managing Smile Risk*. Wilmott Magazine.

**Backtest statistics**
- Sharpe, W. F. (1994). *The Sharpe Ratio*. Journal of Portfolio Management 21(1).
- Lo, A. W. (2002). *The Statistics of Sharpe Ratios*. Financial Analysts Journal 58(4).
- Mertens, E. (2002). *Variance of the IID Estimator in Lo (2002)*.
- López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*. Journal of Portfolio Management 40(5).
- Politis, D. N., & Romano, J. P. (2010). *K-sample subsampling in general spaces: The case of independent time series*. Journal of Multivariate Analysis 101(2).
- Phipson, B., & Smyth, G. K. (2010). *Permutation P-values should never be zero: Calculating exact P-values when permutations are randomly drawn*. Statistical Applications in Genetics and Molecular Biology 9(1).

## License

[MIT License](LICENSE) — 自由 fork / 修改 / 商用，保留 copyright notice 即可。

## 免責聲明

本 repo 為研究框架。回測結果僅供研究與教育用途，過去績效不保證未來結果，不構成任何投資建議。
