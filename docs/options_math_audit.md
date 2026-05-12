# Options-Specific SOP Checklist (Layer 2)

本檔是專案 self-audit SOP 的 **Layer 2**：在通用 code-review hard check（Layer 1）之上，加上**期權研究專屬**的 silent-bug 守門員（公式 + 邊界 reference，非 procedural）。每次改動 `src/options/` 或 `src/strategies/` 的數學邏輯，commit 前逐條對照。

---

## 三分級觸發（摘自 CLAUDE.md §2）

| 級別 | 檔案 / 修法 | 執行內容 |
|------|------------|---------|
| 強 | `src/options/*.py`、`src/strategies/*.py`、`src/backtest/*.py` 邏輯 code | **Layer 1（6 步）+ Layer 2（下列 4 項）全做** |
| 弱 | `src/data/*.py` | 只做 **Layer 2（下列 4 項）** |
| 跳過 | `tests/*.py`、comment-only、`config/*.py` 純常數、新建 stub（`NotImplementedError` + docstring）| 不強制 |

---

## Layer 2 Check 1 — Put-Call Parity（Merton form）

對每個 `(spot S, strike K, expiry T, rate r, dividend yield q)` 組合，驗證：

```
C - P ≈ S · e^(-qT) - K · e^(-rT)
```

- Tolerance：`1e-6`
- 三組典型驗算：ATM / ITM 5% / OTM 5%
- 若差異 > tolerance → pricing 或 Greeks 定有 bug，**不可繼續**
- **注意**：常見教科書寫 `C - P = S - K·e^(-rT)` 假設 q=0。TXO 標的 TAIEX 是價格指數有股利，必須用上方 Merton 版。

**實作位置**：`tests/options/test_pricing.py::test_put_call_parity`

---

## Layer 2 Check 2 — Greeks Boundary

對 random sample of 100 options（涵蓋 ITM/ATM/OTM × Call/Put × 多 DTE），斷言：

- `0 ≤ call_delta ≤ exp(-qT)`（Merton 上界，q=0 時退化為 1）
- `-exp(-qT) ≤ put_delta ≤ 0`
- `gamma ≥ 0`（永遠）
- `vega ≥ 0`（永遠）

**theta 不硬斷符號**：Merton with dividend yield 下，deep ITM call 在 high q 情境可能出現**正 theta**（dividend benefit > 時間耗損）。所以 `theta ≤ 0` 不能當 invariant。改用：

**本專案 theta 定義 = per-year calendar theta**（greeks.py docstring：`per-year rate; divide by 365 for per-calendar-day decay`）。所有驗證都以 per-year 為基準。

- **Finite-difference 驗證**（per-year）：`fd_theta = (BSM(T - dt) - BSM(T)) / dt`，dt = 1/365；驗 `|my_theta - fd_theta| < 1e-4`
  - 直觀：T 縮短一天，價值若下降 → BSM(T-dt) < BSM(T) → fd_theta < 0（時間耗損 → 負）
  - **不要寫 `-(BSM(T-dt) - BSM(T))/dt`**：Codex R3 抓到我之前寫反了，符號會顛倒
- **`py_vollib.black_scholes_merton.greeks.analytical.theta` cross-check**：
  - 簽章：`theta(flag, S, K, t, r, sigma, q)` — **2026-04-25 實測確認**
  - **Day-count 解決（2026-04-25 Day 2 toy verify）**：`py_vollib.theta` 是 **per-day-calendar-365**。
    closed-form per-year vs `pv_theta * 365` **完全匹配**（diff = 0）。
    Codex R3 的 0.93% 差距是 **finite-difference 一階離散誤差**（O(dt)），**非 day-count 問題**。
  - **Cross-check tolerance**：closed-form theta vs `pv_theta * 365` **`< 1e-8`**（嚴格 ground truth）
  - **fd vs closed-form tolerance**：**`< 1e-2`**（fd 本身一階誤差約 1%；要更精準須用 central difference）

### Vega / Rho 單位（2026-04-25 Day 2 toy verify 抓到的單位 trap）

**`py_vollib.vega` 和 `py_vollib.rho` 是 per 1% sigma / per 1% rate**，**不是 per 1.0**。

