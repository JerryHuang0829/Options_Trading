"""Options_Trading Dashboard 首頁 — 專案背景。

Streamlit entry point。從 repo root 啟動：
    streamlit run dashboard/專案背景.py
"""

from __future__ import annotations

import streamlit as st
from utils import PHASE1_TIMELINE

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Options_Trading | 專案背景",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------

st.title("Options_Trading")
st.caption(
    "TAIFEX TXO（台指選擇權）系統化策略研究框架 · "
    "自寫 BSM-Merton 定價 · 5 年 walk-forward 回測 · Pro 量化統計工具鏈"
)

st.divider()

# ---------------------------------------------------------------------------
# Hero metrics（4 個核心數字）
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="TAIFEX 真資料",
        value="1,963",
        delta="個交易日（8 年）",
        delta_color="off",
        help="2018-04-02 ~ 2026-04-28，TXO 每日選擇權鏈 raw + strategy_view 雙層 parquet cache",
    )

with col2:
    st.metric(
        label="Vol Surface fits",
        value="1,227",
        delta="5 年 100% 覆蓋",
        delta_color="off",
        help="SVI 5 參數 + SABR 4 參數 + 多項式 fallback；Gatheral & Jacquier (2014) arb-free + Lee (2004) bound",
    )

with col3:
    st.metric(
        label="Tests",
        value="465",
        delta="2 skipped",
        delta_color="off",
        help="鏡像 src/ 結構；4 件 hard gate（ruff / format / mypy / pytest）全綠",
    )

with col4:
    st.metric(
        label="External Review Chain",
        value="14+1",
        delta="輪 closed",
        delta_color="off",
        help="R12.0 ~ R12.13 連 14 輪 review chain closed + 加碼一輪抓到並修復 silent bug",
    )

st.divider()

# ---------------------------------------------------------------------------
# 一句話定位
# ---------------------------------------------------------------------------

st.markdown(
    """
    > 本 repo 是 systematic options strategy 研究框架：自寫 BSM-Merton 對 50 random sample 對齊 `py_vollib` reference 至 1e-8 精度；
    > 用 8 年 TAIFEX 真資料跑 6 scenario walk-forward；經 14 輪 external review chain + 1 輪內部驗證抓 silent bug 修法；
    > 以 Pro 統計方法（PIT / Bootstrap CI / sign-flip permutation / Deflated Sharpe）嚴謹判定
    > **Phase 1 IC/Vertical short premium 於 5yr OOS 為 NO-GO**（6 scenario 全 negative Sharpe、無一通過預設出口條件）。
    >
    > 用詞精準：IC scenario 5 年僅成交 0~5 筆（全期共 32 筆）→ Bootstrap CI 跨零、permutation p > 0.1 →
    > 嚴格說屬「樣本不足、無法判定（inconclusive）」而非完整「證偽」；binding constraint = TXO 每日 cohort 稀疏（平均 1.44 個到期）。
    > 詳見 Page 2 與 [`docs/phase1_conclusion.md`](https://github.com/JerryHuang0829/Options_Trading/blob/main/docs/phase1_conclusion.md)。
    >
    > 下方 dashboard 展示支撐此判定的證據鏈與方法學。
    """
)

# ---------------------------------------------------------------------------
# 專案規格（30 秒看懂 scope）
# ---------------------------------------------------------------------------

st.subheader("專案規格")

spec_l, spec_r = st.columns(2)
with spec_l:
    st.markdown(
        """
        - **規模 baseline**：零售 NT$ 1,000,000
        - **標的**：TAIFEX TXO（台指選擇權，月選）
        - **策略型態**：賣方溢價 — Iron Condor（4 腳）+ Vertical Spread（bull put / bear call）；**永不 naked**
        - **基準問題**：5 年真實 OOS 下能否穩定賺錢（Sharpe > 1）
        - **資料源**：TAIFEX 每日選擇權結算行情（Big5 ZIP）；Shioaji broker 抽象層（Phase 2）
        """
    )
