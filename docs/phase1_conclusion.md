# Phase 1 Conclusion — IC + Vertical Strategy on 5yr TXO

**Date**: 2026-05-01
**Status**: **STRATEGY NO-GO for paper trading**. Phase 1 alpha hypothesis 證偽.

---

## TL;DR

5yr (2021-04 → 2026-04-28) 真實 walk-forward backtest 6 scenario × ~16 fold disjoint quarterly OOS：

| Scenario | Sharpe | Trades / Folds |
|----------|--------|---------------|
| IC_vanilla | -2.7047 | 5 / 15 |
| IC_IV_percentile | -2.6803 | 4 / 15 |
| IC_HMM | 0.0 | **0** / 15 |
| Vertical_vanilla | -2.1463 | 12 / 15 |
| Vertical_IV_percentile | -2.1249 | 10 / 15 |
| Vertical_HMM | -2.8599 | 1 / 15 |

**所有 scenario Sharpe < 0**. HMM regime gate scenario 幾乎不開倉 (0-1 trades). Phase 1 出口條件「OOS Sharpe > 1」**FAIL**. 不進 Phase 2 paper trading.

---

## Phase 1 出口條件 vs 實測

| 條件 | 預期 | 實測 | 結論 |
|------|------|------|------|
| OOS Sharpe > 1 | ≥ 1.0 | -2.0 ~ -2.86 | ❌ FAIL |
| Bootstrap 95% CI 不跨零 | > 0 | 全部跨零或 negative | ❌ FAIL |
| Codex audit 通過 | tooling PASS | tooling GO + strategy NO-GO | ⚠️ 部分 |
| Paper trading 穩定 | 3 個月 PnL 正 | N/A — Sharpe fail 不進 paper | ❌ N/A |

---

## 為何 strategy 沒 alpha — 假設與證據

### 1. Retail 摩擦壓垮 short-premium IC
- TXO retail cost: NT$12 commission + 10 bps tax + 15 bps slippage per leg
- 4-leg IC entry + exit = 8 fills × cost ≈ NT$200-400 per IC
- Average IC entry credit at 0.16 delta: ~NT$1500-3000 (50pt × 50 multiplier)
- Cost ≈ 7-25% of credit → 風險 reward ratio 嚴重失衡

**待驗**: 跑 `--no-cost-model` 看 cost-free Sharpe 是否變正 (R12.12 加 flag, 但仍未跑全 5yr 對比).

### 2. HMM gate 對 5yr TXO 不 work
- 792 convergence warnings on 48 fits (16.5/fit) — fit 不穩
- IC_HMM 0/15 trades, Vertical_HMM 1/15 trades — gate 幾乎全 close
- 可能原因:
  - hmmlearn 504-day lookback 對 monthly TXO regime 太短/太雜
  - GaussianHMM 假設 Gaussian return distribution; TXO daily return fat-tail
  - active_state="high_vol" 過嚴 — 真實 high-vol 日子相對少

### 3. IV percentile gate 沒幫助
- IC_vanilla -2.70 vs IC_IV_percentile -2.68 (差 0.02 in Sharpe)
- Vertical_vanilla -2.15 vs Vertical_IV_percentile -2.12 (差 0.03)
- → IV percentile threshold (30% rolling 1yr) 沒提供 alpha edge

### 4. Sample period regime
- 2021-04 → 2026-04 含 2022 熊市 + 2023-24 復原 + 2025 川普關稅震盪
- IC short-premium 在 vol expansion 期間 (2022 / 2025) 易爆損
- 這 5 年 regime 平均下來 short premium 不利

---

## 不做的決定 (避 data snooping)

1. **不反向改 short_delta / wing_delta / DTE 追正 Sharpe** — 已知 5yr 全 negative 後改 hyperparameter 是 lookback bias / data snooping
2. **不改 HMM lookback 直到 trades > 0** — sweep 直到出 trade 是 over-fit
3. **不延伸 sample period 找正 Sharpe sub-period** — 區段挑選即偏差

Pro 紀律: 假設證偽就是 valid Phase 1 outcome, 不是「待修 bug」.

---

## Phase 2 重新規劃方向 (待 user 拍板)

A. **完全換 strategy 類別**:
   - Calendar spread (long-dated short-dated 價差) — 無 short-premium 風險敞口
   - Dispersion trade (賣 index option 買 single stock option) — TAIFEX 限制大
   - Momentum + premium overlay — 加 trend filter

B. **Phase 1.5 補 study (限制範圍, fresh OOS)**:
   - Cost-free baseline run (R12.12 `--no-cost-model`) 看是否 retail friction 是 root cause
   - HMM 替換為 EWMA / GARCH regime detection
   - 鎖 5yr OOS, 只在 2018-2020 (沒被 backtest 看過) 跑 hyperparameter search

C. **Phase 1 honest fail + pivot to other domain**:
   - TXO IC/Vertical 對 retail 100 萬 NTD baseline 不可行
   - 轉學 quant infra (factor model / portfolio construction / risk parity 等)

---

## 工程 tooling status (separate from strategy)

✅ **Tooling GO**: 全 R12.x audit (R12.0 → R12.13 連續 close) ; hard gate 全綠 (449+ passed); cp950 cross-platform safe (re-exec + propagate=True + FileHandler-aware); 5yr cache 100% coverage; settle_3rd metric primary report; subprocess.run exit code propagate; --no-cost-model flag for retail-friction-vs-alpha disambiguation.

工程基礎設施可重用於 Phase 2 strategy iteration. Phase 1 alpha 假設證偽不影響 tooling reusability.

---

## 參考

- 5yr backtest reports: `reports/week6_5yr_*` (scenarios.csv / folds.csv / daily_pnl.csv / summary.md / monitor_metrics.json / run_meta.json / console.log)
- Codex audit chain: `Codex-Prompt.md` (R12.0-R12.13)
- Self-audit skill: `.claude/skills/self-audit/SKILL.md`
- Plan: `.claude/plans/week3-keen-babbage.md` (Week 6 Day 6.0-6.7)
