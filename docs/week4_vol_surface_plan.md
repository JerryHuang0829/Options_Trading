# Week 4-5 Vol Surface Plan v1 (D-soft pivot — 為 Week 6+ 真 backtest 準備 mark machinery)

> **Status**: 草稿 v1，待 user 拍板 + Codex R11.6 audit
> **Phase**: Phase 1 Week 4-5（D-soft pivot 提前自原 Week 5-6）
> **Baseline**: 262 tests pass + 8yr 1963-shard 真 cache + R11.5 GO-WITH-CAVEATS
> **Out of scope (Week 6+)**: 真 5yr backtest、Greeks PnL attribution、滑點敏感性

---

## §0 為什麼 Week 4-5 必須做這個

Codex R10.12 實證：2024 全年 100% 天 fallback rate ≥ 20%（mean 60%）。意味著：
- TXO 真 chain 平均**每天 60% rows 沒 bid/ask**（deep OTM / illiquid expiry）
- 用 settle fallback 跑 5yr backtest → Sharpe 被 stale-mark dominated → 結論本質不可發表

**Pro 量化標準**：先建 vol surface (SVI/SABR) 把 60% NaN rows 用 model price 補滿 → 100% markable → Week 6+ 才有資格跑可發表 Sharpe。

---

## §1 R11 系列 Codex 累積的 5 件 Week 4 prerequisite

R11 / R11.1 / R11.2 / R11.3 / R11.4 / R11.5 連 6 輪 audit 累積點過的 prerequisite，本 plan 全 cover：

| # | Prerequisite | Codex 點出輪次 | 本 plan 對應 |
|---|-------------|---------------|------------|
| 1 | SVI fit 1963 days × 8-12 expiries × 30+ strikes **perf 預估** | R11.1 | §3 Day 5 multiprocessing batch + perf 預估表 |
| 2 | **Arbitrage-free SVI constraint** | R11.1 | §2 Day 1 Lee 2004 constraints + butterfly arb 守線 |
| 3 | **Surface cache schema versioning** | R11.1 | §3 Day 4 `vol_surface_cache.py` schema_version=v1 metadata |
| 4 | 3-tier fallback (SVI → SABR → polynomial) **silent reason audit** | R11.1 | §2 Day 3 + §4 Day 1 加 `model_type` col + per-day audit |
| 5 | enrich 1-day 1.25s × 1963 = **41 min serial** | R11.4 | §3 Day 5 multiprocessing per-day parallel; enrich 也順手 vectorize |

---

## §2 Phase 1：數學模型（Week 4 Day 1-3）

### Day 1：SVI raw form 5-param fit + Arbitrage-free constraints

**新建** [src/options/vol_surface.py](../src/options/vol_surface.py)

```python
def fit_svi_raw(
    log_moneyness: np.ndarray,    # k = ln(K/F), shape (N,)
    total_var: np.ndarray,         # w = sigma^2 * T, shape (N,)
    *,
    arb_free: bool = True,
    initial_guess: dict | None = None,
) -> SVIFitResult:
    """SVI raw form per (date, expiry):
        w(k) = a + b{ρ(k-m) + sqrt((k-m)² + σ²)}

    5 params: a, b, ρ, m, σ.
    Arb-free constraints (Lee 2004):
      - butterfly: g(k) = (1 - k·w'(k)/(2w(k)))² - w'(k)²/4·(1/w(k) + 1/4)
                   + w''(k)/2 ≥ 0
      - calendar: w(k, T2) ≥ w(k, T1) for T2 > T1 (cross-expiry, 不在 single fit 範圍)
      - 0 ≤ b ≤ 4/(T·(1+|ρ|))
      - σ ≥ 0
    """
```

**Tests**：5 條（一般 fit 收斂 / arb-free 約束生效 / non-finite 拒絕 / spread-weighted IV RMSE < threshold / boundary 0-strike）。

### Day 1.5：Fit Universe 規則（Codex R11.6 P2 修法）

**新加**到 `fit_with_fallback` 內 + 文件化 quote-filtering 契約：

