# Phase 1 結論 — TXO Iron Condor + Vertical short premium（5 年 walk-forward）

> 一句話：**Phase 1 verdict 是 NO-GO**（沒有任何 scenario 通過預設 Pro 出口條件）；
> 但嚴格而言這是「**未通過出口條件 / 樣本不足無法判定 (inconclusive)**」而非「short premium 假設被完整證偽 (falsified)」——
> 因為 IC scenario 5 年只成交 0~5 筆，binding constraint 是 TXO 每日 cohort 稀疏，不是 alpha 假設本身。

本文件是 README「結果與結論（Phase 1）」與 dashboard Page 2 / Page 3 的延伸（完整出口條件、6 scenario 詳細統計、
permutation H0 caveat、Phase 2 候選方向）；數字 single-source-of-truth 為
`reports/week6_5yr_scenarios.csv`、`reports/week6_5yr_ablation_matrix.csv`、`reports/week7_feasibility.json`。

---

## 1. 預設的 Pro 出口條件（pre-registered）

一個 scenario 要算「有可上線的 edge」，需同時滿足：

| 條件 | 來源 |
|---|---|
| aggregate Sharpe > 0 | — |
| Bootstrap 95% percentile CI 下界 > 0（CI 正側不跨零） | `src/backtest/stats.py::bootstrap_ci` |
| Deflated Sharpe Ratio > 0.95（多 scenario selection-bias 校正後仍顯著） | López-de-Prado 2014 |
| Calmar ratio > 0.5（年化報酬 / max drawdown） | — |

permutation p-value 為輔助（H0「對稱、零飄移」對 short-premium 的 negative-skew PnL 並不嚴格成立，見 §4 caveat）。

## 2. 5 年 OOS 實測結果（2021-04 ~ 2026-04，6 scenario × 15 disjoint quarterly fold）

| scenario | agg Sharpe | Bootstrap 95% CI | perm p | DSR | Max DD | Calmar | trades |
|---|---:|---|---:|---:|---:|---:|---:|
| Iron Condor — vanilla | −2.70 | [−1.46, **+0.15**] 跨零 | 0.127 | 0.00 | −2.77% | −0.23 | **5** |
| Iron Condor — IV percentile gate | −2.68 | [−1.37, **+0.24**] 跨零 | 0.160 | 0.00 | −2.42% | −0.23 | **4** |
| Iron Condor — HMM 2-state gate | 0.00 | n/a | 1.00 | 0.00 | 0.00% | n/a | **0** |
| Vertical — vanilla | −2.15 | [−1.97, **−0.25**] 不跨零 | 0.015 | 0.00 | −6.94% | −0.26 | **12** |
| Vertical — IV percentile gate | −2.12 | [−1.81, **−0.13**] 不跨零 | 0.043 | 0.00 | −5.43% | −0.26 | **10** |
| Vertical — HMM 2-state gate | −2.86 | [−0.79, **+1.09**] 跨零 | 1.00 | 0.00 | −0.99% | +0.02 | **1** |

→ **6 個 scenario 沒有一個通過出口條件**（Sharpe 全負、DSR 全 0、Calmar 全 ≤ 0；CI 半數跨零）。
**Phase 1 verdict：NO-GO** —— 不可作 paper trading 依據。

## 3. 但「NO-GO」 ≠ 「short premium 被證偽」—— 為什麼要區分

- **IC scenario（0~5 筆 / 5 年）**：交易筆數太少，Bootstrap CI 跨零、permutation p > 0.1 → 統計上**無法區分於零**。
  這不是「測了、edge 為負」，而是「**根本沒進到足夠的場去測**」→ 屬 **inconclusive / under-powered**。
  這也是 `reports/week6_5yr_ablation_matrix.csv` 的 `gate_alpha_evidence` 欄寫 `inconclusive (CI 重疊或數據不足)` 的原因。
- **Vertical scenario（10~12 筆）**：CI 不跨零、p < 0.05 → 弱顯著為負，是「邊際上負 edge」的**提示**，但 n=10~12 仍偏小，
  DSR 經 selection-bias 校正後為 0；不足以當成「穩健證偽」。
- **HMM gate（0~1 筆）**：504 天 lookback + 252 天 percentile pre-warm ≈ 需 ~3 年資料才能開火，之後又 fail-closed 居多
  （5 年 backtest 吐 792 個 hmmlearn convergence warning）→ 這個 ablation arm 在 5 年窗下幾乎沒有資訊量。

完整 falsify「TXO short premium 沒有 edge」需要的是：**進得了場、跑滿足夠樣本、edge 顯著為負或不顯著異於零**——
這要嘛放寬參數、要嘛拿到更多 cohort、要嘛換更流動的標的。那是 Phase 2 的 scope，不是 Phase 1 能下的定論。

## 4. Binding constraint：TXO 每日 cohort 稀疏（Week 7 feasibility 量化）

`reports/week7_feasibility.json`（5 年窗，1227 交易日）：

- 每日 unique expiry 數：**mean 1.44 / median 1.0 / p10 1.0 / p90 2.0 / max 2**，**沒有任何一天 ≥ 3 個 cohort**。
- 在 `IronCondor(target_dte=45, DTE_BAND=±7, MAX_DELTA_DIFF=0.05)` 下，要 4 條腿（short call / long call / short put / long put）
  的 delta 都落在 ±0.05、到期日落在 38~52 天 —— 在「每天平均只有 1.44 個到期」的結構下，多數日子湊不齊 → 不開倉。
- → 直接後果就是 §2 的 0~5 筆交易；retail cost ablation（§2.5 of dashboard）因此也 under-powered（|ΔSharpe| < 0.02，但只因為交易太少）。

permutation caveat：sign-flip permutation（Politis & Romano 2010）的 H0 是「PnL 對稱、零飄移」；short-premium PnL 是
左偏（小賺多、偶爾大賠），H0 不嚴格成立，故 p-value 只能輔助，Bootstrap CI 仍是主硬條件。

## 5. 結論與後續（Phase 2 候選方向，未做）

1. **Phase 1 結論定為 NO-GO（不是 falsified）** —— 框架已驗證、嚴謹判定流程已建立；strategy 假設則因樣本不足而 inconclusive。
2. **不在事後反向調參追正 Sharpe**（= data snooping）；任何參數放寬都需重新 pre-register 出口條件再跑 OOS。
3. Phase 2 候選（擇一，需先做 feasibility）：
   - 放寬 DTE band / delta 容差 / 改用 weekly cohort 為主軸，讓策略真的能跑滿樣本（代價：可能 over-fit，須嚴格 OOS）；
   - 換更流動、cohort 更密的標的（如 SPX/ES options 或 TXO + 個股選擇權合併 universe）；
   - 接 Shioaji live 資料 + calendar hedge overlay（Week 7 feasibility 已標 calendar 1.35x viable、straddle 8.36x NO-GO）。

---

*本 repo 為研究框架；回測結果僅供研究與教育用途，過去績效不保證未來結果，不構成投資建議。*
