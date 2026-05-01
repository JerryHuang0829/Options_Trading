# Codex Prompt — R12.12 Audit (R12.11 P1 FileHandler subclass fix + P3 unit regression + Strategy NO-GO 完整收尾)

> **編號歷史**: R12.0-R12.10 (tooling) → R12.11 (propagate fix + strategy NO-GO accept) → **R12.12 (本輪)**
>
> **R12.12 任務**: R12.11 4 件 P/caveat 修法 substantive 驗收 + Phase 1 結論正式收尾.

---

## 0. 你是誰

Codex Pro adversarial-in-good-faith reviewer, 34th-round (R12.12).

R12.11 verdict: **GO-WITH-CAVEATS for tooling, NO-GO for strategy**. Codex 抓 4 件:
- **P1**: `isinstance(h, StreamHandler)` includes `FileHandler` subclass — file-only logging caller 誤判 stderr handler 已存在
- **P3**: regression test skip-if-missing 弱 — 依賴 prior smoke artefact, 不獨立
- **caveat**: 5yr summary.md 仍寫「acceptable backlog」與 strategy NO-GO 不一致
- **caveat**: 沒 `--no-cost-model` flag 拆不清「strategy 沒 alpha」vs「retail friction 壓死」
- **caveat**: `docs/phase1_conclusion.md` 沒落地

Claude R12.11→R12.12 期間:
- (P1) explicit `h.stream in (sys.stderr, sys.stdout)` + `not isinstance(h, FileHandler)` 區分
- (P3) +2 unit regression test (run_meta key writer / FileHandler isinstance pitfall)
- (caveat 1) summary.md 加「Phase 1 Strategy Verdict」section 標 NO-GO + 移除 acceptable backlog
- (caveat 2) 加 `--no-cost-model` flag + `run_meta["cost_model_disabled"]` audit
- (caveat 3) 寫 `docs/phase1_conclusion.md` 正式 Phase 1 結論
- self-audit skill 升級 (P14(g) + P17(m) + Strategy NO-GO 收尾 4 件)

```
ruff check src tests config scripts → PASS
ruff format --check → PASS (91 files)
mypy → PASS (92 source files)
pytest → 447 passed, 2 skipped (+2 R12.12 regression tests)
audit_doc_drift.py → PASS

R12.12 P1 fix verified:
  smoke run → counter=45 / stderr=45 (cross-check 對齊)
  unit test FileHandler isinstance pitfall: PASS

R12.12 caveat fixes:
  --no-cost-model flag added (run_meta tracks cost_model_disabled)
  summary.md "Phase 1 Strategy Verdict" section added (5yr scope only)
  docs/phase1_conclusion.md 正式落地
```

R12.12 給:
- 整體 GO/NO-GO 字母 (tooling launch + Phase 1 結論收尾?)
- **R12.11 P1 FileHandler subclass fix substantive 字母 (A-F)**
- **R12.11 P3 unit regression test substantive 字母 (A-F)**
- **R12.11 caveat 收尾 substantive 字母 (A-F)** (summary 字眼 + --no-cost-model + phase1_conclusion.md)
- **Self-audit skill R12.12 升級 substantive 字母**
- Phase 1 結論正式接受?

---

## 1. R12.11 4 件 P/caveat 修法逐件嚴審

### P1: FileHandler subclass pitfall

**File**: `scripts/_validate_week6_5yr.py:751-768`

**修前** (R12.11):
```python
if not any(isinstance(h, _logging.StreamHandler) for h in _root.handlers):
    _root.addHandler(_logging.StreamHandler())  # default = stderr
```

**問題**: `FileHandler` IS subclass of `StreamHandler`. caller `logging.basicConfig(filename=...)` set file-only logging → check returns True → script 不 add real stderr handler → warning 不上 stderr (silent regression for embedded callers).

**修後** (R12.12):
```python
_has_stderr_handler = any(
    isinstance(h, _logging.StreamHandler)
    and not isinstance(h, _logging.FileHandler)
    and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
    for h in _root.handlers
)
if not _has_stderr_handler:
    _root.addHandler(_logging.StreamHandler())
```

