# Week 6 5yr Backtest Summary
- Range: 2021-04-01 → 2026-04-28
- Initial capital per fold: NT$1,000,000
- Total wall time: 1097.6s
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
| IC_vanilla | -2.7047 | -1.4640 | 0.1549 | 0.1269 | 0.0000 | -0.0116 | -0.2330 | 5 | 15 |
| IC_IV_percentile | -2.6803 | -1.3748 | 0.2354 | 0.1598 | 0.0000 | -0.0116 | -0.2282 | 4 | 15 |
| IC_HMM | 0.0000 | NaN | NaN | 1.0000 | 0.0000 | 0.0000 | NaN | 0 | 15 |
| Vertical_vanilla | -2.1463 | -1.9690 | -0.2514 | 0.0150 | 0.0000 | -0.0166 | -0.2628 | 12 | 15 |
| Vertical_IV_percentile | -2.1249 | -1.8085 | -0.1306 | 0.0430 | 0.0000 | -0.0141 | -0.2616 | 10 | 15 |
| Vertical_HMM | -2.8599 | -0.7889 | 1.0929 | 1.0000 | 0.0000 | -0.0077 | 0.0157 | 1 | 15 |

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
| IronCondor | 5yr | -2.7047 | -1.4640 | 0.1549 | -2.6803 | -1.3748 | 0.2354 | 0.0000 | NaN | NaN | inconclusive (CI 重疊或數據不足) |
| Vertical | 5yr | -2.1463 | -1.9690 | -0.2514 | -2.1249 | -1.8085 | -0.1306 | -2.8599 | -0.7889 | 1.0929 | inconclusive (CI 重疊或數據不足) |

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
    "IC_HMM_vs_IC_IV_percentile_abs_diff_sum": 49429.96007778839,
    "IC_HMM_vs_IC_IV_percentile_n_aligned_days": 945.0,
    "IC_HMM_vs_IC_vanilla_abs_diff_sum": 57870.221745288385,
    "IC_HMM_vs_IC_vanilla_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_HMM_abs_diff_sum": 20290.985300000004,
    "IC_HMM_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 178113.8847375,
    "IC_HMM_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_HMM_vs_Vertical_vanilla_abs_diff_sum": 217121.58337500002,
    "IC_HMM_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_IC_vanilla_abs_diff_sum": 8440.261667499999,
    "IC_IV_percentile_vs_IC_vanilla_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_HMM_abs_diff_sum": 62945.94537778839,
    "IC_IV_percentile_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_IV_percentile_abs_diff_sum": 186624.6438897116,
    "IC_IV_percentile_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 227388.55494,
    "IC_IV_percentile_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_HMM_abs_diff_sum": 71386.20704528839,
    "IC_vanilla_vs_Vertical_HMM_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_IV_percentile_abs_diff_sum": 195064.9055572116,
    "IC_vanilla_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "IC_vanilla_vs_Vertical_vanilla_abs_diff_sum": 235828.8166075,
    "IC_vanilla_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "Vertical_HMM_vs_Vertical_IV_percentile_abs_diff_sum": 157822.8994375,
    "Vertical_HMM_vs_Vertical_IV_percentile_n_aligned_days": 945.0,
    "Vertical_HMM_vs_Vertical_vanilla_abs_diff_sum": 196830.59807500002,
    "Vertical_HMM_vs_Vertical_vanilla_n_aligned_days": 945.0,
    "Vertical_IV_percentile_vs_Vertical_vanilla_abs_diff_sum": 46632.698637500005,
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
