# CLAUDE.md — Options_Trading

本檔為 **Options_Trading repo 專屬** Claude Code 守則。每次新 session 開場載入。

---

## 1. 語言 / 命名慣例

- **回覆繁體中文**；技術術語保留英文（BSM、delta、Iron Condor、option chain）
- **英文專業名詞首次出現附括號中譯**，例：Iron Condor（鐵兀鷹）、implied volatility（隱含波動率）
- **日期**：ISO 8601 `2026-04-24`
- **Greeks**：敘述用希臘字母 Δ Γ Θ ν ρ；code 變數名用英文 `delta` / `gamma` / `theta` / `vega` / `rho`
- **貨幣**：敘述用 `NT$`；code 變數 / 常數用 `TWD`
- **百分比**：敘述用 `%`；表格可選用 `bps`；code 存 decimal（`0.0083` 不是 `0.83`）

---

## 2. Self-Audit SOP — 每次修改 code 都要自審

### 鐵則：**寫 code 前 + 寫完後雙觸發 self-audit**（無例外）

| 觸發點 | 動作 | Skill |
|-------|-----|------|
| **A. 寫 plan / patch / new module 前** | 跑 self-audit Pattern 0 (列 ≥5 attacker tests + 跑 1-2 個攻破測試) | `/self-audit` Trigger A |
| **B. 修 P + 寫 test 後 / 回報「完成」前** | 跑 self-audit Pattern 1-18 全 19 條 | `/self-audit` Trigger B |
| **C. 改 validation/cache/data/schema 層** | 加跑 `/forensic-sweep` 找 sibling pattern | Skill Chain 強制 |
| **D. 寫 plan / 大 milestone** | 加跑 `/multi-perspective` 7+1 personas | Skill Chain 強制 |

**詳細規則見** [.claude/skills/self-audit/SKILL.md](.claude/skills/self-audit/SKILL.md)（19 條 hard checks 分 4 大類：Pre-design / Code / Validation-design / Doc+Discipline）。

### 三分級觸發強度

| 層級 | 觸發條件 | 該跑哪幾條 |
|-----|---------|----------|
| **強觸發** (full audit) | 改邏輯 code: `src/options/*.py` / `src/strategies/*.py` / `src/backtest/*.py` | self-audit 19 條 + Layer 2 options-specific (見下) |
| **弱觸發** (只 Layer 2) | 改 `src/data/*.py` | 跳 self-audit 全 19 條，跑 Layer 2 data integrity gate |
| **跳過** | 改 `tests/*.py` / comment-only / `config/*.py` 純常數 / stub (NotImplementedError + docstring 無邏輯) | 註記「per CLAUDE.md §2 跳過規則」 |

### Layer 2 — Options 數學 reference（來源 [docs/options_math_audit.md](docs/options_math_audit.md)）

- **Put-Call Parity (Merton)**: `C - P ≈ S·e^(-qT) - K·e^(-rT)` (tol 1e-6)
- **Greeks boundary**: call delta ∈ [0, exp(-qT)]; put delta ∈ [-exp(-qT), 0]; gamma ≥ 0; vega ≥ 0; **theta 符號不硬斷** (high q + deep ITM call 可正 theta)，用 finite-difference / `py_vollib` cross-check
- **py_vollib BSM cross-validation**：price `|my - pv| < 1e-8`；**4 單位換算規則**:
  1. delta / gamma：`|my - pv| < 1e-8`（無單位差）
  2. **vega**: py_vollib per 1% → `|my_vega * 0.01 - pv_vega| < 1e-8`
  3. **theta**: py_vollib per-day-calendar-365 → `|my_theta - pv_theta * 365| < 1e-8`（day-count ≠ 252）
  4. **rho**: py_vollib per 1% → `|my_rho * 0.01 - pv_rho| < 1e-8`
- **No-arbitrage bounds (Merton)**: C ≥ max(S·e^(-qT) − K·e^(-rT), 0); P ≥ max(K·e^(-rT) − S·e^(-qT), 0)

### Hard gate 4 件 (R11.14 升級 scope)

回報「完成 / 全綠 / substance」前必跑（**分開跑、scope=`src tests config scripts`、不掃整個 `.` 避免 `.codex_tmp` false fail**）：

