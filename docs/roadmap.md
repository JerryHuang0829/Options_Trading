# Options_Trading 專案路線圖（Roadmap）

> 本檔說明 **Phase 1 / Phase 2 兩大 Phase 的 week-level 工作項目**，每項附簡短解釋。
>
> **目前進度（2026-05-02）**: **Phase 1 完工** — Week 1-6 全綠. 工程 tooling **GO**, strategy alpha **NO-GO** (5yr OOS Sharpe 全 -2.1 ~ -2.9 / HMM gate 0-1 trades / cost-free baseline 同樣 negative → strategy 真的沒 edge, 不是 retail friction 壓死). R12.0-R12.13 連 14 輪 Codex audit 全 close. 447 tests pass + 1227 surface_fits 5yr full coverage + Pro 統計工具鏈 (Bootstrap CI / sign-flip permutation / Deflated Sharpe).
>
> **Phase 1 結論文件**: [phase1_conclusion.md](phase1_conclusion.md) — alpha hypothesis 證偽 honest report.
>
> **Phase 2 方向**: 待 user 拍板. 三選一:
> - (A) **Stock factor model** — 換 asset class, 對齊「贏 0050」目標, factor model 業界共通
> - (B) **Long premium / vol arb** — 留 TXO 但換 strategy class (從賣方換買方 / overlay / calendar)
> - (C) **Honest reality** — passive ETF + 學 quant infra 純為 career

---

## 路線圖總覽

| Phase | 時間範圍 | 核心目標 | 出口條件 | 實際結果 |
|-------|---------|---------|---------|--------|
| **Phase 1** | 已完工 (~6 週) | IC + Vertical / TAIFEX 5yr walk-forward / 自寫 BSM-Merton | OOS Sharpe > 1、Codex audit 通過、paper trading 穩定 | tooling GO ✅ / strategy NO-GO ❌ (5yr -2.x Sharpe) |
| **Phase 2** | 待拍板 | (A) stock factor / (B) long vol-arb / (C) passive + infra | 視 Phase 2 子方向定 | — |

---

## Phase 1：研究框架 + 歷史回測（0–6 個月）

### Week 1 — Options 數學核心 ✅ 完工

| Day | 工作項目 | 簡單解釋 |
|-----|---------|---------|
| 1 | `src/options/pricing.py` | 自寫 **BSM-Merton 公式**（含股利 q）+ Newton-Raphson IV solver + Brent fallback；對 50 隨機 case 驗證 vs `py_vollib` 差距 < 1e-12 |
| 2 | `src/options/greeks.py` + `docs/bsm_derivation.md` | 5 Greek（Δ Γ Θ ν ρ）Merton form；**day-count 解決**（py_vollib theta per-day-365）；vega/rho 單位（per 1.0 內部 vs py_vollib per 1%）|
| 3 | `src/options/chain.py` | 期權鏈三 helper：`filter_by_dte` / `select_by_delta`（signed put delta）/ `pivot_to_chain`；定義 enriched schema（iv/delta/dte/underlying）|
| 4 | `src/data/synthetic.py` | 合成期權鏈（GBM spot + BSM 重定價）；25 欄對齊 TAIFEX；caller 預先填 iv/delta 避免 hot-path 反算 |
| 5 | `scripts/smoke_test.py` | End-to-end pipeline：synthetic → BSM → IC 4-leg 範例（22 秒）|
| 6 | HANDOFF + Codex-Prompt R4 任務書 | 產出 R4 audit prompt 供外部 LLM 跑 |
| 7 | 等 Codex R4 audit | R4 抓兩件 P1：sigma=0 forward intrinsic / synthetic group 漏 contract_date — 全修 |

**Week 1 累計**：32 tests pass / ruff + mypy 全綠 / smoke 22 秒跑通 IC

---

### Week 2 — Strategy + Backtest + Risk ✅ 完工