```python
def filter_fit_universe(
    chain_today: pd.DataFrame,    # 含 strike / option_type / bid / ask / volume / open_interest
    *,
    min_volume: int = 10,                 # volume gate
    min_open_interest: int = 50,          # OI gate
    max_spread_pct_of_mid: float = 0.50,  # spread cap (deep OTM 放寬到 0.5)
    moneyness_band: tuple[float, float] = (-0.30, 0.30),  # log-moneyness ±30%
    min_dte: int = 3,
    max_dte: int = 90,
    use_otm_only: bool = True,            # OTM call + OTM put (避開 ITM 的 ITM 部分受 dividend / rate 影響)
) -> pd.DataFrame:
    """Production-grade fit universe filter (Codex R11.6 P2):

    過濾規則 (依序套用):
      1. bid > 0 AND ask > 0 (mid > 0 強制)
      2. spread_pct = (ask - bid) / mid < max_spread_pct_of_mid (排 wide-spread noise)
      3. volume >= min_volume OR open_interest >= min_open_interest (流動性 gate)
      4. log-moneyness ∈ moneyness_band (排 deep wing 不可信 quote)
      5. dte ∈ [min_dte, max_dte] (排 near-expiry / 太遠到期)
      6. use_otm_only=True 時:
         - call: strike > forward
         - put:  strike < forward
         (合併成單一 OTM smile, 避開 ITM 部分)

    Returns: filtered DataFrame; 若 < 6 rows → caller (fit_with_fallback) 退 SABR 或 poly.
    """
```

**Pro 量化標準**：smile fit 必須有 quality gate；不能把 zero-bid / wide-spread quote 灌進去 fit，會把 noise 當訊號。

**Tests**：5 條（zero-bid 排除 / wide-spread 排除 / volume gate / moneyness band / OTM-only call+put 合併）。

### Day 2：SABR 4-param fallback

```python
def fit_sabr(
    strikes: np.ndarray, ivs: np.ndarray, *,
    forward: float, T: float,
    beta: float = 1.0,    # fix β=1 for index option (lognormal)
) -> SABRFitResult:
    """SABR Hagan 2002 lognormal expansion. 3 free params (α, ρ, ν) when β=1.
    Used when SVI fit fails to converge."""
```

**Tests**：4 條（一般 fit / β=1 lognormal / negative ν 拒絕 / synthetic OOS）。

### Day 3：polynomial degree-2 smile + 3-tier orchestration

```python
def fit_smile_polynomial(
    log_moneyness: np.ndarray, ivs: np.ndarray,
) -> PolyFitResult:
    """σ(k) = a + b·k + c·k² 純 in-sample. 最後 backup."""

def fit_with_fallback(
    *, log_moneyness, ivs, forward, T,
    arb_free: bool = True,
) -> SmileFitResult:
    """3-tier orchestration:
      1. Try SVI raw form (with arb-free constraints if enabled)
      2. SVI fail → try SABR (β=1)
      3. SABR fail → degree-2 polynomial
    Returns SmileFitResult with model_type ∈ {'svi', 'sabr', 'poly'},
    R² (in-sample), converged flag, fit_time_ms.
    """
```