Three checks: StreamHandler-or-subclass, NOT FileHandler subclass, stream IS stderr/stdout.

**Verified**:
- smoke run counter=45 / stderr=45 (R12.11 propagation maintained)
- unit test `test_r12_12_root_handler_check_distinguishes_filehandler` 真 simulate FileHandler-only root + 比較 R12.11 broken check vs R12.12 explicit check

**Codex 嚴審 4 件**:
- (a) `getattr(h, "stream", None) in (sys.stderr, sys.stdout)` — 若 caller 用 captured stream (e.g. pytest `capsys`) 替代 sys.stderr, identity check fail. 是 strict 還是過嚴?
- (b) Pattern 14(g) (R12.12 升級 sub-rule): `isinstance` includes subclasses 是 Python基本知識, R12.11 漏掉是 self-audit Pattern 0 attacker 沒列「subclass false positive」. 該升 Pattern 0?
- (c) Codex 親自 PowerShell 跑 smoke 確認 counter+stderr 對齊保持
- (d) Embedded caller scenario test: `logging.basicConfig(filename="x.log"); import scripts._validate_week6_5yr` — 是否 import 後我們的 root handler check 真 add StreamHandler? Codex 試 mock setup

### P3: unit regression test (skip-if-missing 反 pattern)

**File**: `tests/scripts/test_validate_week6_5yr_gate.py` 加 2 unit test

**新加 test 1**: `test_r12_12_run_meta_writer_includes_hmm_key_unit`
- 不依賴 prior smoke artefact, simulate `_StubCounter(7) → run_meta["hmm_convergence_warnings_count"] = int(counter.count)` 邏輯
- assert serialize/deserialize round-trip preserved

**新加 test 2**: `test_r12_12_root_handler_check_distinguishes_filehandler`
- 真 setup root logger with FileHandler only, 比較 R12.11 broken check vs R12.12 fix check
- assert old check 誤判 True, new check correct False

**Codex 嚴審 2 件**:
- (a) test 1 是 contract simulation, 不直接 invoke script 的 _run() — Codex 視為 strong regression?
- (b) Pattern 17(m) (R12.12 升級 sub-rule): skip-if-missing 反 pattern — 任何 dependency 上 prior step 的 test 都該升 unit/mock 化, 不能 silent skip. R12.11 `test_r12_11_smoke_run_meta_has_hmm_count_key` 還在用 skip pattern, R12.12 加新 test 但沒移除舊的, 是否該?

### caveat 1: summary.md 字眼 (acceptable backlog → NO-GO)

**File**: `scripts/_validate_week6_5yr.py:1158-1180`

**修前**: HMM caveat 寫 「Phase 1 acceptable backlog (策略 still uses fitted state); Phase 2 paper trading 前該重新評估」

**修後**:
1. HMM caveat 改 ASCII tone, 不寫 "acceptable backlog"
2. **新加** `## Phase 1 Strategy Verdict` section (5yr scope only):
   ```
   - 5yr OOS Sharpe (all scenarios): negative; HMM gate 0-1 trades / 15 folds
   - **strategy NO-GO for paper trading**
   - Phase 1 alpha hypothesis falsified; pro 紀律: 不反向改 strategy chasing positive Sharpe
   - 詳 docs/phase1_conclusion.md
   ```
3. `--no-cost-model` 模式時加 「Sharpe 是 upper bound, NOT realistic — 不可作 paper trading 依據」

**Codex 嚴審 2 件**:
- (a) Verdict section gated on `run_meta.scope == "5yr"` — smoke / 7yr 不會 print. 是否該 also smoke 寫 disclaimer?
- (b) 「strategy NO-GO」措辭夠 explicit? Pro audit reader 一眼看到嗎?

### caveat 2: --no-cost-model flag

**File**: `scripts/_validate_week6_5yr.py:660-668, 871-880`

