---
name: self-audit
description: Codex-grade pre-design + post-fix self-audit. 19 hard checks 分 4 大類 (Pre-design / Code-correctness / Validation-design / Doc+Discipline)；強制 Skill Chain 接 forensic-sweep + multi-perspective；Result Evidence Missing = FAIL。Run BEFORE plan/patch (Pattern 0) AND after fix / before report (Pattern 1-18). Updated 2026-04-29 (R11.18 後第 8 次升級).
---

# self-audit — Codex-Grade Pattern Check

## 觸發時機 (雙觸發)

| Trigger | 時機 | 跑哪幾條 |
|---------|------|---------|
| **A. Pre-design** | 寫 plan / patch / new module / new gate **之前** | Pattern 0 (攻破 → 重設計) |
| **B. Post-fix** | 修 P + 寫 test 後，回報「完成 / 全綠 / substance」**前** | Pattern 1-18 全跑 |

對應 [CLAUDE.md §2](../../../CLAUDE.md) Self-Audit SOP 三分級觸發。**任一條 FAIL = 修法不算成立**。

## 19 Hard Checks (按類分組)

### A. Pre-design (動手前；攻破則重設計)

| # | Pattern | 規則 | 來源教訓 |
|---|---------|------|---------|
| **0** | **Pre-design Attack Gate** (R12.2 升級) | 列 ≥5 attacker tests + expected failure mode + 跑最危險 1-2 個。**禁止「先寫完再回頭找理由 PASS」事後合理化**。**R12.2 加 sub-rule (real-data pipeline scripts)**: launch script (load cache + walk-forward + multi-stage report) 必把以下 4 件列入 attacker list (pre-design 必跑)：(a) **Lookback prerequisite**: 任何 regime/HMM/percentile gate lookback >= N → 真實 load 必 pre-load N 天 BEFORE backtest start (`pre_start_returns < lookback → raise`). (b) **Cache coverage**: surface_fits / mark cache 範圍 vs backtest range coverage_pct + danger_rows 計數 (`coverage_pct < 100% AND any rows have NaN bid/ask AND NaN model_price → raise`). (c) **Smoke exercises logic**: smoke run 必 ≥1 fold 跑通 strategy + walk-forward + monitor + stats end-to-end；0-fold smoke = hollow (見 P17 sub-rule f). (d) **Empty CSV pandas-readable**: rows=0 仍寫 header line, `pd.read_csv` 不能 EmptyDataError | R11.x 5 輪 + R12.0/R12.1/R12.2 連 3 輪 Codex 抓 4-5 件 critical P, self-audit Pattern 0 全 missed → 升級 |

### B. Code correctness (程式碼層；helper / consumer / mask 不偷工)

| # | Pattern | 規則 | 來源教訓 |
|---|---------|------|---------|
| **1** | Helper caller path + internal consistency | grep helper 真 caller path；cat output 不信 docstring；helper 內 var rename 必同步全 function | R11.3/R11.9 |
| **6** | 過度防呆 mask 假通過 | 計算路徑禁 `np.where / fillna / try-except-pass` 邊界值；邊界 raise 不在中段 mask | R11.7 butterfly w_safe |
| **8** | Defer / 容錯只測 unit 不測 e2e | `return False / None / skip` 必有 e2e test 走全 pipeline 驗 metric 不污染 | R11.3 close-gate defer |
| **10** | 臆測未查證 | 數學公式必含 citation；第三方 API 必 `python -c "help(X)"` 真貼 signature | R10.8 / R11.6 |
| **14** | Producer/Consumer Contract Parity (R11.13/14/16 + R12.2) | (a) Producer constraint → Consumer 必 mirror，grep `constraint\|invariant\|raise` 對齊 (b) **Mutation 後重算下游** derived col (mask `bid/ask` 後 `can_buy/can_sell` 必 drop+recompute) (c) **R12.2 升級 sub-rule (cross-frame state lifetime)**: walk-forward / fold-based / per-iteration framework — Consumer (CSV writer / report) 若需要 Producer (strategy / model) 的 internal state, 必在 Producer go out of scope **之前** snapshot 到 FoldResult / FrameResult dataclass; 不可事後 retrieve (instance 已 GC). 例: GatedIC.rejected_reasons accumulator 必 captured into `FoldResult.rejected_reasons: pd.DataFrame` 在 walk_forward 內部 each fold complete 時 | Cache 4 輪 silent + R11.16 stale flag + R12.2 walk_forward strategy lifetime gap |

