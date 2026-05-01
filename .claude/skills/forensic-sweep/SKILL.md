---
name: forensic-sweep
description: Cross-interference grep for sibling bug detection. Run after fixing one silent-skip / NaN guard / mark_policy enum / fallback path / ignore_errors / etc. Maps bug type to grep patterns and sweeps src/ tests/ scripts/ for sibling code with same shape that may need same fix. Replaces Layer 1 SOP Step 3+4 (grep 終態 + cross-interference sweep).
---

# forensic-sweep — Cross-Interference Grep Sweep

## When to invoke

修一個函式 / 一個 silent-skip / 一個 NaN guard / 一個 fallback path 後，**強制跑**抓 sibling 同型 bug。

對應 sop-checklist-template.md 已廢棄的 Step 3 (Grep 終態) + Step 4 (Cross-interference)。

## 用法

`/forensic-sweep <pattern_keyword>`

`<pattern_keyword>` 可以是：
- 內建 keyword（見下表）
- 自訂 grep regex

## 內建 Pattern Keyword 對照表

| Keyword | grep regex | 適用情境 |
|---------|-----------|---------|
| `silent-skip` | `try:\\s*$\\|except.*:\\s*pass\\|continue\\b\\|return None.*#` | 修一個 silent-skip 後找同型 |
| `nan-guard` | `pd\\.notna\\\|np\\.isfinite\\\|math\\.isfinite\\\|isnan` | 改一個 NaN check 後找同型 |
| `fallback` | `fallback\\\|or DEFAULT\\\|or fallback\\\|.*=.*if .* else` | 改一個 fallback 邏輯後找同型 |
| `mark-policy` | `mark_policy\\\|mark_to_market\\\|_mid_price` | 改 mark policy 後找全鏈 enum 處理 |
| `can-buy-can-sell` | `can_buy\\\|can_sell\\\|_assert_executable` | 改 execution gate 後找全鏈 side check |
| `ignore-errors` | `ignore_errors=True\\\|except.*pass\\\|with contextlib.suppress` | 改一個容錯後找同型 (R11.7 教訓 mask 反而假通過) |
| `mid-fallback` | `bid.*ask.*mid\\\|_mid_price\\\|mid_with_` | 改 mid price source 後找全 source priority chain |
| `to-numeric` | `to_numeric\\\|astype.*Int64\\\|errors=.coerce.` | 改一個 NaN-safe parse 後找同型 (Bug 3 contract_date) |
| `tempfile` | `tempfile\\.\\\|TemporaryDirectory\\\|mkdtemp` | 改 tmp dir 路徑後找同型 (R11.5 backfill_range vs test fixture) |
| `baseline-number` | `<具體數字>` (caller 提供舊 baseline) | 改 baseline 後 grep HANDOFF.md / docs/*.md 全文同步 |
| `schema-version` | `RAW_TAIFEX_COLUMNS\\\|schema_version\\\|frozenset` | 改 schema 後找跨 layer concat raise 同步 |
| `assert-executable` | `_assert_executable\\\|side==.buy.\\\|side==.sell.` | 改 execution side gate 後找同型 |

## Skill 流程

1. **User invoke**: `/forensic-sweep <keyword>` 或 `/forensic-sweep "<custom-regex>"`
2. **Claude 執行**:
   - 查 keyword → 對應 regex（內建）或直接用 custom
   - 跑 `Grep` 跨 `src/` `tests/` `scripts/`
   - 對每個命中：cat 該行 + 前後 3 行 context
3. **分類**：
   - **同型可能 bug**（需驗）
   - **不同 context, 無需修**
   - **已修 / 已守線**
4. **輸出 sibling list + 提示**：「修一個 → 同型可能還有 N 個」

## 輸出格式（強制）

```
=== /forensic-sweep Report ===
Keyword: <keyword> → regex: <resolved-regex>
Scope: src/ tests/ scripts/ (排除 .pytest_tmp / notebooks)

=== Hits (N total) ===
1. <file:line> <code line>
   Context (3-line before/after):
   <...>
   Verdict: ⚠️ 同型可能 bug → 需驗 / ✅ 不同 context 無需修 / ✅ 已守線

2. <file:line> ...

=== Summary ===
- 同型 bug 候選 (⚠️): X 處 → caller 必須逐一驗
- 不同 context (✅): Y 處
- 已守線 (✅): Z 處

修法建議:
- [候選 1] @ <file:line>: <suggested fix>
- [候選 2] @ <file:line>: ...
```

## Reference

- 已廢: `.claude/sop/sop-checklist-template.md` Step 3+4 (內容合併進本 skill)
- [CLAUDE.md §10](../../../CLAUDE.md) Codex Audit Follow-up 4 件硬規則 #2 (4 件 verification)
- `feedback_silent_bugs.md` Pattern 5 silent-skip sibling