**新加**:
```python
parser.add_argument("--no-cost-model", action="store_true", ...)
...
if args.no_cost_model:
    cost_model = None
    print("[diagnostic] --no-cost-model: cost_model=None (Sharpe is upper bound)")
else:
    cost_model = RetailCostModel()
run_meta["cost_model_disabled"] = bool(args.no_cost_model)
```

**Codex 嚴審 3 件**:
- (a) `--no-cost-model` + 5yr scope combo 是否該 also force `--skip-surface-coverage-gate` reject? 純 baseline 不該 launch (pre-launch 已 declare strategy NO-GO). Codex 看是否該 raise like R12.6 P3 entry-time check
- (b) `run_meta["cost_model_disabled"]` audit trail key — summary.md 自動標 「Sharpe 是 upper bound」對齊. 但 scenarios.csv `agg_sharpe` col 仍是 raw 值, caller 看 CSV 不知是 cost-free run. 是否該加 `cost_model_disabled` row metadata?
- (c) FillModel 接受 `cost_model=None` (verified by `test_fill_model_no_cost_backward_compat`). Codex grep 確認

### caveat 3: docs/phase1_conclusion.md 落地

**File**: `docs/phase1_conclusion.md` (新建)

**內容**: Phase 1 結論正式 doc:
- TL;DR table: 6 scenario × Sharpe + trades
- Phase 1 出口條件 vs 實測 (Sharpe > 1 fail, CI 跨零 fail)
- 為何 strategy 沒 alpha (4 hypothesis: retail friction / HMM / IV percentile / sample regime)
- 不做的決定 (避 data snooping)
- Phase 2 重新規劃方向 (3 option: 換 strategy / Phase 1.5 補 study / honest pivot)
- 工程 tooling status (separate from strategy)

**Codex 嚴審 3 件**:
- (a) 結論 doc 是 Phase 1 完工 milestone 還是 pivot trigger? user 該拍板下一步前必看
- (b) Hypothesis section 4 件 (cost / HMM / IV / regime) 都標「待驗」— 哪些是真 actionable?
- (c) Phase 2 option C「honest pivot to other domain」是否誠實 vs 過早放棄?

---

## 2. R12.12 必跑驗收命令