### C. Validation design (驗證層；hollow PASS / metric 定義 / 數字 / mutation)

| # | Pattern | 規則 | 來源教訓 |
|---|---------|------|---------|
| **2** | Test name + count + metric definition drift (R11.15) | docstring N tests `grep -c def test_` 對齊；含 `rate/share/coverage/ratio` metric 必明文釐清分母分子 + 雙報 | R11.7 / R11.9 / R11.15 SVI rate 1.0 vs 0.8777 混用 |
| **3** | 「pytest 全綠」當 proxy | **4 件分開跑 scope=src tests config scripts** (R11.14)：`ruff check / format --check / mypy / pytest`；只貼 fail 的 evidence | R11.1/5/13 (.codex_tmp false fail) |
| **11** | Mutation test (Layer 1) | 反注原 bug → 跑新 test 必 fail；pass = test 沒抓到 bug 必補 | sop-checklist Step 1 |
| **12** | ≥3 組數字驗算 (Layer 1) | 不靠公式直覺，實跑 ≥3 input (正常/邊界/極端) 對齊 expected vs actual | sop-checklist Step 2 |
| **17** | **Hollow PASS detector + plumbing-vs-real-strategy** (R11.16/17 + R12.2 + R12.4) | (a) 寫 assertion 必反問「什麼條件會 fail?」(b) metric 必驗「真打到 measured path」非僅 schema (c) 「3 scenario 一致 / fallback=0 / sum>=0」weak assertion 必加 strict (d) 用 spy/mock/bypass 跑 e2e = **plumbing proof**，非 real-strategy 證明，claim 必標 「plumbing only」 (e) **R12.2 升級 sub-rule (smoke run 0-fold = hollow by definition)**: launch script smoke / quick test 若產生 0-fold / 0-trade / empty CSV / 沒打到 strategy 邏輯 → **textbook hollow PASS, 不能標 done**。Smoke 必選 sub-set 真產出 ≥1 fold + ≥1 trade + non-empty rejected_reasons / monitor metric. 「跑得動」≠ 「邏輯正確」 (f) **R12.2 升級 sub-rule (mid-run raise mutation)**: 修「pre-flight gate 防 mid-run raise」必 mutation 反證 — 移除 gate, 跑真 chain 真 raise; 不能僅 unit test 假設 raise scenario 存在 (g) **R12.4 升級 sub-rule (institutional-grade gate threshold = 0)**: pre-flight gate 對 hard-fail scenario 必 strict `count == 0`，不可用 % threshold (1% / 5% etc) — 「平均 OK 但邊界個別 fail」對 institutional Pro 不可接受。每個 danger row 可能對應實際持倉 → 個別 raise. Pro 解：升級 fallback chain 增第 N 層直到 truly_unmarkable 才 raise (e.g. mid → surface → settle → raise) | R11.15 Sharpe diff=0% / R11.17 _OpenOnceHoldIC bypass / R12.2 smoke 0-fold + mutation / R12.4 surface gate 1% threshold = hollow PASS — Codex toy 反證 individual row 真 raise; 改成 truly_unmarkable strict gate + 3-layer fallback |

### D. Doc + Discipline (紀律層；baseline / fixture / 自評 / 絕對句 / doc drift)

