# Week 6 smoke Backtest Summary
- Range: 2024-04-01 → 2025-04-01
- Initial capital per fold: NT$1,000,000
- Total wall time: 289.4s
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
| IC_vanilla | -2.2044 | -2.2729 | 2.0267 | 0.7423 | 0.0000 | -0.0094 | -1.2829 | 1 | 8 |
| IC_IV_percentile | -2.2044 | -2.2729 | 2.0267 | 0.7423 | 0.0000 | -0.0094 | -1.2829 | 1 | 8 |
| IC_HMM | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 8 |
| Vertical_vanilla | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 8 |
| Vertical_IV_percentile | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 8 |
| Vertical_HMM | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 8 |

## Surface Fallback Metrics
| scenario | n_fallback_surface_total | n_fallback_settle_total | n_fallback_settle_3rd_total | settle_3rd_fallback_ratio | fallback_legs_ratio | avg_fallback_rate |
|---|---|---|---|---|---|---|
| IC_vanilla | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| IC_IV_percentile | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| IC_HMM | 0.0000 | 0.0000 | 0.0000 | NaN | NaN | 0.0000 |
| Vertical_vanilla | 0.0000 | 0.0000 | 0.0000 | NaN | NaN | 0.0000 |
| Vertical_IV_percentile | 0.0000 | 0.0000 | 0.0000 | NaN | NaN | 0.0000 |
| Vertical_HMM | 0.0000 | 0.0000 | 0.0000 | NaN | NaN | 0.0000 |

## Regime Gate Ablation
| strategy | backtest_scope_yr | vanilla_sharpe | vanilla_ci_low | vanilla_ci_high | IV_percentile_sharpe | IV_percentile_ci_low | IV_percentile_ci_high | HMM_sharpe | HMM_ci_low | HMM_ci_high | gate_alpha_evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| IronCondor | smoke | -2.2044 | -2.2729 | 2.0267 | -2.2044 | -2.2729 | 2.0267 | 0.0000 | NaN | NaN | inconclusive (CI 重疊或數據不足) |
| Vertical | smoke | 0.0000 | NaN | NaN | 0.0000 | NaN | NaN | 0.0000 | NaN | NaN | inconclusive (CI 重疊或數據不足) |

## Monitor Metrics
```json
{
  "metrics_per_scenario": {
    "IC_vanilla": {
      "n_days_observed": 168.0,
      "n_legs_marked_total": 12.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": 0.0,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    },
    "IC_IV_percentile": {
      "n_days_observed": 168.0,
      "n_legs_marked_total": 12.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": 0.0,
      "settle_3rd_fallback_ratio": 0.0,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    },
    "IC_HMM": {
      "n_days_observed": 168.0,
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
      "n_days_observed": 168.0,
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
    "Vertical_IV_percentile": {
      "n_days_observed": 168.0,
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
    "Vertical_HMM": {
      "n_days_observed": 168.0,
      "n_legs_marked_total": 0.0,
      "n_fallback_settle_total": 0.0,
      "n_fallback_surface_total": 0.0,
      "n_fallback_settle_3rd_total": 0.0,
      "fallback_days_count": 0.0,
      "fallback_legs_ratio": NaN,
      "settle_3rd_fallback_ratio": NaN,
      "avg_fallback_rate": 0.0,
      "rejected_reasons_n": 0
    }
  },
  "scenario_pnl_divergence": {
    "IC_HMM_vs_IC_IV_percentile_abs_diff_sum": 14875.0,
    "IC_HMM_vs_IC_IV_percentile_n_aligned_days": 168.0,
    "IC_HMM_vs_IC_vanilla_abs_diff_sum": 14875.0,
    "IC_HMM_vs_IC_vanilla_n_aligned_days": 168.0,
    "IC_HMM_vs_Vertical_HMM_abs_diff_sum": 0.0,
    "IC_HMM_vs_Vertical_HMM_n_aligned_days": 168.0,
    "IC_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 0.0,
    "IC_HMM_vs_Vertical_IV_percentile_n_aligned_days": 168.0,
    "IC_HMM_vs_Vertical_vanilla_abs_diff_sum": 0.0,
    "IC_HMM_vs_Vertical_vanilla_n_aligned_days": 168.0,
    "IC_IV_percentile_vs_IC_vanilla_abs_diff_sum": 0.0,
    "IC_IV_percentile_vs_IC_vanilla_n_aligned_days": 168.0,
    "IC_IV_percentile_vs_Vertical_HMM_abs_diff_sum": 14875.0,
    "IC_IV_percentile_vs_Vertical_HMM_n_aligned_days": 168.0,
    "IC_IV_percentile_vs_Vertical_IV_percentile_abs_diff_sum": 14875.0,
    "IC_IV_percentile_vs_Vertical_IV_percentile_n_aligned_days": 168.0,
    "IC_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 14875.0,
    "IC_IV_percentile_vs_Vertical_vanilla_n_aligned_days": 168.0,
    "IC_vanilla_vs_Vertical_HMM_abs_diff_sum": 14875.0,
    "IC_vanilla_vs_Vertical_HMM_n_aligned_days": 168.0,
    "IC_vanilla_vs_Vertical_IV_percentile_abs_diff_sum": 14875.0,
    "IC_vanilla_vs_Vertical_IV_percentile_n_aligned_days": 168.0,
    "IC_vanilla_vs_Vertical_vanilla_abs_diff_sum": 14875.0,
    "IC_vanilla_vs_Vertical_vanilla_n_aligned_days": 168.0,
    "Vertical_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 0.0,
    "Vertical_HMM_vs_Vertical_IV_percentile_n_aligned_days": 168.0,
    "Vertical_HMM_vs_Vertical_vanilla_abs_diff_sum": 0.0,
    "Vertical_HMM_vs_Vertical_vanilla_n_aligned_days": 168.0,
    "Vertical_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 0.0,
    "Vertical_IV_percentile_vs_Vertical_vanilla_n_aligned_days": 168.0
  }
}
```

## Caveats (R12.1 Codex)
- permutation_test 用 sign-flip (Politis & Romano 2010); H0「對稱、零 drift」對 IC short-premium negative-skew PnL 違反假設, p_value 只能輔助, 不能當唯一 alpha 顯著性結論. Bootstrap 95% CI 不跨零仍是 Phase 1 出口主硬條件.
- walk-forward step=test=63 (disjoint OOS, R12.0 P3); 5yr ~= 16 folds, 7yr ~= 24 folds. 原 plan 預設 1mo step (~48 folds) 會 OOS 重疊 → aggregate metric inflate, 已禁.
- TXO retail 摩擦 default: NT$12 commission/口 + 10 bps tax (TAIFEX 0.001) + 15 bps slippage (R12.0 P4a; pre-fix tax 2 bps 5x 低估).
- HMM convergence warnings: 45. hmmlearn 對短窗 / noisy regime period 偶發 'Model is not converging'; high count signals regime-detection unreliability.
- **--no-cost-model 模式**: cost_model=None (commission/tax/slippage 全 0). Sharpe 是 upper bound, NOT realistic — 不可作 paper trading 依據.