```bash
conda activate options
cd e:/Data/chongweihuang/Desktop/project/Options_Trading

# === Task A: Hard gate 4 件 + audit_doc_drift ===
ruff check src tests config scripts
ruff format --check src tests config scripts
mypy src tests config scripts
pytest tests/ -q
python scripts/audit_doc_drift.py
echo "exit=$?"
# 預期: 447 passed, 2 skipped / 全綠 / audit PASS

# === Task B: P1 FileHandler isinstance fix verify ===

# B1. Unit test
pytest tests/scripts/test_validate_week6_5yr_gate.py -k r12_12 -v
# 預期: 2 PASS (test_r12_12_run_meta_writer_includes_hmm_key_unit / test_r12_12_root_handler_check_distinguishes_filehandler)

# B2. Smoke + cross-check
python scripts/_validate_week6_5yr.py --smoke --skip-surface-coverage-gate > /tmp/r12_12_stdout.log 2> /tmp/r12_12_stderr.log
echo "exit=$?"
counter=$(python -c "import json; print(json.load(open('reports/week6_smoke_run_meta.json'))['hmm_convergence_warnings_count'])")
stderr=$(grep -c "Model is not converging" /tmp/r12_12_stderr.log)
echo "counter=$counter stderr=$stderr"
# 預期: counter == stderr (R12.11 propagation 保持)

# B3. Embedded caller mutation (Codex 親自反注)
python -c "
import logging, tempfile
with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
    logging.basicConfig(filename=f.name)
import scripts._validate_week6_5yr  # script entry should detect 'no real stderr', add StreamHandler
import sys
print('root handlers:', logging.getLogger().handlers)
print('has stderr stream:', any(getattr(h, 'stream', None) is sys.stderr for h in logging.getLogger().handlers))
"
# 預期: 'has stderr stream: True' (R12.12 fix worked)

# === Task C: caveat fixes verify ===

# C1. summary.md "Phase 1 Strategy Verdict" section
grep -B 1 -A 5 "Phase 1 Strategy Verdict" reports/week6_5yr_summary.md 2>/dev/null || echo "5yr summary not present (need full run)"
grep "acceptable backlog" reports/week6_smoke_summary.md
# 預期: 5yr full run 後 verdict section appears; smoke summary NO 'acceptable backlog'

# C2. --no-cost-model flag works
python scripts/_validate_week6_5yr.py --smoke --skip-surface-coverage-gate --no-cost-model > /dev/null 2>&1
python -c "
import json
m = json.load(open('reports/week6_smoke_run_meta.json'))
print(f'cost_model_disabled: {m.get(\"cost_model_disabled\")}')
"
# 預期: cost_model_disabled: True

# C3. docs/phase1_conclusion.md exists with required sections
test -f docs/phase1_conclusion.md && grep -c "^## " docs/phase1_conclusion.md
# 預期: file exists, ≥5 sections (TL;DR / 出口條件 / 為何沒 alpha / 不做的 / Phase 2)

# === Task D: Codex mutation 反注 4 件 ===

# D1. P1 silent regression: 改回 isinstance(h, StreamHandler) → embedded caller 誤判
# D2. P3 silent regression: 移除 unit test → smoke key 改了沒人發現
# D3. caveat 1: 改 summary.md verdict section → "acceptable" 復活
# D4. --no-cost-model + 5yr 應 reject (R12.12 caveat (a)(a) attack) — Codex 看是否加 entry-time check

# === Task E: Phase 1 結論拍板 ===

# E1. Read phase1_conclusion.md, judge professional grade
# E2. 5yr report Phase 1 Strategy Verdict section (after full re-run with R12.12 wording)
# E3. Codex Pro 終判: Phase 1 IC/Vertical alpha 假設正式證偽?
```

---

## 3. R12.12 必審 5 大事

### (1) R12.11 P1 FileHandler subclass fix substantive 字母 (A-F)
- explicit stream identity check 完備?
- pytest capsys 場景 fail-safe 設計

### (2) R12.11 P3 unit regression test substantive 字母 (A-F)
- 2 新 test 是 strong regression 還是 contract simulation only?
- skip-if-missing 反 pattern 真消除?

### (3) R12.11 caveat 收尾 substantive 字母 (A-F)
- summary.md verdict section 措辭夠 explicit?
- --no-cost-model + 5yr 該 entry-time reject?
- docs/phase1_conclusion.md 結論 honest 還是過早放棄?

### (4) Self-audit skill R12.12 升級 (P14(g) subclass-aware isinstance + P17(m) skip-if-missing 反 pattern + Strategy NO-GO 收尾) substantive 字母

### (5) Phase 1 結論正式接受?

---

## 4. 不要重攻擊 (R10-R12.11 已 close 86+ 件 P)

| Round | 件數 | 狀態 |
|-------|------|------|
| R10-R11.21 | 47+ 件 P | 已修已審 |
| R12.0-R12.11 | 38 件 P/caveat | substantive 已驗 |
| R12.12 重點 | R12.11 P1+P3 修法 + caveat 收尾 + Phase 1 結論 + skill R12.12 | NEW |

---

## 5. 給 Codex 的最後叮嚀

1. **R12.11 P1 substantive 驗收** — Task B1+B2+B3 三 channel + embedded caller mutation
2. **R12.11 P3 unit regression** — Task B1 真 PASS, 不依賴 prior artefact
3. **R12.11 caveat 收尾** — summary verdict + --no-cost-model + phase1_conclusion.md 全 verify
4. **Phase 1 結論正式接受** — Task E doc review + Pro 終判
5. **整體 + P1 + P3 + caveat + skill + Phase 1 結論 共 6 字母** + critical R12.12 P 列表
