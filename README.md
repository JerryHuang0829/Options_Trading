# Options_Trading

中頻系統化期權（options）研究專案，主攻**台指選擇權（TXO）Iron Condor（鐵兀鷹）+ Vertical spread** 策略 + 5yr OOS walk-forward 驗證 + Pro 量化統計工具鏈.

> **Status (2026-05-02)**: **Phase 1 完工** — 工程 tooling **GO**, strategy alpha hypothesis **NO-GO**. 詳 [docs/phase1_conclusion.md](docs/phase1_conclusion.md).

## 專案定位

| 面向 | 內容 |
|------|------|
| 研究目標 | 自主操盤實戰 + Buy-side quant 求職雙軌 |
| Phase 1（已完工）| IC + Vertical / TAIFEX 5yr walk-forward / 自寫 BSM-Merton / Pro 統計（Bootstrap CI / sign-flip permutation / Deflated Sharpe / Calmar）|
| Phase 1 結論 | 5yr OOS Sharpe 全 negative (-2.1 ~ -2.9), HMM gate 0-1 trades. **alpha hypothesis 證偽, 不進 paper trading**. |
| Phase 2（規劃中）| 三選一：(A) Stock factor model / (B) Long premium / vol arb / (C) Event-driven IV crush. 待 user 拍板 |

## 技術棧

- **語言**：Python 3.12 (conda `options` env, conda-forge channel)
- **定價核心**：自寫 BSM-Merton (含股利 q) + 5 Greeks；`py_vollib.black_scholes_merton` 作 pytest reference (4 規則單位換算)
- **波動率曲面**：SVI / SABR / poly fit + arb-free check, 1227 shards 跨 2021-2026 5yr full coverage
- **資料**：TAIFEX 每日 option chain (1963 days raw / strategy_view); TAIEX spot for regime gate
- **回測**：walk-forward backtest (252-day train / 63-day disjoint OOS quarterly), 6 scenario (IC / Vertical × vanilla / IV-percentile / HMM)
- **Retail 摩擦**：commission NT$12 / 期交稅 10 bps / slippage 15 bps + worst-side fill (非 mid)
- **Pro 統計**：Bootstrap CI 95% / sign-flip permutation (Politis & Romano 2010) / Deflated Sharpe (López-de-Prado 2014) / Calmar
- **跨平台 launch**：subprocess.run + sys.exit(returncode) + cp950 stderr clean (R12.0-R12.13 連續 audit fix)
- **測試**：pytest 447 passed, 2 skipped (~3 min full)
- **Audit 紀律**：19 條 self-audit pattern + audit_doc_drift.py automated gate + Codex external audit chain (R12.0-R12.13)

## 快速開始

```bash
conda create -c conda-forge --override-channels -n options python=3.12 -y
conda activate options
pip install -r requirements.txt
cp .env.example .env

# 全綠 verification
pytest tests/ -q                                          # 預期 447 passed, 2 skipped
ruff check src tests config scripts                       # PASS
ruff format --check src tests config scripts              # PASS
mypy src tests config scripts                             # PASS
python scripts/audit_doc_drift.py                         # PASS

# Smoke pipeline (~3-4 min)
python scripts/_validate_week6_5yr.py --smoke --skip-surface-coverage-gate

# 5yr full backtest (~18-20 min)
python scripts/_validate_week6_5yr.py                     # with retail cost
python scripts/_validate_week6_5yr.py --no-cost-model     # cost-free baseline
```

## 架構

```
src/
├── options/       # BSM / Greeks / chain / regime gate (IV percentile / HMM 2-state) / vol surface
├── strategies/    # Iron Condor / Vertical (IV skew gated) / RegimeWrappedStrategy
├── backtest/      # walk_forward / engine / portfolio / execution (RetailCostModel) / monitor / stats
└── data/          # TAIFEX loader / synthetic / enrich / cache
tests/             # 鏡像 src/ 結構, 447 tests
config/            # 常數
scripts/           # CLI 入口 (_validate_week6_5yr.py / _validate_surface_mark_5_4a.py / audit_doc_drift.py)
docs/              # bsm_derivation.md / options_math_audit.md / phase1_conclusion.md / roadmap.md / taifex_data_source_spec.md
.claude/skills/    # self-audit (19-pattern) + multi-perspective + forensic-sweep
reports/           # week6_5yr_* (with-cost) + week6_5yr_no_cost/ (cost-free baseline)
data/taifex_cache/ # 1963 raw shards + 1227 surface_fits (2021-2026 100% coverage)
```

## Phase 1 5yr 真實結果

| Scenario | With-cost Sharpe | No-cost Sharpe | Trades / 15 folds |
|----------|------|------|---|
| IC_vanilla | -2.7047 | -2.7055 | 5 |
| IC_IV_percentile | -2.6803 | -2.6869 | 4 |
| IC_HMM | 0.0000 | 0.0000 | 0 |
| Vertical_vanilla | -2.1463 | -2.1303 | 12 |
| Vertical_IV_percentile | -2.1249 | -2.1101 | 10 |
| Vertical_HMM | -2.8599 | -2.8670 | 1 |

**所有 |Δ Sharpe| ≤ 0.016** → retail 摩擦不是 root cause. **strategy 真的沒 alpha**.

詳 [docs/phase1_conclusion.md](docs/phase1_conclusion.md) + [reports/week6_5yr_summary.md](reports/week6_5yr_summary.md).

## 相關專案

- `../Quantitative-Trading`：舊 long-only TW stock factor research 專案, 2026-04-23 pivot 後保留為學習檔案

## Status & 文件導覽

- [HANDOFF.md](HANDOFF.md) — 當前 session snapshot（每 session end 覆寫）
- [docs/phase1_conclusion.md](docs/phase1_conclusion.md) — Phase 1 alpha 證偽 honest report
- [docs/roadmap.md](docs/roadmap.md) — Phase 1 / Phase 2 路線圖
- [Codex-Prompt.md](Codex-Prompt.md) — 當前外部 LLM audit 任務書
- [.claude/skills/self-audit/SKILL.md](.claude/skills/self-audit/SKILL.md) — 19 條 self-audit pattern
- [CLAUDE.md](CLAUDE.md) — Claude Code 守則 + SOP