with spec_r:
    st.markdown(
        """
        - **回測樣本**：walk-forward OOS 2021-04 ~ 2026-04（資料管線涵蓋 2018-04 起，8 年 / 1963 交易日）
        - **Walk-forward 設定**：252 日 rolling train / 63 日 disjoint quarterly OOS / step = 63（OOS 不重疊）→ 15 folds
        - **定價核心**：自寫 BSM-Merton（含連續股利率 `q`）+ `py_vollib` 交叉驗證至 1e-8
        - **Mark-to-market**：SVI / SABR vol surface model-price fallback（解 60% bid/ask 缺值）
        - **成本模型**：手續費 NT$12 + 期交稅 10 bps + 滑價 15 bps + worst-side fill
        - **回測引擎**：自寫 daily-loop backtest engine（PIT-safe）
        """
    )

st.divider()

# ---------------------------------------------------------------------------
# 路線圖時間軸（Plotly horizontal timeline）
# ---------------------------------------------------------------------------

st.subheader("Phase 1 路線圖")

# 用 native st.markdown 排版，比 Plotly timeline 簡潔（時間軸資料量小）
for week, title, desc in PHASE1_TIMELINE:
    cols = st.columns([1, 2, 5])
    with cols[0]:
        st.markdown(f"**{week}**")
    with cols[1]:
        st.markdown(f"**{title}**")
    with cols[2]:
        st.markdown(desc)

st.divider()

# ---------------------------------------------------------------------------
# 技術棧（5 類別表）
# ---------------------------------------------------------------------------

st.subheader("技術棧")

st.markdown(
    """
    | 類別 | 套件 / 工具 |
    |---|---|
    | 🧮 **數學 / 定價** | Python 3.12（conda-forge）· numpy · pandas · scipy · `py_vollib` |
    | 📊 **統計工具** | scipy.stats · 自寫 [`src/backtest/stats.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/stats.py)（Bootstrap CI / sign-flip permutation / Deflated Sharpe / Calmar）|
    | 💾 **資料 I/O** | pyarrow（parquet）· requests · holidays（台灣假日）|
    | 🎨 **Dashboard** | streamlit · plotly |
    | 🛡️ **品質紀律** | ruff（lint + format）· mypy + pandas-stubs（靜態型別）· pytest + pytest-cov（465 tests）|

    完整版本鎖定見 [`requirements.txt`](https://github.com/JerryHuang0829/Options_Trading/blob/main/requirements.txt)；
    開發環境為 conda-forge channel `options` env。
    """
)

st.divider()

# ---------------------------------------------------------------------------
# 核心能力 5 條（expander 摺疊）
# ---------------------------------------------------------------------------

st.subheader("核心能力")

with st.expander("🧮 自寫 BSM-Merton + py_vollib 交叉驗證"):
    st.markdown(
        """
        - **Black-Scholes-Merton 封閉解**（含連續股利率 `q`）+ 5 個 Greeks（Δ Γ Θ ν ρ）
        - **Newton-Raphson IV solver** + Brent fallback
        - 對 50 random sample 用 `py_vollib.black_scholes_merton` 交叉驗證，price diff < 1e-8
        - **4 種單位換算規則**對齊：vega per 1.0 vs per 1%、theta per-day-365、rho per 1%
        - 為何 Merton form 不是純 BSM：TAIEX 是 price index，成分股配息會在除權日造成 schedule drop；
          純 BSM (`q=0`) 系統性偏置 ATM delta 約 `q·T·S` 並破壞 Put-Call Parity

        詳見 **Page 1 — 定價核心**。
        """
    )

with st.expander("📊 8 年 TAIFEX TXO 資料管線"):
    st.markdown(
        """
        - **2018-04 ~ 2026-04，1963 個交易日**真期權鏈
        - **Big5 / CP950 編碼** 自動解碼
        - **3 種 schema 自動偵測**（OLDEST 18 欄 / PRE 20 欄 / POST 21 欄）
        - **parquet 雙層 cache**（raw + strategy_view，按年份分資料夾）
        - **ZIP magic-bytes guard**（防下載失敗時把 HTML 錯誤頁存成 ZIP）
        - **annual + daily 兩種下載模式**

        資料體積：raw 149 MB + strategy_view 37 MB（gitignored，用 loader 重建）。
        """
    )