| # | Pattern | 規則 | 來源教訓 |
|---|---------|------|---------|
| **4** | Baseline 多點同步 + second-order trap | 改 baseline 必 grep 多個舊數字 (270/272/275/...) 0 殘留；改一處後**強制再 grep 新數字**確認 N-1 處同步 | R11.5/7/8/9 連 5 輪 |
| **5** | Claude env OK ≠ 修法成立 | 環境差異必 acknowledge 進 HANDOFF；不假裝跑通；Codex env / CI runner 差異標明 | R11.3-R11.5 tmpdir WinError 5 |
| **7** | Plan/文件內部一致 | 改完文件 grep 關鍵術語上下文同語意 | R11.6 mark priority |
| **9** | Fixture 真資料邊界沒覆蓋 | fixture 必抽真資料邊界 (早/晚/異常檔/schema 換版/停盤前後) | Week 3 silent bug 4 連發 |
| **13** | **Architectural fix trigger** (R11.9) | 同類 bug ≥3 輪重犯 → procedural skill 已無效 → **必跳級**提 architectural fix 給 user 拍板 (source-of-truth 集中 / 自動化 script / pre-commit hook / data structure 重設計) | Baseline 5 輪 / fit primitive 4 輪 / cache silent 連 4 輪 |
| **15** | **自評風險不是免死金牌 + 對立 failure mode** (R11.13/15) | (a) 「誠實補充」標 known unknown = P1 待修，30 min 內可補必補 (b) 寫 risk 必想「100% 對立會怎樣」(plan 「Sharpe diff > 30%」沒想到 「= 0%」也是 problem) | R11.11/12/13/15 連 4 輪自評風險被真抓 |
| **16** | Test fixture / helper 偷工 | helper 預設值用 dispatch dict 依 type schema-correct，不用 happy-path 一個預設套全部 type | R11.12 _make_record SVI 配 sabr 偷工 |
| **18** | **Absolute claim stress-test + Doc drift sweep + Automated gate** (R11.17/18/19) | (a) 含「永遠/絕不/必定/不可能/0%」絕對句必先想 ≥3 反例；反例存在 → 措辭降為條件句 (b) 改名/refactor 後必 `grep -rn "<舊名>" . --include="*.py" --include="*.md" --include="*.json"` **無 path filter** (含 HANDOFF/CLAUDE/plans/roadmap)；**禁止 `reports/ scripts/` 限定路徑** (c) 每輪 audit 後必 grep HANDOFF 「下一步：R<old>」/「最後一次 R<old>」殘留 (d) **claim vs reality**: 宣稱「修了 X」前必 grep verify X 真改了 (e) **R11.19 升級 Automated gate (Pattern 13 第二類已觸發)**: 改 doc 後必跑 `python scripts/audit_doc_drift.py` (5 類 drift: stale audit ref / OOS / stale baseline / plumbing-vs-real-strategy / absolute claim)；exit 1 = drift found = 修法不成立。**手寫紀律已實證連 3 輪失守 (R11.17-R11.19)**, 必靠 script 強制 | R11.17 「永遠不被觸發」絕對句 / R11.18 path filter 違反 / R11.19 docs/roadmap.md 連 3 輪沒掃到 stale → 觸發 Pattern 13 第二類 (architectural fix) → scripts/audit_doc_drift.py 落地 |

## Result Format (強制 — Evidence Missing = FAIL)

```
=== /self-audit Report ===
Task: <task description>

| # | Pattern | Status | Evidence |
| 0 | Pre-design Attack | ✅ / ❌ | <≥5 attackers + 跑 1-2 cmd output> |
| 1-18 | ... | ✅ / ❌ | <file:line + grep / cmd output / test result> |

Skill Chain:
  - /forensic-sweep: <invoked or N/A reason>
  - /multi-perspective: <invoked or N/A reason>

Result: PASS (19/19) / FAIL (X/19 → 必補：[列 FAIL patch list])
```

**Evidence Missing = FAIL** (R11.14)：PASS 行若 evidence 欄空 / 寫 "checked manually" / "looks ok" / 沒 cmd output / 沒 file:line / 沒 grep 結果 → **自動降級 FAIL**，不准 lip-service。

## Skill Chain (R11.14 加 — 強制接非 optional)

| Trigger | 必接 | 理由 |
|---------|-----|------|
| validation/cache/data/schema/serialization 層改動 | `/forensic-sweep` keywords (schema-version, nan-guard, fallback, silent-skip) | sibling bug 同型大概率存在 |
| 寫 plan / 大 milestone / new module / 架構決策 | `/multi-perspective` 7+1 personas (含 Codex audit) | 單視角必有盲區 |
| Pattern 15 known unknown 影響 next milestone | **直接 blocker** — 不准留給 Codex 抓 | R11.x 連 5 輪自評風險真抓 |
| 修 P + 寫 test 後 | self-audit Trigger B (19 條) | 回報 done 前必過 |
| 寫 plan / patch / new module 前 | self-audit Trigger A (Pattern 0) | 動手前先攻擊 |

## Reference