| Day | 工作項目 | 簡單解釋 |
|-----|---------|---------|
| 1 | `src/common/types.py` Domain Model 統一 | 5 frozen dataclass（OptionQuote / Order / StrategySignal / PortfolioState / RiskConfig）取代 dict 邊界；Strategy ABC dict→typed refactor |
| 2 | `IronCondor.open_position` + 3 credit metrics | IC 開倉：DTE band → 4 strike 抓（短/長 ±0.16/±0.08）→ `settle_credit / mid_credit / worst_credit` 三種報；max_defined_risk 計算 |
| 3 | `IronCondor.should_close / should_adjust` + Vertical builders | 平倉 3 觸發（DTE stop / 50% profit / stop-loss）；adjust 為 short-strike breach 時 single-roll 成 vertical |
| 4 | `src/risk/limits.py` Risk Layer | 4 hard gate（max_loss_per_trade / max_concurrent / max_capital_at_risk / portfolio_loss_cap）+ `trigger_stop_loss`；整合進 IC strategy |
| 5 | Backtest Engine 主 loop | `Portfolio` 4 method（open/close/MtM/aggregate_greeks）+ 4 FillModel concrete（Settle/Mid/WorstSide/Slippage）+ Sharpe/maxDD/winrate；engine 預設 **WorstSide**（GPT-5.5 + R4 共識）+ PIT 切片 |
| 6 | `scripts/stress_test.py` 4 scenarios | IV crush -30% / IV expand +50% / spot gap up +5%(IV+20%) / spot gap dn -7%(IV+30%)；對 IC 開倉日 chain 套 BSM 重定價 → mark-to-market |
| 7 | `chain.py` NaN/dup guard + Codex-Prompt R5 任務書 | `select_by_delta` 加 raise_on_nan/raise_on_duplicate 兩 kwarg；R5 prompt 含 Week 1+2 完整審計範圍 |

**Week 2 累計**：~100 tests pass（合計 132 pass + 5 skip 含 R6/R7/R8/R9/R10 修法）/ stress 4 scenarios 表格產出 / GPT-5.5 north-star 全達成

---

### Week 3 — TAIFEX 資料 pipeline (D-soft pivot) ✅ 完工 (2026-04-28)

> **2026-04-28 D-soft pivot**：Codex R10.12 實證 2024 全年 100% 天 fallback rate ≥20%（mean 59.55%）→ 原 Week 3 backtest 用 stale-mark Sharpe 不可發表。Pro 量化標準改為「先建 vol surface 才 backtest」。本週收斂為「資料 pipeline + dummy run 通管路」，**不跑 5yr 真實 backtest**。
>
> **完工成果**：8yr 1963-shard cache (2018-04-02 → 2026-04-28) / 257 tests pass / D-soft dummy 通管路 / 4 個真資料 silent bug 修法（OLDEST 18-col schema / mixed-type cast / contract_date NaN-safe / ZIP magic-bytes guard）。R11 cross-week audit 待跑。

| Day | 工作項目 | 簡單解釋 |
|-----|---------|---------|
| Pre-1 | `docs/taifex_data_source_spec.md` | 已實證 endpoint：POST `/cht/3/optDataDown`（annual `down_type=2&his_year=YYYY` ~13MB/yr ZIP；daily `down_type=1` ≤1mo range）；CP950 編碼；20 欄 pre-2025-12-08 / 21 欄 post |
| Pre-2 | `scripts/fetch_taiex.py` | 一次性抓 yfinance ^TWII 5yr → `data/taiex_daily.csv`；docstring 標 4 條 audit caveats（price index / settlement 時點 / q-only 用途 / Phase 2 升級 vendor data）|
| 1 | `src/data/taifex_loader.py` 下載 + Big5 解碼 | POST endpoint 雙模式（daily/annual）；CP950 解碼；保留所有 contracts（TXO/TXF/個股 etc.）+ 週月選；不過濾（filter 是 Day 2 的事） |
| 2 | TAIFEX → strategy_view (9-col) | TXO filter；週月選靠 `contract_date` 區分；schema drift 三維度 hard raise（cols/dtypes/nullability）；2025-12-08 pre/post fixture |
| 3 | `src/data/cache.py` parquet 層 | 兩層 cache（raw 20-col / strategy_view 9-col）schema_version 分開；annual mode 增量 backfill；atomic write |
| 4 | q PIT 解（**audited enrichment 不影響交易**）| put-call parity 反算 q_pit + audit flags；NEGATIVE q hard raise；missing spot raise 不 silent fallback；**Day 5 IV 反算預設用 fallback q=0.035 不用 q_pit** |
| 5 | per-strike IV + can_buy/can_sell (純 execution gate) | implied_vol 反算 + delta；R10.10 3ii side-specific (廢 v3 mark_source 二分 / v4 drop_unmarkable / v5 雙 Sharpe — 詳見 plan v6) |
| 6 | **dummy run 通管路** (D-soft 修正) | 餵合成資料證 GatedIronCondor + engine `mark_policy` + audit metric 接通；**不跑 5yr 真實 backtest**（推遲 Week 6+ vol surface 完成後）|
| 7 | HANDOFF + Codex R13 audit + roadmap pivot 確認 | D-soft 收斂；交棒給 Week 4-5 vol surface |

