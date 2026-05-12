# Black-Scholes-Merton Derivation Notes

> 本檔目的：把 Phase 1 Week 1 Day 1-2 實作 `pricing.py` / `greeks.py` 用到的 BSM-Merton 公式**書面推導 + 數字驗證**，避免 Codex R1-R3 抓到的 4 類 silent bug pattern 重蹈：
> 1. 領域知識死背（公式憑記憶寫錯）
> 2. 未實測就寫文件（理論值 vs 套件實值不對齊）
> 3. 修法無 grep sweep（單位 / 公式分散在多檔不同步）
> 4. 臆測未查證（py_vollib 單位 / day-count basis 用名字猜）
>
> 每章末附 **Python toy example 跑出的數字** 作為 ground truth 錨點。

---

## 1. Black-Scholes PDE 推導（從 Itô + Delta-hedge）

### 1.1 標的價格動態（GBM）

假設標的 $S_t$ 服從幾何布朗運動（geometric Brownian motion）：

```
dS = μ·S·dt + σ·S·dW
```

其中 $W_t$ 是標準 Wiener process，$\mu$ 是 drift，$\sigma$ 是 volatility。

對 $\ln S$ 套 Itô's lemma：

```
d(ln S) = (μ - σ²/2)·dt + σ·dW
```

→ $S_T \mid S_0 \sim \text{LogNormal}\left(\ln S_0 + (\mu - \sigma²/2)T,\ \sigma\sqrt{T}\right)$

### 1.2 Delta-hedge 組合 → BS PDE

設選擇權價值 $V(S, t)$。建構 portfolio $\Pi = V - \Delta·S$（long 1 option, short Δ shares），讓 $d\Pi$ 不含 $dW$ 項：

```
dV = (∂V/∂t)·dt + (∂V/∂S)·dS + (1/2)·(∂²V/∂S²)·(dS)²
   = (∂V/∂t)·dt + (∂V/∂S)·(μS·dt + σS·dW) + (1/2)·σ²S²·(∂²V/∂S²)·dt
```

選 $\Delta = ∂V/∂S$，$dW$ 項消失：

```
d(V - Δ·S) = [∂V/∂t + (1/2)·σ²S²·∂²V/∂S²]·dt
```

無套利下這個無風險組合應賺 $r$：$d\Pi = r·\Pi·dt$，得 **BS PDE**：

```
∂V/∂t + (1/2)·σ²S²·∂²V/∂S² + rS·∂V/∂S - rV = 0
```

### 1.3 邊界條件

- Call: $V(S, T) = \max(S - K, 0)$
- Put: $V(S, T) = \max(K - S, 0)$

解 PDE → BS closed form。

---

## 2. Merton with Continuous Dividend Yield q

### 2.1 為何 TXO 必用 Merton 變體

TXO 標的是 **TAIEX（台灣加權股價指數）**。TAIEX 是 **price index**（價格指數），不是 total-return index：成分股配發現金股利時，個股除息 → 指數除息下跌（指數 ex-dividend drop）。

對指數選擇權持有人：
- Call 持有：股利落入 spot 的 ex-div drop，**不會**轉到選擇權
- Put 持有：股利除息 = 利於 put（spot 跌）

**Merton 1973** 把連續 dividend yield $q$ 加入 PDE：

```
∂V/∂t + (1/2)·σ²S²·∂²V/∂S² + (r - q)·S·∂V/∂S - rV = 0
```

### 2.2 Closed-form 解

對 $S$ 用 forward price $F = S·e^{(r-q)T}$ 換元，PDE 變回標準 BS 形式於 $F$。代回：

```
d1 = [ ln(S/K) + (r - q + σ²/2)·T ] / (σ·√T)
d2 = d1 - σ·√T

Call = S·e^(-qT)·N(d1) - K·e^(-rT)·N(d2)
Put  = K·e^(-rT)·N(-d2) - S·e^(-qT)·N(-d1)
```

### 2.3 Put-Call Parity（Merton form）

從 Call - Put 直接代入：