- [CLAUDE.md §2 + §10](../../../CLAUDE.md) Self-Audit SOP + Codex Follow-up 4 件硬規則
- [docs/options_math_audit.md](../../../docs/options_math_audit.md) Layer 2 reference
- `feedback_silent_bugs.md` memory — silent bug patterns
- `Codex-Prompt.md` history — R11 → R11.18 累計 38+ 件 P 修法軌跡

## Changelog (簡)

| 升級時點 | 主要新增 |
|---------|---------|
| Initial 12 條 | R11.x 基礎 + Mutation + 數字驗算 |
| R11.9 | + P13 Architectural fix trigger |
| R11.13 | + P14/P15/P16 (defense-in-depth / 自評免死 / fixture 偷工) |
| R11.14 | + P0 Pre-design Attack + Skill Chain + Evidence FAIL |
| R11.16 | + P17 Hollow PASS + P2(c) metric drift + P14(e) mutation 後重算下游 + P15(c) 對立 failure mode |
| R11.17 | + P17(e) plumbing-vs-real-strategy + P18 Absolute claim + Doc drift sweep |
| R11.18 | + P18 升級 grep 禁 path filter + Stale state cross-check + claim vs reality discipline |
| R11.19 | + P18(e) Automated gate via scripts/audit_doc_drift.py (Pattern 13 第二類 architectural fix 落地) |
| **R12.2** | **+ P0 sub-rule (real-data pipeline lookback prerequisite + cache coverage + smoke exercises logic + empty CSV header)** + **P14(c) cross-frame state lifetime (walk-forward strategy escapes scope before consumer reads)** + **P17(e/f) smoke 0-fold = hollow by definition + mid-run raise mutation 反證** |
| **R12.4** | **+ P17(g) institutional-grade gate threshold = 0** (pre-flight gate 對 hard-fail scenario % threshold = hollow PASS; 每 row 可能對應實際持倉 → 必 truly_unmarkable strict 0 gate + N-layer fallback chain). Codex toy 反證 1% threshold 漏網; 修法: mid_with_surface_fallback 加 settle 第 3 層 + truly_unmarkable strict gate. 加 `_extract_rejected_reasons` cycle-safe + depth 16 unwrap (R12.4 P4 fix `range(3)` silent fail) |
| **R12.5** | **+ P0 sub-rule (e) Windows cp950 stdout encoding crash**: launch script 在非 UTF-8 stdout 環境 (Windows PowerShell cp950 / cp1252 / 公司 locale 等) print 任何非 ASCII char (中文 / `≈` / box-drawing) 都會 UnicodeEncodeError → R12.4 smoke 命令在 prompt 內標 `--smoke` 但實跑 crash, 全 verification 不可重現. **修法**: script 入口必加 `sys.stdout = io.TextIOWrapper(buffer, encoding='utf-8', errors='replace')`, 不要求 user 設 PYTHONIOENCODING. **+ P14(d) two-tier gate parity** (degradation hidden by single-tier gate): 多層 fallback (mid → surface → settle) 的 gate 必逐 tier 獨立量化 + 獨立 raise threshold. 單層 `truly_unmarkable == 0` 看起來嚴, 但 silent 容許「上層 fallback 退化成下層」(50% surface coverage + 100% settle = surface gate 名存實亡). 修法: Tier 1 (coverage_pct >= 0.95) + Tier 2 (truly_unmarkable == 0) 雙閘. **+ P14(e) 同名計數混用 schema separation** (semantic-distinct paths 必分別 metric): `n_fallback_settle` 同時計 (a) direct settle policy (b) surface degraded to settle 兩條路 → caller 看到單一數字無法 attribution. 修法: 新加 `n_fallback_settle_3rd` separate metric, 既有 aggregate 保留 backward-compat |
| **R12.6** | **+ P0 sub-rule (e) extended: subprocess child stdio encoding**: R12.5 修了 main process stdout/stderr 的 UnicodeEncodeError, 但 multiprocessing/joblib 子 process (e.g. hmmlearn fit, pandas C ext) 寫 UTF-8 byte 給 parent's `_readerthread`, parent 用 cp950 decode → `UnicodeDecodeError: 'cp950' codec can't decode byte 0xe6` traceback in stderr (main exit=0, console.log 沒捕獲, 但 terminal 髒). **修法**: script 入口除了 wrap 自己的 stdout/stderr, 必同步 `os.environ.setdefault('PYTHONIOENCODING', 'utf-8')` + `os.environ.setdefault('PYTHONUTF8', '1')` 讓 child Python process 繼承 UTF-8 環境. **+ P17(h) primary-report metric surfacing**: 新加 metric (R12.5 settle_3rd_fallback) 必同步出現在 primary CSV / summary.md 給 user 看, 不能只藏 nested JSON ("internal correct but report missing" 仍是 hollow PASS — Codex caller 看 summary 看不到 metric 等於沒實作). **修法**: 新 metric 同步加進 `_scenario_aggregate_row` (CSV) + `_build_summary_md` (markdown table). **+ P17(i) early-exit gate timing**: pre-flight gate 必在「成本前」(parse-time / smoke-time) raise, 不可在 long-running step 後 raise (e.g. R12.5 5yr+skip 的 raise 在 enrich 後 = user 浪費 15-20 min). 修法: 移到 `main()` 入口 argparse 之後立即 check |
| **R12.7** | **+ P0 sub-rule (e) further extended: setdefault vs force overwrite for env-driven self-protection**. R12.6 用 `os.environ.setdefault('PYTHONIOENCODING', 'utf-8')` 修 subprocess child stdio. 但 user 預設 `PYTHONIOENCODING=cp950` (Windows PowerShell explicit) → setdefault 不覆蓋 → child 仍 cp950 → 仍 crash (Codex R12.6 反證). **核心 lesson**: script 對 own-protection env vars (encoding / locale / safety flags) 不該尊重 user override — script 是 authoritative source 知道自己需要什麼 child env. **修法**: `os.environ['PYTHONIOENCODING'] = 'utf-8'` (force overwrite), 不用 setdefault. Defensive design rule: **setdefault 用於提供 default; force overwrite 用於 strict requirement**. 兩者語意差別必明確. **+ Pattern 18(f) Codex claim verify in actual user shell**: claim「cp950 env verified clean」必在 user 真實 shell (PowerShell, not msys bash) 跑驗 — msys bash 的 stdio inheritance 與 Windows PowerShell 不同, 跑通 msys 不代表跑通 PowerShell |
| **R12.8** | **+ P0 sub-rule (e) ULTIMATE: interpreter-startup env vs mid-run env**. R12.7 force overwrite `os.environ['PYTHONUTF8']='1'` 在 module top-level. 但 PEP 540 UTF-8 mode 必在 **interpreter 啟動時** 透過 `python -X utf8` flag 或 startup env 才能 enable; mid-run set `os.environ` 是 no-op for `sys.flags.utf8_mode`. 結果 `locale.getpreferredencoding()` 仍 cp950 → `subprocess.Popen._readerthread` 仍 用 cp950 decode child UTF-8 stdout → **仍 crash** (Codex R12.7 反證: `sys.flags.utf8_mode=0`, `getpreferredencoding=cp950` even after env force overwrite). **核心 lesson**: interpreter-startup-only flags (PEP 540, hash randomization, etc) 必透過 **re-exec self with explicit -X flag** 才能 enable; mid-run env mutation 對這類 flag 完全無效. **修法**: script 最早入口 (在任何 `import` 之前) detect `sys.flags.utf8_mode == 0`, 若 true 則 `os.execv(sys.executable, [sys.executable, "-X", "utf8", *sys.argv])` 重啟 interpreter. Gate by `Path(sys.argv[0]).resolve() == Path(__file__).resolve()` 避免 import 時誤觸發 (test runner / module import). 用 `os.environ['_R12_8_UTF8_REEXEC']='1'` sentinel 防 infinite loop. **+ Pattern 17(j) Codex 反證 evidence chain 確認**: claim「修法 substantive」前必跑出實證 (PowerShell + Tee-Object + Select-String) AND 證明 root-cause 真消失 (e.g. `sys.flags.utf8_mode=1` AND `locale.getpreferredencoding=utf-8` 都對齊期望) — 連 3 輪 (R12.5/R12.6/R12.7) Codex 用 PowerShell 反證 stderr 仍有 Traceback, 連 3 輪 Claude self-verify 用 msys bash 漏網 |
| **R12.9** | **+ P0 sub-rule (e) FINAL: Windows os.execv ≠ POSIX exec — silent exit code lost**. R12.8 用 `os.execv(sys.executable, [...])` 重啟 interpreter. POSIX 上 `execv` 是 in-process replace, child 共用 PID + 直接繼承 stdio + exit code 自然 propagate. 但 **Windows os.execv 是 spawn-and-detach**: parent 立即 exit code 0, child 是 detached process; 任何 child error / ValueError / argparse fail 都被 silent 吞掉 (Codex R12.8 反證: `bad_arg_exit=0` 對應 argparse error, `5yr+skip_exit=0` 對應 ValueError raise — 但 traceback 有 print, exit code 全 0). CI / automation 全會誤判 success. **核心 lesson**: Windows process model 與 POSIX 不同; `os.execv` 在 Windows 是 fire-and-forget spawn, 不是 true exec. **修法**: 改用 `subprocess.run([python, "-X", "utf8", *argv], env=...) → sys.exit(proc.returncode)`. parent block 等 child 完, 然後 explicit propagate exit code. Defense-in-depth: regression test 必驗 child failure exit code (argparse error → 2, ValueError → 1) propagate to parent. **Pro 約定**: 凡需要 propagate child failure 的 re-exec / wrapper script, **禁用 os.execv on Windows**, 必用 subprocess.run + sys.exit |
| **R12.10** | **+ P17(k) Codex caveat → backlog vs immediate fix decision rule**. Codex R12.9 verdict GO-WITH-CAVEATS 留 2 件 caveat: (a) smoke 2 trades / 4 scenario 0-trade pipeline-only proof (b) HMM convergence warnings count 沒 surface to report. Pro 紀律: caveat **不阻 launch** (verdict GO 已給) 但 **必收進 reportable metric** — user 看 5yr 真跑 report 必能看到 HMM warning count + scenario trade count, 不能只在 stderr/log 漂浮. **修法**: HMM convergence count via `logging.Handler` filter "Model is not converging", 寫進 run_meta.json + summary.md "Caveats" section. Caveat (a) smoke trade count 已在 folds.csv (per-scenario n_trades), summary 不重述. **核心 lesson**: GO-WITH-CAVEATS verdict 不是 "ignore caveats"; 是「launch OK 但 measurable caveats 必入 audit trail」. 區分: **blocker P** (NO-GO) vs **caveat metric** (GO + tracked) vs **optional improvement** (no action) |
| **R12.11** | **+ P17(l) Evidence chain 真假驗證 — claim「兩 source 對齊」必跑 cross-check**. R12.10 prompt 我寫「stderr count 對齊 counter count」claim, 但實測 stderr=0 / counter=45 完全不對齊 (Codex R12.10 反證). **根因**: 加 `logging.Handler` 接住 warning 但沒設 `propagate=True`; hmmlearn warning 被 handler 「吃掉」, 不再走 default stderr propagation. **核心 lesson**: claim「A=B」必同時 dump A, B 看真值, 不可只 dump 其中 1 個 + 推論. 修法: claim 兩端對齊前必 cross-check (e.g. `stderr_count=$(grep -c ...) ; counter_count=$(json query) ; assert ==`). **+ P14(f) logging Handler propagate=True 默認紀律**: Python logging 加 custom handler 必明文 set `logger.propagate = True` (default 是 True 但子 logger 加了 handler 可能 silent change 行為; 為了 evidence chain explicit 標明) AND 確保 root logger 至少有一個 StreamHandler 否則 warnings.warn 路由的 record 會 vanish. **+ Strategy NO-GO vs Tooling NO-GO 區分**: Codex R12.10 抓 「strategy 5yr full Sharpe -2.x / HMM 0-1 trades」是 strategy alpha NO-GO, 不是 tooling fix problem. Pro decision: 工程 launch OK (gates pass), strategy 結論 honest 報「Phase 1 IC/Vertical 在 5yr TXO 沒 alpha」也是 valid Phase 1 outcome, 不是 implementation bug 待 fix |
| **R12.12** | **+ P14(g) Subclass-aware isinstance check pitfall**. R12.11 用 `isinstance(h, logging.StreamHandler)` 判斷 root 是否已有 stderr handler. 但 `FileHandler` IS a `StreamHandler` subclass — caller 已 set file-only logging (e.g. `logging.basicConfig(filename=...)`) 時 isinstance check 為 True, script 誤以為「stderr handler 已存在」, warning 仍走不到 stderr. **核心 lesson**: `isinstance(x, ParentClass)` includes ALL subclasses; 若需區分 specific subtype 必額外 check (`type(x) is X` 或 `x.stream is sys.stderr`). 修法: explicit `h.stream in (sys.stderr, sys.stdout)` AND `not isinstance(h, FileHandler)`. **+ P17(m) Skip-if-missing regression test 反 pattern**: R12.11 加 `if not file.exists(): pytest.skip(...)` 依賴 prior smoke 跑出來的 artefact. 不獨立, prior step 不跑就 silent skip → regression value 弱. 修法: unit test 用 stub / mock / tmp_path 簡化 production logic, 不依賴 external artefact. **+ Strategy NO-GO 完整收尾 4 件**: (a) 寫 `docs/phase1_conclusion.md` 正式落地結論 (b) summary.md 「acceptable backlog」字眼改 「NO-GO for paper trading」 (c) 加 `--no-cost-model` flag 提供 cost-free baseline 拆「strategy 沒 alpha」vs「retail friction 壓死」 (d) `run_meta["cost_model_disabled"]` audit trail. **核心 lesson**: 「驗收結論」必落地 doc + flag + audit, 不只口頭. NO-GO verdict 不能停在 stderr/log, 必固化到 reports/ + docs/ |
| **R12.13** | **+ P18(g) prompt 自宣稱 evidence vs 實際行為對齊**. R12.12 prompt 說「import scripts._validate_week6_5yr 後 root 應 add StreamHandler」 — 但 handler add 邏輯在 main() 裡, 不是 module top-level. import-only 後 `root.handlers=[]`. Codex 反證 import-only 沒 handler. **核心 lesson**: prompt 描述 verification step 必對齊真實 code path; 不可推論 module-level 必執行某邏輯, 必看 actual file 哪裡執行. 修法: prompt 把驗證 reference module-level vs main()-level 邏輯分清, 並 actual 跑驗才寫 claim. **+ P14(h) Embedded library use-case 必 cover**. R12.8 P1 re-exec gate 只在 sys.argv[0]==__file__ 時觸發. 若 caller `import scripts.X as m; m.main([...])`, gate skip → utf8_mode=0 / locale=cp950 / subprocess `_readerthread` 仍 cp950 decode → embedded use-case 在 PowerShell 仍 crash. **修法**: main() 入口加 runtime check, 若 utf8_mode=0 AND locale 非 utf-8 → 印警告告 caller 必 CLI 跑 / 設 PYTHONUTF8=1 BEFORE Python startup. 不能 mid-run re-exec (caller process 已 active). **+ P17(n) Caveat-tracking metadata 必同步 primary CSV / JSON / doc**. R12.12 加 `--no-cost-model` flag + `run_meta["cost_model_disabled"]`, 但 scenarios.csv 沒這 col → CSV-only caller (notebooks / dashboards) 看不到. R12.13 加 `cost_model_disabled` per-row col. **核心 lesson**: audit trail 不能只在一個 surface (JSON-only); 必同步 CSV row metadata + summary.md verdict + run_meta JSON 三 surface (Pattern 17(h) 升級). **+ P18(h) Skill changelog 數字 stale 自查**: docs/phase1_conclusion.md 之前寫 「R12.0→R12.11, 445 passed」, 現實 R12.13/447 passed. **修法**: 每輪 audit 收尾 grep 當前 round number / passed count 在 docs/ HANDOFF.md / Codex-Prompt 是否對齊 |

**Pattern 13 第二類 architectural fix** (連 12 輪 audit 連 9 次 skill 升級) **R11.19 + R12.2 觸發升級**:
- ✅ **scripts/audit_doc_drift.py 落地** (5 類 drift detection；R11.19 P3 fix)
- ✅ **R12.2 升級 P0 sub-rule (real-data pipeline 4 件 prerequisite)**：launch script 在 pre-design 必把 lookback / cache coverage / smoke exercises logic / empty CSV header 列入 attacker list — Pattern 0 連 R12.0/R12.1/R12.2 三輪 missed 4-5 件 critical P, 加細則防 regression
- ⏳ pre-commit hook 整合（user 待拍板）
- ⏳ GitHub Actions CI（Phase 2 push remote 後）