with st.expander("📈 SVI / SABR Volatility Surface"):
    st.markdown(
        """
        - **SVI 5 參數**（a / b / ρ / m / σ）+ **SABR 4 參數**（α / ρ / ν / β=1 lognormal）+ **多項式 degree-2 fallback**
        - **3-tier orchestration**：SVI → SABR → polynomial
        - **arb-free 守衛**：Gatheral & Jacquier (2014) butterfly + Lee (2004) Roger Lee moment formula
        - **5 年回測窗口 1227 shard 100% 日期覆蓋**
        - 解決 60% bid/ask 缺值問題：用 model price fallback 取代 settle，得到乾淨的 mark-to-market

        詳見 **Page 1 — 定價核心** 第 1.5 節 SVI Vol Surface 3D。
        """
    )

with st.expander("🔬 Walk-forward 回測引擎"):
    st.markdown(
        """
        - **252 日 train / 63 日 disjoint quarterly OOS folds**
        - **6 scenario** =（Iron Condor + Vertical）×（vanilla + IV percentile gate + HMM 2-state regime gate）
        - **嚴格 daily loop**（按日推進，禁止 backward look）
        - **Point-in-time（PIT）正確性**：strategy factory 只看到 train 期 returns
        - **Critical correctness gate**：`step_days ≥ test_window_days` 強制（防 fold OOS 重疊污染 aggregate metrics）

        詳見 **Page 2 — Walk-forward 結果**。
        """
    )

with st.expander("📐 Pro 量化統計工具鏈"):
    st.markdown(
        """
        - **Bootstrap percentile CI**（百分位信賴區間，`n_iter=1000`）
        - **Sign-flip permutation test**（Politis & Romano 2010；對每筆 PnL 翻轉 ±1，
          解決 random shuffle 在 Sharpe 下完全不變的問題）
        - **Deflated Sharpe Ratio**（López-de-Prado 2014；校正 selection bias / non-normality）
        - **Calmar ratio**（年化報酬 / max drawdown）
        - **Retail 成本模型**：手續費 NT$12 + 期交稅 10 bps + 滑價 15 bps
        - **Worst-side fill model**（賣方 fill at bid，買方 fill at ask）— 散戶實際成交真實性

        詳見 **Page 2 — Walk-forward 結果** 與 **Page 3 — Audit 紀律**。
        """
    )

st.divider()

# ---------------------------------------------------------------------------
# Dashboard 導引
# ---------------------------------------------------------------------------

st.subheader("如何使用本 dashboard")

st.markdown(
    """
    **建議閱讀順序**（每 page 約 2-5 分鐘）：

    1. **Page 1 — 定價核心**：BSM-Merton 公式、py_vollib 交叉驗證、Strategy payoff、Greeks 互動 slider、Vol Surface 3D
    2. **Page 2 — Walk-forward 結果**：6 scenario 5 年 OOS 摘要、cumulative PnL curves、Bootstrap CI、Permutation null distribution、Retail cost ablation
    3. **Page 3 — Audit 紀律與 Bug 修法**：14+1 輪 external review chain timeline、4 件 hard gate 全綠、`agg_max_drawdown` silent bug 案例 deep dive
    """
)

st.info(
    "💡 **使用提示**：左側 sidebar 點擊各 page 切換；Page 1 的 Greeks slider 與 Page 2 的 Permutation dropdown 為互動元件，可拖動 / 切換以即時看到圖表變化。",
    icon="💡",
)

st.divider()

# Cross-page quick nav
nav_cols = st.columns(3)
with nav_cols[0]:
    st.page_link("pages/1_定價核心.py", label="Page 1 — 定價核心", icon="🧮")
with nav_cols[1]:
    st.page_link("pages/2_Walk-forward結果.py", label="Page 2 — Walk-forward 結果", icon="📈")
with nav_cols[2]:
    st.page_link("pages/3_Audit紀律與Bug修法.py", label="Page 3 — Audit 紀律", icon="🛡️")

st.caption(
    "GitHub: [JerryHuang0829/Options_Trading](https://github.com/JerryHuang0829/Options_Trading) · "
    "Tests: 465 passed, 2 skipped · "
    "Phase 1 完工"
)