**目標 (D-soft 修正)**：資料 pipeline + 系統管路接通；**不**跑 5yr Sharpe（Pro 標準：vol surface 完成才跑）。

---

### Week 4-5 — Vol Surface (SVI/SABR) **D-soft 提前自原 Week 5-6**

> **D-soft pivot**：vol surface 是 Pro 量化「先 mark machinery 才 backtest」的核心。原 Week 5-6 提前到 Week 4-5；60% TAIFEX rows 缺 bid/ask 不能用 settle fallback hack 跑 backtest，必須先有 surface model price 才行。

| 工作項目 | 簡單解釋 |
|---------|---------|
| TAIFEX 真實 IV 資料 fit SVI/SABR | 從 5yr Day 5 enriched IV 資料 fit per-day vol surface (5-param SVI / 4-param SABR) |
| Skew / term structure 視覺化 | 台指 IV smile (put skew) + term structure 短中長 DTE 對比 |
| Vol surface model price 取代 settle fallback | enrich.py 加 `model_price` col：illiquid (bid/ask 缺) row 用 surface 反算 model price 取代 settle |
| Mark policy 升級：`'mid_with_surface_fallback'` | engine.run_backtest 新增 mark_policy enum value；fallback 用 model price 不用 settle |
| Vol surface fit 穩定性檢查 | 參數 day-to-day 跳動 / R² / 5yr OOS 預測準確度 |

**目標**：vol surface fit 完成後，60% missing rows 用 model price → 真乾淨 mark machinery → Week 6+ 才有資格跑可發表 Sharpe。

---

### Week 6 — 真 backtest 6 scenario × walk-forward ✅ 完工 + Phase 1 結論

| 工作項目 | 完工狀態 |
|---------|---------|
| **5yr 主 backtest (2021-04 → 2026-04, ~1227 days)** | ✅ 完工 (2026-05-01) — disjoint quarterly OOS 15 folds, with-cost + cost-free 兩版 |
| **6 scenario walk-forward** | ✅ IC + Vertical × vanilla / IV-percentile / HMM 2-state |
| Pro 統計工具鏈 | ✅ Bootstrap CI / sign-flip permutation (Politis & Romano 2010) / Deflated Sharpe (López-de-Prado 2014) / Calmar |
| Surface cache 5yr 補齊 | ✅ 1227 shards 100% date coverage (R12.3 P fix) |
| Retail cost model | ✅ commission NT$12 + 期交稅 10 bps + slippage 15 bps + worst-side fill |
| Codex external audit | ✅ R12.0-R12.13 連 14 輪 closed (47+ Phase 1 早期 P + 39 件 R12.x P 全 substantive 修法) |

**5yr 真實結果 (2026-05-01)**:

| Scenario | With-cost Sharpe | No-cost Sharpe | Trades / 15 folds |
|----------|------|------|---|
| IC_vanilla | -2.7047 | -2.7055 | 5 |
| IC_IV_percentile | -2.6803 | -2.6869 | 4 |
| IC_HMM | 0.0000 | 0.0000 | 0 |
| Vertical_vanilla | -2.1463 | -2.1303 | 12 |
| Vertical_IV_percentile | -2.1249 | -2.1101 | 10 |
| Vertical_HMM | -2.8599 | -2.8670 | 1 |

→ **|Δ Sharpe| ≤ 0.016** (retail 摩擦不是 root cause). **strategy alpha 證偽**.

**Phase 1 出口條件 vs 實測**:
- OOS Sharpe > 1 — ❌ FAIL (全 negative)
- Bootstrap 95% CI 不跨零 — ⚠️ 部分 (IC 跨零, Vertical 顯著 negative)
- Codex audit 通過 — ✅ tooling GO
- Paper trading 穩定 — ❌ N/A (Sharpe fail 不進 paper)

**Phase 1 結論**: tooling GO + strategy NO-GO. 詳 [phase1_conclusion.md](phase1_conclusion.md).

---

### Week 7+ — 原 Vertical Spread / Paper Trading 計劃 ❌ 取消

原 plan 預期 OOS Sharpe > 1 後進 paper trading. Phase 1 alpha 已證偽, **paper trading 取消**, 不再延續 IC/Vertical 路線 (避 data snooping).

---

## Phase 2：待 user 拍板（IC/Vertical 路線取消後重新規劃）

> Phase 1 已證偽 IC/Vertical short premium hypothesis. 原 Phase 2「Shioaji 串接 + Calendar + 實盤」基於「Phase 1 出口 OOS Sharpe > 1」前提, **該前提 fail → Phase 2 必須重新拍板方向**.

