# Week 6 5yr Backtest Summary
- Range: 2021-04-01 → 2026-04-28
- Initial capital per fold: NT$1,000,000
- Total wall time: 1112.6s
- Codex audits applied: R12.0 P1-P4b, R12.1 caveats 1-4, R12.2-R12.9 P all closed

## Pro 出口條件閾值
| 指標 | 閾值 |
|---|---|
| Sharpe | ≥ 1.0 |
| Max DD | < 20% |
| Calmar | > 0.5 |
| Bootstrap 95% CI | 不跨零 |

## 6 Scenario 結果
| scenario | agg_sharpe | bootstrap_ci_low | bootstrap_ci_high | permutation_p_value | deflated_sharpe | agg_max_drawdown | calmar_ratio | total_trades | n_folds_total |
|---|---|---|---|---|---|---|---|---|---|
| IC_vanilla | -2.7055 | -1.4478 | 0.1903 | 0.1399 | 0.0000 | -0.0115 | -0.2310 | 5 | 15 |
| IC_IV_percentile | -2.6869 | -1.3582 | 0.2693 | 0.1678 | 0.0000 | -0.0115 | -0.2261 | 4 | 15 |
| IC_HMM | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 15 |
| Vertical_vanilla | -2.1303 | -1.9567 | -0.2304 | 0.0210 | 0.0000 | -0.0165 | -0.2621 | 12 | 15 |
| Vertical_IV_percentile | -2.1101 | -1.7918 | -0.1031 | 0.0470 | 0.0000 | -0.0141 | -0.2607 | 10 | 15 |
| Vertical_HMM | -2.8670 | -0.7874 | 1.0931 | 0.9311 | 0.0000 | -0.0076 | 0.0191 | 1 | 15 |

## Surface Fallback Metrics
| scenario | n_fallback_surface_total | n_fallback_settle_total | n_fallback_settle_3rd_total | settle_3rd_fallback_ratio | fallback_legs_ratio | avg_fallback_rate |
|---|---|---|---|---|---|---|
| IC_vanilla | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0079 | 0.0003 |
| IC_IV_percentile | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0098 | 0.0003 |
| IC_HMM | 0.0000 | 0.0000 | 0.0000 | NaN | NaN | 0.0000 |
| Vertical_vanilla | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Vertical_IV_percentile | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Vertical_HMM | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Regime Gate Ablation
| strategy | backtest_scope_yr | vanilla_sharpe | vanilla_ci_low | vanilla_ci_high | IV_percentile_sharpe | IV_percentile_ci_low | IV_percentile_ci_high | HMM_sharpe | HMM_ci_low | HMM_ci_high | gate_alpha_evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| IronCondor | 5yr | -2.7055 | -1.4478 | 0.1903 | -2.6869 | -1.3582 | 0.2693 | 0.0000 | NaN | NaN | inconclusive (CI 重疊或數據不足) |
| Vertical | 5yr | -2.1303 | -1.9567 | -0.2304 | -2.1101 | -1.7918 | -0.1031 | -2.8670 | -0.7874 | 1.0931 | inconclusive (CI 重疊或數據不足) |

## Monitor Metrics
```json
{
  "metrics_per_scenario": {
    "IC_vanilla": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 126.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 1.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 1.0,
      "fallback_legs_ratio": 0.007936507936507936,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.00026455026455026457,
      "rejected_reasons_n": 0
    },
    "IC_IV_percentile": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 102.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 1.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 1.0,
      "fallback_legs_ratio": 0.00980392156862745,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.00026455026455026457,
      "rejected_reasons_n": 0
    },
    "IC_HMM": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 0.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": NaN,
      "settle_3rd_fallback_ratio": NaN,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    },
    "Vertical_vanilla": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 174.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": 0.0,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    },
    "Vertical_IV_percentile": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 150.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": 0.0,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    },
    "Vertical_HMM": {
      "n_days_observed": 945.0,
      "n_legs_marked_total": 8.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": 0.0,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    }
  },
  "scenario_pnl_divergence": {
    "IC_HMM_vs_IC_IV_percentile_abs_diff_sum": 48826.21241278839,
    "IC_HMM_vs_IC_IV_percentile_n_aligned_days": 945.0,
    "IC_HMM_vs_IC_vanilla_abs_diff_sum": 57051.21241278839,
    "IC_HMM_vs_IC_vanilla_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_HMM_abs_diff_sum": 20250.0,
    "IC_HMM_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 177725.0,
    "IC_HMM_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_vanilla_abs_diff_sum": 216650.0,
    "IC_HMM_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_IC_vanilla_abs_diff_sum": 8225.0,
    "IC_IV_percentile_vs_IC_vanilla_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_HMM_abs_diff_sum": 62301.21241278839,
    "IC_IV_percentile_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_IV_percentile_abs_diff_sum": 185708.7875872116,
    "IC_IV_percentile_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 226390.0,
    "IC_IV_percentile_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_HMM_abs_diff_sum": 70526.21241278839,
    "IC_vanilla_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_IV_percentile_abs_diff_sum": 193933.7875872116,
    "IC_vanilla_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_vanilla_abs_diff_sum": 234615.0,
    "IC_vanilla_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "Vertical_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 157475.0,
    "Vertical_HMM_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "Vertical_HMM_vs_Vertical_vanilla_abs_diff_sum": 196400.0,
    "Vertical_HMM_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "Vertical_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 46550.0,
    "Vertical_IV_percentile_vs_Vertical_vanilla_n_aligned_days": 945.0
  }
}
```

## Caveats (R12.1 Codex)
- permutation_test 用 sign-flip (Politis & Romano 2010); H0「對稱、零 drift」對 IC short-premium negative-skew PnL 違反假設, p_value 只能輔助, 不能當唯一 alpha 顯著性結論. Bootstrap 95% CI 不跨零仍是 Phase 1 出口主硬條件.
- walk-forward step=test=63 (disjoint OOS, R12.0 P3); 5yr ~= 16 folds, 7yr ~= 24 folds. 原 plan 預設 1mo step (~48 folds) 會 OOS 重疊 → aggregate metric inflate, 已禁.
- TXO retail 摩擦 default: NT$12 commission/口 + 10 bps tax (TAIFEX 0.001) + 15 bps slippage (R12.0 P4a; pre-fix tax 2 bps 5x 低估).
- HMM convergence warnings: 792. hmmlearn 對短窗 / noisy regime period 偶發 'Model is not converging'; high count signals regime-detection unreliability.

## Phase 1 Strategy Verdict (R12.12 honest report)
- 5yr OOS Sharpe (all scenarios): negative; HMM gate 0-1 trades / 15 folds → **strategy NO-GO for paper trading**. Phase 1 alpha hypothesis (IC + Vertical with regime gate on 5yr TXO) **falsified**. Pro 紀律: 不反向改 strategy chasing positive Sharpe (= data snooping); honest 接受結論. 詳 [docs/phase1_conclusion.md](../docs/phase1_conclusion.md).
- **--no-cost-model 模式**: cost_model=None (commission/tax/slippage 全 0). Sharpe 是 upper bound, NOT realistic — 不可作 paper trading 依據.