- closed-form vega（mathematical）：`S·e^(-qT)·φ(d1)·√T`，per 1.0 sigma 變動
- `py_vollib.vega` 回傳 per 0.01 sigma 變動（即 per 1%）
- 實測：closed-form = 11.40, pv = 0.1140 → **差 100x**

**本專案約定**：
- Greeks 函式**回傳 per 1.0**（mathematical 一致性 + 內部 chain rule 直接用）
- Cross-check 用 `|my_vega * 0.01 - pv_vega| < 1e-8`（單位換算後比較）
- 若 retail / trader 想看 per 1% 直覺值，呼叫 `vega(...) * 0.01`

同樣規則套用 rho。delta / gamma 不受影響（沒 per-% 慣例分歧）。

### 其他 py_vollib API 實測簽章（避免 typo）

```python
from py_vollib.black_scholes_merton import black_scholes_merton
# (flag, S, K, t, r, sigma, q)

from py_vollib.black_scholes_merton.greeks.analytical import delta, gamma, theta, vega, rho
# 全部: (flag, S, K, t, r, sigma, q)
# 單位: delta / gamma per 1.0；vega per 1% sigma；theta per-day-calendar-365；rho per 1% rate

from py_vollib.black_scholes_merton.implied_volatility import implied_volatility
# (price, S, K, t, r, q, flag)  ← ⚠️ 順序不同！q 在 flag 前
```

delta / gamma / vega 任一違反邊界 = Greeks 實作有符號錯誤或公式錯誤。

**實作位置**：`tests/options/test_greeks.py::test_greeks_boundaries`、`tests/options/test_greeks.py::test_theta_finite_difference`

---

## Layer 2 Check 3 — `py_vollib` Cross-Validation（Merton 變體）

每次改 `src/options/pricing.py` 或 `src/options/greeks.py`，**強制**：

```python
from py_vollib.black_scholes_merton import black_scholes_merton as pv_bsm_m
from src.options.pricing import bsm_price

assert abs(
    bsm_price(S, K, T, r, q, sigma, 'call') - pv_bsm_m('c', S, K, T, r, sigma, q)
) < 1e-8
```

差異 ≥ `1e-8` → 自寫實作偏離 industry reference，必修。

**重要**：**絕不**用 `py_vollib.black_scholes`（無股利版）驗證 TXO pricing。那會讓有 bug 的
無 q 實作「自洽地」通過 cross-check。必須用 `black_scholes_merton` 子模組。

**實作位置**：`tests/options/test_pricing.py::test_bsm_matches_py_vollib`

---

## Layer 2 Check 4 — No-Arbitrage Bounds（Merton form）

對每個定價，驗證：

- Call price：`C ≥ max(S · e^(-qT) - K · e^(-rT), 0)`（Merton intrinsic 下界）
- Put price：`P ≥ max(K · e^(-rT) - S · e^(-qT), 0)`
- Upper bound：Call `≤ S · e^(-qT)`；Put `≤ K · e^(-rT)`

違反 = pricing 允許套利，嚴重 bug。**注意**：常見教科書寫不含 `e^(-qT)` 假設 q=0；TXO 有股利必須用上方 Merton 版，否則 ATM IC 會誤判為違反套利。

**實作位置**：`tests/options/test_pricing.py::test_no_arbitrage_bounds`

---

## 最終判定（配合 Layer 1 template）

| 情境 | 判定 |
|------|------|
| Layer 1 全做 + Layer 2 四項全過 | **強觸發修法完備** |
| Layer 2 四項全過（data 層修法）| **弱觸發修法完備** |
| 任一 Layer 2 fail | **不可回報**；先修再重跑 |
| 跳過情境 | 在回覆中註記「per CLAUDE.md §2 跳過規則」 |

---

## 使用提醒

- Layer 2 四項 check **應實作為 pytest test**，不是口頭敘述
- 強觸發時 Layer 2 跑 **在** Layer 1 Step 6（full pytest）**內**即完成 — 不是額外 step
- 若 Layer 2 test 尚未建（例如 stage 早期）→ 註記 `TODO: Layer 2 Check X pending implementation` 並在對應 PR 同時補