```bash
ruff check src tests config scripts
ruff format --check src tests config scripts
mypy src tests config scripts
pytest tests/ -q
```

### Evidence Missing = FAIL

self-audit 結尾**強制** print 19 條 PASS/FAIL 表 + 對應 file:line / cmd output / grep 結果。**無 evidence 自動降級 FAIL**，不准 lip-service。

### 紀律歷史 (連 9 輪 Codex audit 教訓)

> 2026-04-29：原 SOP hook (`.claude/sop/sop-hook.py`) 已刪除（R11.x 實證 hook reminder 對 silent bug 無實質防線）。改靠 `/self-audit` skill manual invoke + CLAUDE.md §10 Codex Follow-up 4 件硬規則 + Codex audit 三層防線。
>
> R11.18 之後 self-audit skill 已升級 19 條 (連 8 次升級)，含 Pattern 0 pre-design attack + Pattern 13 architectural fix trigger + Pattern 17 Hollow PASS detector + Pattern 18 absolute claim + doc drift sweep。**Pattern 13 第二類 architectural fix** (pre-commit hook / CI / Claude Code hooks 自動化) 候選方案待 user 拍板。

---

## 3. 不主動邊界（Plan 核准後）

| 類型 | 可直接做 | 必先問 |
|------|---------|-------|
| Plan 列的所有檔案（stub / placeholder / docstring / `__init__.py`）| ✅ | |
| Plan 列的 memory entries | ✅ | |
| 邏輯 code（任何 `if` / `loop` / 計算）| | ❌ |
| 新 skill / 新 agent | | ❌ |
| Plan 外的 memory | | ❌ |
| Plan 沒列但執行中發現必要的檔案 | | ⚠️ 先問 |

---

## 4. 多角度思考

### 7 核心角色（Claude 主動判斷何時 invoke）

1. **資深量化主管**：架構、portfolio 全貌
2. **策略研究員（Quant Researcher）**：FDR / OOS / bootstrap / PIT / permutation
3. **波動率交易員（Vol Trader）**：IV surface / skew / term structure / IC 實戰
4. **風控長（CRO）**：position sizing / tail risk / margin / loss cap
5. **Buy-side 面試官**（含 quant dev）：code 可展示性 / 架構 / portfolio value
6. **資料工程師**：look-ahead / 資料完整 / schema drift / corporate action
7. **台灣在地 retail trader**：滑點 / 手續費 / 稅金 / 券商限制 / 流動性

### Codex Audit（獨立機制，非視角）

每 milestone 生成 `Codex-Prompt.md` → 外部 LLM independent review。角色為 adversary in good faith，專門 bug 找碴。位置在 SOP Layer 1+2 **之外**。

---

## 5. 誠實補充（固定格式）

技術推薦 / 時間估算 / 架構決策後，固定加此 block：

```markdown
## 誠實補充
- **Time estimate**: <預估時間 + 不確定性 ±% + 最大卡點>
- **Failure modes**: <哪種情況會壞 / 已知 tail risk>
- **Assumption boundary**: <這段 code / 建議所依賴的假設>
```

---

## 6. 回覆結構（情境調整）

| 情境 | 結構 |
|------|------|
| 簡單問答 | 純段落、無 header、≤200 字 |
| 中等決策 | 對照表 + 推薦 + AskUserQuestion |
| 複雜教學 | H2 分段 + 公式 + 範例 + 誠實補充 + AskUserQuestion |
| 實作階段完工匯報 | Diff 清單 + verification 執行結果 + HANDOFF 更新 + 下一步 |

---

## 7. Checkpoint-driven 執行

**每個執行單元結束後，必做 4 步**：

1. **自我 review**：剛剛做的有沒有偏題（對照 plan 原清單 / user intent）
2. **回報做了什麼**：修改 / 新建的檔案清單 + 關鍵 decision
3. **下一步是什麼**：明確說下個單元做什麼
4. **等 user 核可**：**不自動繼續**

**偵測偏題時**：立即停下告知 user，不再對任何檔案修改。

---

## 8. Candidate Skills Roadmap（未建，Phase 1 後期 / Phase 2 再抽）