**Audit (prerequisite #4)**：每筆 fit 紀錄 `model_type` + `converged` + `R²` + `fit_time_ms` → 寫進 surface cache + summary stats（per Phase, 多少 % 走 svi / sabr / poly）。

**Tests**：6 條（3-tier orchestration / model_type 正確標 / R² 對齊 fit 品質 / 全 fail raise / fit_time_ms 紀錄 / cross-validate 三 model 順序）。

---

## §3 Phase 2：Caching + Performance（Week 4 Day 4-5）

### Day 4：Surface cache（prerequisite #3）

**新建** [src/data/vol_surface_cache.py](../src/data/vol_surface_cache.py)

```
data/taifex_cache/vol_surface/<YYYY-MM-DD>.parquet
```

**Schema (v1)**：
| col | dtype | meaning |
|-----|-------|---------|
| date | datetime64[ns] | trading date |
| expiry | datetime64[ns] | smile expiry date |
| model_type | string | 'svi' / 'sabr' / 'poly' |
| params_json | string | JSON-serialized params dict |
| r_squared | float64 | in-sample R² |
| converged | bool | optimizer converged |
| fit_time_ms | int64 | fit wall-clock ms |
| schema_version | string | 'v1' |

**API**：
```python
def save_surface(date, surfaces: list[SmileFitResult], cache_dir): ...
def load_surface(date, cache_dir) -> dict[expiry, SmileFitResult]: ...
def list_cached_surface_dates(cache_dir) -> list[str]: ...
```

**Tests**：8 條（save/load round-trip / schema_version metadata / drift raise / multi-expiry / overwrite / atomic write / list_cached / load missing date 回 empty）。

### Day 5：Batch fit + Multiprocessing（prerequisite #1, #5）

**新建** [scripts/fit_vol_surface_batch.py](../scripts/fit_vol_surface_batch.py)

```bash
python scripts/fit_vol_surface_batch.py \
    --start 2018-04-01 --end 2026-04-28 \
    --workers 8 \
    --skip-cached \
    --dry-run             # 估時間不真寫
```

**Perf 預估表**（prerequisite #1）：

| 參數 | 估 |
|------|-----|
| 1 day × 1 expiry × 30 strikes SVI fit | 50-100 ms |
| 1 day × 8 expiries 全 fit serial | 0.4-0.8 sec |
| 1963 days × 8 expiries serial | 13-26 min |
| 1963 days × 8 workers parallel | **2-4 min** ⭐ |

**接受門檻**：8-worker parallel < 10 min；序列 > 30 min 不接受。

**enrich vectorize（prerequisite #5）**：[src/data/enrich.py](../src/data/enrich.py) `add_iv_per_strike` 維持 numpy column access，但 IV solver 改 batch — 對同 (date, expiry) 一群 strikes 一次解（共用 d1/d2 base computation）。

**Tests**：5 條（batch fit 1-day full pipeline / multiprocessing 4-worker correctness / skip-cached 重跑 idempotent / dry-run 估時間 / fail 1 day 不影響其他 day）。

---

## §4 Phase 3：Enrich + Mark policy 整合（Week 5 Day 1-3）

### Day 1：enrich.add_model_price（prerequisite #4 落地）

```python
def add_model_price(
    df: pd.DataFrame,    # ENGINE_REQUIRED 13-col + iv 已在
    surface_cache_dir: str,
    *,
    r: float = RISK_FREE_RATE_DEFAULT,
    q_source: Literal["fallback", "pit"] = "fallback",
) -> pd.DataFrame:
    """對 NaN bid/ask row 用 surface 反算 BSM model price.

    Adds cols:
      - model_price: float64 (NaN if surface fit not available for that date)
      - model_price_source: 'svi' / 'sabr' / 'poly' / 'mid' (mid = no fallback used)
    """
```

**Tests**：6 條（基本 reverse-bsm correctness / NaN bid/ask 用 surface / 真 mid 不蓋 / surface cache miss → NaN model_price / multi-expiry / cross-check vs Day 5 IV round-trip）。

### Day 2：portfolio.mark_policy 加 `mid_with_surface_fallback`

[src/backtest/portfolio.py](../src/backtest/portfolio.py) 修 `_mid_price_with_basis`：

```python
# Codex R11.6 P2 修法：source priority 統一單一語意
# mid → model_price → settle (settle 永遠最後，因為是 stale fair value)
# audit 三欄分開記不混 (fallback_model_rate / fallback_settle_rate)
if mark_policy == "mid_with_surface_fallback":
    if pd.notna(bid) and pd.notna(ask):
        return (bid + ask) / 2.0, "mid"
    if pd.notna(model_price) and model_price > 0:
        return model_price, f"model_{model_price_source}"
    if pd.notna(settle) and settle > 0:
        return settle, "settle"
    raise ValueError("no price source available")
```

**Audit metric (R11.6 P2 三欄分離)**：
- `n_fallback_model` / `fallback_model_rate` (走 surface model_price 的)
- `n_fallback_settle` / `fallback_settle_rate` (走 settle 的，stale-mark 警告)
- `n_legs_marked` (總 leg 數)

**Tests**：8 條（mid 優先 / model 優先 settle / NaN model + 有 settle → 退 settle / 三 source 都 NaN raise / 三 audit 欄完整 / strict_mid 不變 / mid_with_settle_fallback 不變 / invalid policy raise）。

### Day 3：engine.run_backtest 接新 policy

[src/backtest/engine.py](../src/backtest/engine.py) `mark_audit` DataFrame 加：
- `n_fallback_surface` (int64)
- `fallback_surface_rate` (float64)

R10.12 a 修法 mark_policy 三 forward sites（pre_open / eod / final）守住。

**Tests**：3 條（mark_audit 新欄齊 / 跨三 mark_policy diff / e2e 用 surface mark 跑 dummy chain pass）。

---

## §5 Phase 4：1-year sub-set 真 backtest 驗證（Week 5 Day 4-5）

### Day 4：跑 2024 sub-set IC 三 mark_policy 對比

```bash
python scripts/run_taifex_ic_subset_2024.py \
    --start 2024-01-01 --end 2024-12-31 \
    --mark-policy strict_mid mid_with_settle_fallback mid_with_surface_fallback \
    --output outputs/2024_three_policy_comparison.csv
```

**期望結果**：
| mark_policy | 預期行為 |
|------------|---------|
| strict_mid | 跑不到 2 天 raise（60% NaN bid/ask） |
| mid_with_settle_fallback (R10.11) | 跑通但 Sharpe 被 stale-mark 污染 |
| **mid_with_surface_fallback** | 跑通 + 通過下面 Pro 驗收矩陣 |

**Pro 驗收矩陣（Codex R11.6 P2 修法 — 廢「diff < 30% 唯一 gate」）**：

主要 gate（**全 pass 才算通過**）：
1. **Spread-weighted IV RMSE** per (date, expiry) < 0.05 vol point（OTM strikes，weight = 1/spread）
2. **Bid/Ask band violation rate** < 5%（surface model_price 不能在 5% 行落到 bid/ask 範圍外）
3. **Butterfly arbitrage grid check**：surface 算的 risk-neutral density f(K) ≥ 0 across full strike grid
4. **Calendar arbitrage check**：w(k, T2) ≥ w(k, T1) for T2 > T1（cross-expiry total var monotonic）
5. **Holdout-by-strike RMSE**：每筆 fit 留 20% strikes OOS，OOS RMSE < 1.5 × in-sample RMSE
6. **Fallback model_type 分布**：SVI 收斂率 ≥ 60%，SABR 不超 30%，poly 不超 10%（poly 多代表 fit pipeline 有問題）

次要 sanity check（**參考用，非 hard gate**）：
- mid_with_surface_fallback Sharpe vs mid_with_settle_fallback Sharpe diff（之前的 30% 改 secondary）
- 邏輯：diff 小代表 surface 沒大幅改變結果（reasonable）；diff 大可能 surface 真的更乾淨也可能 silent bug → 需主要 gate 1-6 已 pass 才能信 secondary

**驗收行動**：
- 主要 gate 1-6 全 pass → mark machinery 過審 → Week 6+ 真 backtest GO
- 任一主要 gate fail → 重 fit / 調 quote universe / 退 SABR β=0.5 / 重審 plan
- secondary diff > 50% **不再** auto-fail；改成「flag 進 audit log + 走 main gate 判」

### Day 5：HANDOFF 更新 + Codex R12 audit

- HANDOFF.md 改 Week 4-5 完工 snapshot + Week 6+ 真 backtest 起手
- 新 Codex prompt R12（cross-week 第二輪，覆蓋 Week 1+2+3+4-5 全部）
- 重點審 vol surface fit 品質 + arb-free 真實守線 + 三 model fallback 比例 + 1-year sub-set 三 policy diff

---

## §6 Tests + Verification 矩陣

| 模組 | 預估 tests |
|------|-----------|
| `tests/options/test_vol_surface.py` | 15 (SVI fit / SABR / poly / arb-free / 3-tier orchestration / model_type audit) |
| `tests/data/test_vol_surface_cache.py` | 8 (schema_version / save / load / drift raise / multi-expiry / overwrite / atomic / list_cached) |
| `tests/data/test_enrich_model_price.py` | 6 (add_model_price / NaN fill / source col / cache miss → NaN / cross-validate IV / multi-expiry) |
| `tests/backtest/test_portfolio_surface_fallback.py` | 6 (mark_policy 新值 / audit metric 三欄 / source priority / 三 source NaN raise / strict_mid 不變 / invalid raise) |
| `tests/integration/test_vol_surface_e2e.py` | 5 (1-day batch fit / mark_audit fallback_surface_rate / 三 mark_policy diff < 30% on synthetic / multiprocessing 4-worker correctness / skip-cached idempotent) |
| **小計** | **40** |

baseline **262 → 302 pass**（+40）。

---

## §7 不動（R5 P1 紅線守住）

- `src/options/{pricing, greeks, chain}.py`
- `src/data/{schema, taifex_loader, cache, synthetic}.py` 主結構
- `src/strategies/{base, iron_condor, vertical}.py`
- `src/backtest/{execution, metrics}.py`
- `src/risk/limits.py`
- `config/constants.py`
- 既有 262 tests 全部不可動

---

## §8 Verification 出口條件（Week 5 Day 5）

```bash
# 1. 4 件硬規則（CLAUDE.md §10）
ruff check . && ruff format --check .
mypy src tests config scripts
pytest tests/ -q                                            # 預期 ~302 pass
python scripts/_dummy_backtest_pipeline_check.py

# 2. SVI fit reference
python -c "
from src.options.vol_surface import fit_with_fallback
import numpy as np
log_k = np.linspace(-0.2, 0.2, 30)
ivs = 0.20 + 0.05 * log_k**2  # synthetic V-shape
result = fit_with_fallback(log_moneyness=log_k, ivs=ivs, forward=17500.0, T=30/365)
print(f'model_type={result.model_type}, R²={result.r_squared:.4f}, converged={result.converged}')
"

# 3. Batch fit dry-run perf 估
python scripts/fit_vol_surface_batch.py --dry-run

# 4. 1-year sub-set Pro 驗收矩陣 (R11.6 P2)
python scripts/run_taifex_ic_subset_2024.py --output outputs/2024_pro_validation_matrix.csv
cat outputs/2024_pro_validation_matrix.csv | head -10
# 預期主要 gate 1-6 全 pass:
#   - spread-weighted IV RMSE < 0.05
#   - bid/ask band violation < 5%
#   - butterfly arb-free across grid
#   - calendar arb-free cross-expiry
#   - holdout-by-strike OOS RMSE < 1.5x in-sample
#   - SVI 收斂 ≥60% / SABR ≤30% / poly ≤10%
# secondary diff Sharpe 為 audit log 不再 hard gate
```

---

## §9 Risk Register

| 風險 | 影響 | 機率 | 緩解 |
|------|------|------|------|
| SVI 對 TXO 真資料 fit RMSE 太高 | 大量退 SABR / polynomial | 中 | 3-tier 兜底；spread-IV-RMSE gate |
| arb-free 約束太嚴 → 收斂率低 | 大量退 SABR | 中-高 | 第一 pass 試 arb_free=True; 不過再 arb_free=False relax |
| multiprocessing 速度不線性 | batch fit > 10 min | 中 | 接受；後續 numba/cython 優化 |
| 1-year sub-set surface mark 仍污染 | secondary diff > 50% | 低-中 | 主要 gate 1-6 pass 為準；secondary 改 audit log |
| Lee 2004 SVI 不適用 short-DTE | 短 DTE expiry fit 散 | 中 | DTE < 7 day 直接走 polynomial |
| 公司 Trend Micro AV 鎖 cache_dir/vol_surface/ | batch fit 寫不進 | 低 | cache_dir 已實證寫得進（1963 shard）|
| **TAIEX spot 缺 3 day** (Codex R11.6 P1) | full-range pipeline raise | **已實證** | add_underlying 加 missing_policy enum；production 用 forward_fill + audit log |
| **Greek-level risk limits 未接** (Codex R11.6 P2) | engine.aggregate_greeks 空，沒 portfolio Δ/Γ/ν/Θ limit | 高 (Pro trading 必補) | Phase 2 backlog；Week 6+ 真 backtest 前**至少**加 portfolio delta limit 守線 |
| **ATM 認定 deep-OTM-only 月份不適用** (R10 系列 prerequisite) | q_pit 拿稀疏月份 ATM → q 偏差 | 中 | Q_PIT_ABS_CAP=0.15 已 gate；Day 1 fit universe filter 統一 OTM 規則 |

---

## §10 跨 Phase 全域決策

- **DP-VS-1 SVI raw vs SVI natural form**：選 raw（5 params 直接 / Lee 2004 直接套用）；natural form 留 Phase 2 evaluate
- **DP-VS-2 fix β=1 (lognormal)** for SABR：TAIEX 是 index option 慣例
- **DP-VS-3 multiprocessing pool 大小**：CPU count - 1（保留 1 core 給 OS）
- **DP-VS-4 surface cache 永久 vs 臨時**：永久（Day 5 batch 跑一次 → 之後 backtest 用），跟 raw_zip cache 同等級
- **DP-VS-5 model_type 標 silent fallback (prerequisite #4)**：每 row `model_price_source` col 明示 svi/sabr/poly/mid，永不 silent

---

## §11 誠實補充

- **Time estimate**: Week 4 Day 1-5 約 5-8 day（SVI 數學細節有風險）；Week 5 Day 1-5 約 5-7 day；總 ~12 day。Week 6+ 真 backtest 推 5 月中。
- **Failure modes**:
  1. SVI 數學我寫過 reference 但**沒實測 TXO 真資料** → Day 1-2 可能踩坑
  2. arb-free constraint 對真 wide-spread 月份太嚴 → SABR / poly 比例 > 50% （接受但要 audit）
  3. 1-year sub-set surface vs settle diff 真實可能 > 30%，需重 fit + Codex R12 review
  4. multiprocessing 在 Windows AV 環境可能 worker spawn fail（要加 fallback to serial）
- **Assumption boundary**:
  1. 假設 Lee 2004 arb-free 對 TXO 適用；TXO 短 DTE / Asia-only liquidity 可能不完全成立
  2. 假設 SABR β=1 lognormal 對 TAIEX 合理；若 vol-of-vol 太高要改 β=0.5
  3. 假設 cache_dir/vol_surface/ AV 友善（現有 raw/strategy_view 已 OK，新 dir 推測也 OK）
  4. 假設 1-year sub-set 三 policy diff 是 mark machinery 健康度的合理 proxy；若 IC 策略本身對 mark 不敏感（e.g. ATM-collected credit 主導 PnL），diff 可能無意義

---

## §12 待 user 拍板

1. SVI raw form vs natural form？（推薦 raw）
2. SABR β fix 1.0 (lognormal) vs 0.5？（推薦 1.0 對 index option）
3. polynomial fallback degree-2 vs degree-3？（推薦 2，過 fit 風險低）
4. Week 5 Day 4 sub-set 用 2024 vs 2023？（推薦 2024 — Codex R10.12 已實證 fallback 行為）
5. multiprocessing 預設 worker = N_CORES - 1 vs 8 vs 4？（推薦 N_CORES - 1）
6. arb-free 預設 on vs off？（推薦 on，但提供 `--no-arb-free` flag 給 debug）
7. 跑 R11.6 audit 跟此 plan 一起送審 vs 各自獨立？（推薦一起）

**user 拍板後**，我才動 code。Plan 文件先送審。