```
C - P = S·e^(-qT)·[N(d1) + N(-d1)] - K·e^(-rT)·[N(d2) + N(-d2)]
      = S·e^(-qT) - K·e^(-rT)
```

注意 $N(d1) + N(-d1) = 1$。

### 2.4 q=0 vs q>0 的偏差量化

**Toy verify**（Day 2 Pre-flight 跑過）：

| 案例 | S | K | T | r | q | sigma | Call (no q, BSM) | Call (Merton) | 偏差 |
|------|---|---|---|---|---|------|---------------|--------------|----|
| ATM TXO 30-DTE | 16800 | 16800 | 30/365 | 1.5% | 3.5% | 20% | 394.44 | 369.83 | **−24.61 (-6.2%)** |

→ 用 q=0 BSM 算 TXO ATM call 會 **systematically over-price ~6%**。R1 抓到的 BSM 漏 q **是 P1 真錯誤**。

---

## 3. 5 Greeks 封閉解 + 單位約定

### 3.1 公式（Merton form）

| Greek | Call | Put |
|-------|------|-----|
| Δ (delta) | $e^{-qT}·N(d_1)$ | $e^{-qT}·[N(d_1) - 1]$ |
| Γ (gamma) | $\dfrac{e^{-qT}·\varphi(d_1)}{S·\sigma·\sqrt{T}}$ | 同 call |
| ν (vega) | $S·e^{-qT}·\varphi(d_1)·\sqrt{T}$ | 同 call |
| Θ (theta) | $-\dfrac{S·e^{-qT}·\varphi(d_1)·\sigma}{2\sqrt{T}} - r·K·e^{-rT}·N(d_2) + q·S·e^{-qT}·N(d_1)$ | $-\dfrac{S·e^{-qT}·\varphi(d_1)·\sigma}{2\sqrt{T}} + r·K·e^{-rT}·N(-d_2) - q·S·e^{-qT}·N(-d_1)$ |
| ρ (rho) | $K·T·e^{-rT}·N(d_2)$ | $-K·T·e^{-rT}·N(-d_2)$ |

其中 $\varphi$ 是標準常態 PDF。

### 3.2 單位約定（**本專案 vs py_vollib 對照**）

| Greek | 本專案 (`src/options/greeks.py`) | py_vollib (`black_scholes_merton.greeks.analytical`) |
|-------|-----------------------------|--------------------------------------------------|
| delta | per 1.0 spot 變動 | 同 |
| gamma | per 1.0 spot 變動 squared | 同 |
| theta | **per-year calendar** | per-day-calendar-365 → 換算 `* 365` |
| vega | **per 1.0 sigma** 變動 | per 1% sigma 變動 → 換算 `* 0.01` |
| rho | **per 1.0 rate** 變動 | per 1% rate 變動 → 換算 `* 0.01` |

**為何選 per 1.0**：
- Mathematical 一致性（delta 沒人用 per 1%）
- 內部 chain rule（如 IV solver Newton step）直接用，不需 0.01 scale
- 跨套件比較時，乘上換算因子比除回去直覺

**Retail / trader 直覺場景**：呼叫 `vega(...) * 0.01` 即得 per-1% 值，給人類讀。

### 3.3 Toy verify（2026-04-25 Day 2 實測）

```
S=K=100, T=30/365, r=1.5%, q=3.5%, sigma=20%, ATM call

Greek      | my (per 1.0)        | py_vollib (raw)     | unit-converted equality
-----------|--------------------|--------------------|------------------------
delta      | 0.4985637107       | 0.4985637107       | direct ✓ (diff 0)
gamma      | 0.0693771627       | 0.0693771627       | direct ✓ (diff 1.4e-17)
vega       | 11.4044651039      | 0.1140446510       | my * 0.01 == pv ✓ (diff 0)
theta      | -12.8452850000     | -0.0351925616      | my == pv * 365 ✓ (diff 0)
rho        | 3.9168493048       | 0.0391684930       | my * 0.01 == pv ✓ (diff 0)
```

**這個 toy 直接定義了 cross-check tol 1e-8 的可行性**。

---

## 4. Theta Per-year vs Per-day + Day-count 0.93% 疑點解決