以下 6 個候選 skill 在**實際出現重複性**後再建，不預先設計：

- `/pricing-check`：跑 BSM vs `py_vollib` + Put-Call parity + Greeks boundary
- `/backtest-ic`：標準 IC 回測 + report（PnL curve、trade log）
- `/chain-sanity`：option chain no-arbitrage + IV sanity
- `/pnl-attribution`：IC PnL 拆解到 delta/gamma/theta/vega/residual
- `/strategy-audit`：策略 Codex-level attack（mutation / edge case）
- `/dual-audit`：兩個實作並列比對

---

## 9. 專案路線圖

| Phase | 時間 | 範圍 | 出口條件 |
|-------|------|------|---------|
| **Phase 1** | 0-6 個月 | IC + Vertical 研究 / TAIFEX 資料 pipeline / 自寫 BSM + Greeks | 回測 OOS Sharpe >1、Codex audit 通過、paper trading 穩定 |
| **Phase 2** | 6-12 個月 | 加 Calendar / Shioaji live broker 整合 / 實盤小額 | 實盤 3 個月 PnL 正、max DD < 15% |

---

## 10. 快速指令

### Env setup

```bash
conda create -c conda-forge --override-channels -n options python=3.12 -y
conda activate options
pip install -r requirements.txt
```

### 測試

```bash
pytest tests/ -v                  # full regression
pytest tests/options/ -v          # options module only
pytest --collect-only             # smoke test (no run)
```

### 開發迴圈（每修 `src/*.py` 邏輯）

1. 改 code → 2. 跑對應 unit test → 3. 跑 full pytest → 4. 填 SOP checklist → 5. 更新 `HANDOFF.md`

### Codex Audit Follow-up 4 件硬規則（R11.4 起，連 5 輪 audit verification 偷工教訓）

**每件 Codex P 修法都必須**：

1. **e2e toy test**：unit test 過 ≠ 系統 OK。修完每件 Codex P 必加 1 條 `test_<P_name>_e2e_*` 走 caller 端到端 path（例：close-gate defer 加 e2e 跑 `engine.run_backtest` 驗 `closed_trades >= 1`，不只測 `should_close` 回 False）。

2. **4 件 verification 全綠**：每輪 audit 結束前必跑 4 件，缺一件不算「全綠」：
   ```bash
   ruff check . && ruff format --check .
   mypy src tests config scripts
   pytest tests/ -q
   python scripts/_dummy_backtest_pipeline_check.py
   ```

3. **Helper caller 路徑驗 file 真實內容**：寫 helper 後必跑 caller 真路徑，看 file（cat / head）真實內容對齊預期 schema。**不准信 docstring** —— 必須看實檔。R11.3 manifest 7-col helper 寫好但 caller 0 newly saved 從沒實跑 → 舊 6-col header 沒被替換 → schema corruption。

4. **Defer / 容錯設計必加 e2e scenario**：任何「return False (defer)」「skip」「fallback to default」「ignore_errors」設計必加 e2e scenario test，驗整個 pipeline 不會 silent stuck / inflate metric / mask data loss。R11.3 close-gate defer unit test 過但 e2e 卡死到 expiry day final_unrealised inflate。

**違反任一件 = 修法不算成立**。Codex audit 只看實證命令輸出，不看 Claude 文字描述。

---

## 11. 檔案角色

| 檔案 | 用途 |
|------|------|
| `CLAUDE.md`（本檔）| Claude 守則 |
| `HANDOFF.md` | 當前 session snapshot（每 session end 覆寫更新）|
| `README.md` | 對外公開介紹 |
| `.claude/skills/self-audit/SKILL.md` | Layer 1 12 條 hard check（合併原 6 步 SOP + 10 條 R11.x pattern） |
| `.claude/skills/multi-perspective/SKILL.md` | 7+1 personas 系統化執行 |
| `.claude/skills/forensic-sweep/SKILL.md` | Cross-interference grep（合併原 SOP Step 3+4） |
| `docs/options_math_audit.md` | Layer 2 options-specific reference（PCP / Greeks / py_vollib / no-arb；從 `.claude/sop/options_sop.md` 改放） |

---

**Last refined**: 2026-04-24（Stage 2 初建）
