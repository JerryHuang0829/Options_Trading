---
name: multi-perspective
description: Apply 7 quant personas + Codex audit (8 attack angles) to a task / plan / code segment. Run after writing plan draft, completing a major milestone, or before sending Codex audit prompt. Each persona asks 2-3 attack-style questions targeting their domain weakness; output is consolidated P1/P2/P3 patch list. Systematizes CLAUDE.md §4 7-persona framework which was descriptive-only.
---

# multi-perspective — 7+1 Personas Pro Audit

## When to invoke

- 寫完 plan 草稿時（驗 plan 合理性 + 漏洞）
- 完成大 milestone（Week N day 5 / Phase 結束）時
- Codex audit prompt 寄出前自審（預擋 Codex 會找的 P）
- 用戶說「多角度評估」/「pro 視角」/「mock 面試官」/「self-attack」

對應 [CLAUDE.md §4](../../../CLAUDE.md) 7 personas 系統化執行（原文件只是描述，本 skill 是 procedural）。

## 7 Personas + Codex Audit (共 8 角度)

| 角色 | 核心關注 | Attack Questions Template | R-round 教訓 |
|------|---------|--------------------------|------------|
| **量化主管** | Architecture / portfolio 全貌 / 跨 module 一致性 | (1) 這個改動跟 portfolio mark policy / risk gate 全鏈是否對齊？(2) 跨 layer 改動有沒有破壞 R5 P1 紅線？(3) 為何這個是 priority 不是 backlog？ | R10.x mark_policy hybrid 三層 (enrich / portfolio / engine) 不同步 |
| **策略研究員 (Quant Researcher)** | FDR / OOS / bootstrap / PIT / permutation / regime | (1) 用什麼 metric 證明？R² / Sharpe / RMSE 是否業界標準？(2) PIT 真的 PIT 嗎？有 look-ahead 嗎？(3) 結果可重現？fixed seed 嗎？ | R10.5 PIT cache leak / R11.6 R² 不夠 vol surface 應 spread-IV-RMSE |
| **波動率交易員 (Vol Trader)** | IV surface / skew / term structure / IC 實戰 / spread economics | (1) 這個 fit 對 wide-spread / illiquid 月份還合理嗎？(2) ATM 認定怎麼選？(3) bid/ask 0.5 滑點 IC 真實 PnL 還活嗎？ | R11.6 fit universe / arb-free / OTM-only |
| **風控長 (CRO)** | Position sizing / tail risk / margin / loss cap / Greek limits | (1) Portfolio Δ/Γ/ν/Θ limit 在哪？(2) margin 升 80% 會 force liquidation 嗎？(3) tail event (Black Friday / 春節) 有 stress test？ | R11.6 P7 Greek-level risk gap acknowledged 但 Phase 2 才補 |
| **Buy-side 面試官 (含 quant dev)** | Code 可展示性 / 架構 / portfolio value / 文件清晰 | (1) 我能 5 分鐘看完 README 跑通嗎？(2) Test coverage 對 critical path 對嗎？(3) Codex / GitHub Actions CI 跑通嗎？ | R11.x SOP 文件化 / HANDOFF 同步 / requirements 缺 holidays |
| **資料工程師** | Look-ahead / 資料完整 / schema drift / corporate action | (1) Spot 缺天 怎麼處理？forward fill 還是 raise？(2) Schema 換版 (2025-12-08) 跨期 concat 行為？(3) Stock split / dividend 校正？ | Week 3 4 silent bug (OLDEST schema / mixed-type / contract_date NaN / ZIP magic) + R11.6 P1 spot 缺 3 day |
| **台灣 Retail Trader** | 滑點 / 手續費 / 稅金 / 流動性 / 券商限制 | (1) 真實 IC 開倉滑點多少 bp？(2) 證交稅 / 手續費 / 期交稅 算進 PnL 嗎？(3) Shioaji 真實 fill 跟 backtest 差多少？ | Phase 2 真實 trading 必補；Week 6+ 真 backtest 加 SlippageFillModel sweep |
| **(Codex audit) Adversary in good faith** | 不接受 Claude 措辭 / 跑命令真實 output / silent semantic bug | (1) 「應該」「合理」「大致」皆視未實證 → 跑命令驗；(2) 修法 substance vs lip service？grep file 真實內容；(3) 邊界 attack：NaN / Inf / 負數 / multi-day / 跨 schema | R10-R11 系列 24 件 P |

## Skill 流程

1. **User invoke**: `/multi-perspective <task description / plan path / code section>`
2. **Claude 對每個角色執行**：
   - 列該角色 2-3 個 attack questions（從上面 template + 對應 task 客製化）
   - 從**該角色立場**回答（可能 attack 自己 plan / code）
3. **收集 attack points**：哪些是真 bug、哪些 acknowledged-推遲、哪些 不適用 task
4. **輸出 patch list**:
   - P1 (must-fix before next step)
   - P2 (must-fix in current sprint)
   - P3 (Phase 2 backlog)

## 輸出格式（強制）

```
=== /multi-perspective Audit ===
Task: <task description>

### 角色 1: 量化主管
Q1: <attack question>
A1: <claude 從該角色立場回答 / 可能找到漏洞>
Q2: ...

### 角色 2: 策略研究員
...

[8 角色全跑完]

=== Consolidated Patch List ===
P1 (must-fix before next step):
- [角色] <patch description> @ <file:line>
- ...

P2 (must-fix this sprint):
- [角色] <...>

P3 (Phase 2 backlog):
- [角色] <...>

Verdict: GO / GO-WITH-CAVEATS / NO-GO for <next milestone>
```

## Reference

- [CLAUDE.md §4](../../../CLAUDE.md) 7 角色描述 (本 skill 系統化執行)
- [Codex-Prompt.md](../../../Codex-Prompt.md) Codex audit 攻擊 pattern 範本
- `feedback_multi_perspective.md` memory — 7 personas + Codex audit invocation rules