### 4.1 Codex R3 疑點

R3 audit report 指出：
- closed-form theta = my (per-year) = -12.85
- `py_vollib_theta * 365` = -12.85 ← match
- backward fd `(BSM(T-dt) - BSM(T))/dt` with dt=1/365 = -12.96 ← **差 0.93%**

懷疑 py_vollib 用 252 trading days。

### 4.2 實測解決

**Day 2 toy verify**：
```
pv_theta * 365 = -12.8453   ← my closed-form perfect match (diff 0)
pv_theta * 252 = -8.8685    ← 大幅偏離 (diff 3.98)
fd (dt=1/365)  = -12.9638   ← 跟 closed-form 差 0.93%
```

**結論**：py_vollib 是 **per-day-calendar-365**，不是 252。Codex R3 的 0.93% 不是 day-count 問題，是 **finite-difference 一階離散誤差**：

```
backward FD ≈ ∂V/∂t + O(dt)
```

當 dt = 1/365 ≈ 0.00274，O(dt) 帶來 ~1% 誤差是預期。要更精準需 **central difference**：

```
central FD = (BSM(T+dt) - BSM(T-dt)) / (2·dt) ≈ ∂V/∂t + O(dt²)
```

→ Phase 1 暫不改 fd 公式（test 用 backward + tol 2% 接受）；Phase 2 sensitivity test 若需 1e-4 級精度再改 central。

### 4.3 SOP tolerance 修正

```
my closed-form theta vs pv_theta * 365     : 1e-8 ground truth
my closed-form theta vs backward fd (dt=1/365): 2% (O(dt) 一階誤差)
my closed-form theta vs central fd (dt=1/365) : 1e-4（若改 central）
```

詳見 `docs/options_math_audit.md` Layer 2 Check 2。

---

## 5. IV Solver Newton-Raphson 起始值 + 收斂保證

### 5.1 Brenner-Subrahmanyam ATM Approximation

對 ATM call（S = K），price ≈ $0.4·S·\sigma·\sqrt{T}$（Brenner-Subrahmanyam 1988）。逆推：

```
sigma_0 ≈ price / (0.4 · S · sqrt(T)) ≈ sqrt(2π/T) · price / S
```

**Day 1 實作用此公式為 Newton-Raphson 起始值**。

### 5.2 Newton-Raphson 迭代

設目標 $f(\sigma) = \text{BSM}(\sigma) - \text{price}_{\text{market}}$，找 root：

```
σ_{n+1} = σ_n - f(σ_n) / f'(σ_n)
        = σ_n - (BSM(σ_n) - price) / vega(σ_n)
```

**收斂條件**：
- $|f(\sigma_n)| < 10^{-8}$
- iterations < 100

### 5.3 Brent fallback 何時觸發

Newton-Raphson 失效情境：
1. **Vega → 0**（deep OTM/ITM）：分母小，迭代爆炸
2. **σ → 0 or σ → ∞**：超出合理 IV 範圍

→ fallback `scipy.optimize.brentq` 在 $[10^{-6}, 5.0]$ bracket 上 bisection。Brent 保證收斂（root 必在 bracket 內），但慢。

### 5.4 已知限制（Self-Attack 紀錄，避免 silent bug）

- Brenner-Subrahmanyam 對 deep OTM 不準（適用 ATM）→ 起始值偏差大但 Newton 仍能收斂；Phase 2 可考慮 Manaster-Koehler 1982
- Brent bracket 上限 5.0 對黑天鵝日 0DTE 可能不夠 → 罕見情境，Phase 1 視為 known limit

詳見 `pricing.py::implied_vol` docstring。

---

## 6. 進一步閱讀

- Hull, *Options, Futures, and Other Derivatives* Ch 15-19
- Merton, R.C. (1973). "Theory of Rational Option Pricing"
- Brenner & Subrahmanyam (1988). "A Simple Formula to Compute the Implied Standard Deviation"
- py_vollib source: `https://github.com/vollib/py_vollib`

---

**Last updated**: 2026-04-25 (Phase 1 Week 1 Day 2)