### 三選一（待 user 決定）

#### 選項 A — Stock Factor Model（換 asset class）

對齊 user memory「贏 0050」目標, 業界 quant interview 共通語言.

| 工作項目 | 簡單解釋 |
|---------|---------|
| 5 factor 模型 | Value / Momentum / Quality / Low vol / Size |
| 月 rebalance | 每月選前 30 檔, 1 個月後 rebalance |
| Benchmark | 0050 (台灣 50 ETF) |
| 重用 Phase 1 infra | walk-forward / Bootstrap CI / Pro 統計工具 全部帶過去 |
| ❌ 不可重用 | BSM / Greeks / IV / surface (option-specific) |
| Time | 8-12 週 |

#### 選項 B — Long Premium / Vol Arb（留 TXO 但換 strategy class）

承認 short premium 證偽, 改買方策略 / vol arb / overlay.

| 工作項目 | 簡單解釋 |
|---------|---------|
| (B1) Trend + option overlay | 趨勢往上買 OTM call / 往下買 OTM put, 不賭 vol direction |
| (B2) Long straddle on event | 結算 / 財報前買跨式, 賺大行情 |
| (B3) Calendar spread | 賣短月買長月, 賺 term structure mean reversion |
| 重用 Phase 1 infra | BSM / Greeks / IV / surface / RetailCostModel 全可重用 |
| Time | 6-10 週 |

#### 選項 C — Honest Reality（接受 retail 量化邊界）

接受 retail 100 萬 NTD active strategy 期望值輸 passive ETF.

| 工作項目 | 簡單解釋 |
|---------|---------|
| 每月定額 0050 / 0056 | passive 投資 |
| Phase 1 整理成 portfolio repo | 為 quant engineer career interview 用 |
| 學 quant infra (factor / risk / OMS / data pipeline) | 履歷強化, 不真實盤 |
| Time | ongoing 每週 2-3 hr |

---

### Phase 2 出口條件（視子方向定）

| 子方向 | 出口條件 |
|------|----|
| A (factor) | 5yr OOS Sharpe vs 0050 + Information Ratio > 0.3 + Codex audit 通過 |
| B (vol-arb) | OOS Sharpe > 0.5 + Bootstrap CI 不跨零 + retail 摩擦 cover |
| C (passive) | 每年複利對齊 0050; portfolio repo + interview 拿到 quant 面試機會 |

---

## 永遠不做（Out of Scope）

- ❌ Naked Short Options（裸賣選擇權，無限風險）
- ❌ 高頻交易（latency-sensitive；超出 retail 邊界）
- ❌ 國外期權（流動性 / 稅務複雜度）
- ❌ 槓桿超過 broker 自然提供額度
- ❌ 跟 0050 DCA 實盤資金混用（user 月投 2.5 萬 100% 0050 DCA 不動）

---

## 相關檔案

- `README.md` — 專案 entry point + Phase 1 結論 summary
- `HANDOFF.md` — 當前 session snapshot（每 session end 覆寫）
- `CLAUDE.md` — Claude Code 守則 + SOP 三分級
- `docs/phase1_conclusion.md` — Phase 1 alpha 證偽 honest report
- `docs/options_math_audit.md` — Layer 2 options-specific math reference (PCP / Greeks / py_vollib / no-arb)
- `docs/bsm_derivation.md` — BSM-Merton 數學推導 + R1-R3 教訓錨定
- `docs/taifex_data_source_spec.md` — TAIFEX 資料源 endpoint / encoding / schema 正式規格
- `docs/week4_vol_surface_plan.md` — Week 4 vol surface plan v1 (歷史)
- `.claude/skills/{self-audit,multi-perspective,forensic-sweep}/SKILL.md` — 19 條 self-audit pattern + procedural skill
- `Codex-Prompt.md` — 當前 milestone 的外部 LLM audit 任務書
- `reports/week6_5yr_*` — 5yr backtest 帶成本結果
- `reports/week6_5yr_no_cost/` — 5yr cost-free baseline 結果
- `reports/week6_5yr_no_cost_vs_with_cost.csv` — 兩組 side-by-side 對比
- Plan: `C:\Users\chongweihuang\.claude\plans\week3-keen-babbage.md`

---

**Last updated**: 2026-05-02（Phase 1 完工 + 5yr backtest done + alpha 證偽 honest report + R12.0-R12.13 連 14 輪 Codex audit closed + Phase 2 待 user 拍板 A/B/C）
