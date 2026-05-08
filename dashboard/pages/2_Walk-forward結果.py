"""Page 2 — Walk-forward 結果：6 scenario × 5 年 OOS 證據鏈。

Framework 視角 frame：本 page 展示「框架為何能誠實判定 NO-GO」的證據鏈
（PIT / Bootstrap CI / sign-flip permutation / Deflated Sharpe / cost ablation），
而非單純自貶「我輸了」。重點是嚴謹判定贏輸的能力。
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from utils import (
    SCENARIO_COLORS,
    SCENARIO_DISPLAY_NAMES,
    load_5yr_daily_pnl,
    load_5yr_scenarios,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Walk-forward 結果 | Options_Trading", page_icon="📈", layout="wide")

st.title("Page 2 — Walk-forward 5 年 OOS 結果")
st.caption(
    "6 scenario × 15 disjoint quarterly OOS folds · "
    "Bootstrap CI · Sign-flip permutation · Deflated Sharpe · Retail cost ablation"
)

st.divider()

# ===========================================================================
# Section 2.1 — Hero 摘要（framework 視角 frame）
# ===========================================================================

st.header("2.1 摘要")

st.markdown(
    """
    本 repo 對 IC（Iron Condor）+ Vertical Spread short premium 假設於 **2021-04 ~ 2026-04 5 年真資料**
    跑 walk-forward 嚴謹 OOS（樣本外）驗證。下方 6 個面向構成 **可信度證據鏈**：

    1. **6 scenario 摘要**：Sharpe / Bootstrap CI / sign-flip permutation p-value / Deflated Sharpe / max drawdown / Calmar / 交易次數
    2. **Cumulative PnL 曲線**：6 條策略 5 年 OOS 累積 PnL，含 fold 邊界與 max drawdown 區段
    3. **Bootstrap CI**：每個 scenario 的 95% percentile CI 是否跨零（不跨零代表 OOS Sharpe 統計顯著）
    4. **Permutation null 分布**：sign-flip 1000 次，觀測 Sharpe 在 null 分布的位置 + p-value
    5. **Retail cost ablation**：with-cost vs no-cost Sharpe 對比，判斷 retail 摩擦是否為 root cause
    6. **Walk-forward 設計**：disjoint quarterly OOS 視覺化，解釋 step ≥ test_window 的 critical correctness gate

    結果：5 年 OOS 6 scenario 全 negative Sharpe；retail cost ablation 證明摩擦不是 root cause；
    **strategy alpha 證偽**（不為產品結論，為 framework 嚴謹度的 demonstration — 對 quant interview portfolio
    而言，「能嚴謹證偽自己的 hypothesis」比「假裝賺錢」更有信號）。
    """
)

st.divider()

# ===========================================================================
# Section 2.2 — 6 scenario 摘要表
# ===========================================================================

st.header("2.2 6 scenario 摘要表")

scenarios_df = load_5yr_scenarios()

# 選顯示欄位 + rename
display_df = scenarios_df[
    [
        "scenario",
        "agg_sharpe",
        "bootstrap_ci_low",
        "bootstrap_ci_high",
        "permutation_p_value",
        "deflated_sharpe",
        "agg_max_drawdown",
        "calmar_ratio",
        "total_trades",
    ]
].copy()

# 加可讀的 scenario 名稱
display_df.insert(
    1, "Strategy", display_df["scenario"].map(SCENARIO_DISPLAY_NAMES).fillna(display_df["scenario"])
)
display_df = display_df.drop(columns=["scenario"])

# 改欄位名（中文）
display_df.columns = [
    "Strategy",
    "Sharpe",
    "Boot CI low",
    "Boot CI high",
    "Perm p-value",
    "Deflated Sharpe",
    "Max DD",
    "Calmar",
    "Trades",
]


# 條件 highlight：Sharpe < 0 紅；Boot CI low < 0 紅；DSR ≤ 0 紅；Trades = 0 紅
def _highlight_negative(val: float, threshold: float = 0.0) -> str:
    if isinstance(val, (int, float)) and not np.isnan(val) and val < threshold:
        return "color: #d62728; font-weight: bold;"
    return ""


def _highlight_zero(val: float) -> str:
    if isinstance(val, (int, float)) and not np.isnan(val) and val <= 0.0:
        return "color: #d62728; font-weight: bold;"
    return ""


styled = (
    display_df.style.format(
        {
            "Sharpe": "{:.4f}",
            "Boot CI low": "{:.4f}",
            "Boot CI high": "{:.4f}",
            "Perm p-value": "{:.4f}",
            "Deflated Sharpe": "{:.4f}",
            "Max DD": "{:.4%}",
            "Calmar": "{:.4f}",
            "Trades": "{:.0f}",
        },
        na_rep="—",
    )
    .map(_highlight_negative, subset=["Sharpe", "Boot CI low"])
    .map(_highlight_zero, subset=["Deflated Sharpe"])
)

st.dataframe(styled, use_container_width=True, hide_index=True)

# 下方 4 個 takeaways（觀察、紀律陳述）
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "Sharpe < 0 scenarios",
        f"{(scenarios_df['agg_sharpe'] < 0).sum()} / {len(scenarios_df)}",
        help="6 scenarios 中 Sharpe 為負的比例",
    )
with col2:
    st.metric(
        "Boot CI 不跨零（負）",
        f"{(scenarios_df['bootstrap_ci_high'] < 0).sum()} / {len(scenarios_df)}",
        help="Bootstrap 95% CI 上界 < 0 → Sharpe 顯著為負",
    )
with col3:
    st.metric(
        "DSR > 0.95",
        f"{(scenarios_df['deflated_sharpe'].fillna(0) > 0.95).sum()} / {len(scenarios_df)}",
        help="Deflated Sharpe 顯著（López-de-Prado 2014 閾值）",
    )
with col4:
    st.metric(
        "Total trades",
        f"{int(scenarios_df['total_trades'].sum())}",
        help="6 scenario × 15 fold 跨 5 年總交易次數",
    )

st.caption(
    "**讀法**：紅字 = Sharpe < 0 / Boot CI low < 0 / DSR ≤ 0；DSR = 0 因 N=945 obs / 6 scenarios "
    "selection bias 校正後在 H0 失敗（López-de-Prado 2014 公式）。HMM gate 因樣本不足過於嚴格 "
    "→ IC_HMM 0 trades / Vertical_HMM 1 trade。"
)

st.divider()

# ===========================================================================
# Section 2.3 — Cumulative PnL Curves
# ===========================================================================

st.header("2.3 Cumulative PnL Curves（6 scenario 疊加）")

st.markdown(
    """
    各 scenario daily PnL 取 `cumsum()` 得 5 年 OOS 累積曲線。x 軸為交易日，y 軸為累積 PnL（NT$）。
    觀察：6 條曲線最終全收於負區域；HMM 兩條因 trades 過少（0 / 1）幾乎水平。
    """
)

daily_df = load_5yr_daily_pnl()

# Compute per-scenario cumsum
plot_data = []
for sc in daily_df["scenario"].unique():
    sub = daily_df[daily_df["scenario"] == sc].sort_values("date")
    cum = sub["daily_pnl_twd"].cumsum()
    plot_data.append(
        {
            "scenario": sc,
            "dates": sub["date"].values,
            "cum_pnl": cum.values,
        }
    )

# Plotly multi-line plot
fig_cum = go.Figure()
for d in plot_data:
    sc_name = SCENARIO_DISPLAY_NAMES.get(d["scenario"], d["scenario"])
    color = SCENARIO_COLORS.get(d["scenario"], None)
    fig_cum.add_trace(
        go.Scatter(
            x=d["dates"],
            y=d["cum_pnl"],
            mode="lines",
            name=sc_name,
            line={"width": 2, "color": color} if color else {"width": 2},
            hovertemplate="%{x|%Y-%m-%d}<br>cum PnL: NT$%{y:,.0f}<extra>" + sc_name + "</extra>",
        )
    )

# 0 horizontal line
fig_cum.add_hline(y=0, line_color="black", line_width=1, line_dash="dash")

fig_cum.update_layout(
    xaxis_title="Date",
    yaxis_title="累積 PnL（NT$）",
    height=550,
    margin={"l": 60, "r": 20, "t": 30, "b": 60},
    legend={"orientation": "h", "yanchor": "bottom", "y": -0.25, "xanchor": "left", "x": 0},
    hovermode="x unified",
)

st.plotly_chart(fig_cum, use_container_width=True)

# 4 個 takeaway 數字
col1, col2, col3, col4 = st.columns(4)
with col1:
    worst_final = min(d["cum_pnl"][-1] if len(d["cum_pnl"]) > 0 else 0 for d in plot_data)
    st.metric(
        "Worst final cum PnL",
        f"NT${worst_final:,.0f}",
        help="6 scenario 5 年累積 PnL 最差者",
    )
with col2:
    best_final = max(d["cum_pnl"][-1] if len(d["cum_pnl"]) > 0 else 0 for d in plot_data)
    st.metric(
        "Best final cum PnL",
        f"NT${best_final:,.0f}",
        help="6 scenario 5 年累積 PnL 最好者",
    )
with col3:
    st.metric(
        "Initial capital",
        "NT$1,000,000",
        delta="retail 100 萬 baseline",
        delta_color="off",
    )
with col4:
    n_obs = int(daily_df.groupby("scenario").size().iloc[0])
    n_dates = daily_df["date"].nunique()
    st.metric(
        "Observations",
        f"{n_obs}",
        delta=f"{n_dates} 個交易日",
        delta_color="off",
    )

st.divider()

# ===========================================================================
# Section 2.4 — Bootstrap CI + Permutation null distribution
# ===========================================================================

st.header("2.4 Bootstrap CI + Permutation null distribution")

st.markdown(
    """
    **Bootstrap percentile CI**（百分位信賴區間）：對 daily PnL resample with replacement 1000 次，
    每次重算 Sharpe，取 2.5% / 97.5% percentile 作 95% CI 下/上界。**CI 不跨零**代表 Sharpe 統計顯著。

    **Sign-flip permutation test**（Politis & Romano 2010）：對每筆 PnL 獨立翻轉 ±1（共 1000 次），
    計算 null 分布下 |Sharpe|，p-value = `(n_extreme + 1) / (n_iter + 1)`（Phipson & Smyth 2010 unbiased）。
    為何不用 random shuffle：Sharpe 對 shuffle 完全不變（mean 與 std 都是排列不變量），原始 shuffle 的 p-value 沒意義。

    code 路徑：[`src/backtest/stats.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/stats.py)
    """
)

left, right = st.columns(2)

# --- Left: Bootstrap CI horizontal bars ---
with left:
    st.subheader("Bootstrap 95% CI per scenario")

    fig_ci = go.Figure()
    for _, row in scenarios_df.iterrows():
        sc = row["scenario"]
        sc_name = SCENARIO_DISPLAY_NAMES.get(sc, sc)
        ci_lo = row["bootstrap_ci_low"]
        ci_hi = row["bootstrap_ci_high"]
        sharpe = row["agg_sharpe"]
        if np.isnan(ci_lo) or np.isnan(ci_hi):
            continue
        fig_ci.add_trace(
            go.Scatter(
                x=[ci_lo, ci_hi],
                y=[sc_name, sc_name],
                mode="lines+markers",
                line={"width": 6, "color": SCENARIO_COLORS.get(sc, "#888")},
                marker={"size": 10, "color": SCENARIO_COLORS.get(sc, "#888")},
                showlegend=False,
                hovertemplate=f"{sc_name}<br>CI low: {ci_lo:.3f}<br>CI high: {ci_hi:.3f}<extra></extra>",
            )
        )
        fig_ci.add_trace(
            go.Scatter(
                x=[sharpe],
                y=[sc_name],
                mode="markers",
                marker={"size": 14, "color": "black", "symbol": "diamond"},
                showlegend=False,
                hovertemplate=f"observed Sharpe: {sharpe:.4f}<extra></extra>",
            )
        )

    fig_ci.add_vline(x=0, line_color="black", line_width=2)
    fig_ci.update_layout(
        xaxis_title="Sharpe ratio",
        height=400,
        margin={"l": 200, "r": 20, "t": 20, "b": 50},
    )
    st.plotly_chart(fig_ci, use_container_width=True)
    st.caption(
        "黑色菱形 = observed Sharpe；橫條 = 95% CI 範圍；垂直黑線 = 0。"
        "若 CI 完全在 0 左側 → Sharpe 顯著為負。"
    )

# --- Right: Permutation null distribution (selectable scenario) ---
with right:
    st.subheader("Permutation null distribution")

    pickable_scenarios = [sc for sc in scenarios_df["scenario"].unique() if sc not in ("IC_HMM",)]
    selected_sc = st.selectbox(
        "選擇 scenario",
        pickable_scenarios,
        index=0,
        format_func=lambda sc: SCENARIO_DISPLAY_NAMES.get(sc, sc),
        help="IC_HMM 0 trades / 全 0 PnL，permutation null 退化，已從選單剔除",
    )

    @st.cache_data(show_spinner="計算 sign-flip null distribution（1000 iter）...")
    def _compute_permutation(scenario: str, seed: int = 42) -> tuple[float, np.ndarray, float]:
        """Sign-flip permutation 用 risk-free 校正後的 daily excess returns，
        確保 observed Sharpe 與 Section 2.2 摘要表（用 `sharpe_ratio(initial_capital=)`
        TWD-mode）一致。H0：daily excess returns 對稱零飄移。
        """
        from src.backtest.stats import permutation_test

        initial_capital = 1_000_000.0
        rf_annual = 0.015
        rf_daily = rf_annual / 252.0

        sub = daily_df[daily_df["scenario"] == scenario].sort_values("date")
        pnl_twd = sub["daily_pnl_twd"].astype(float).values
        excess_returns = pnl_twd / initial_capital - rf_daily
        return permutation_test(excess_returns, n_iter=1000, seed=seed)

    observed, null_dist, p_value = _compute_permutation(selected_sc)

    fig_perm = go.Figure()
    fig_perm.add_trace(
        go.Histogram(
            x=null_dist,
            nbinsx=50,
            marker={"color": "#888", "opacity": 0.7},
            name="null distribution",
        )
    )
    fig_perm.add_vline(
        x=observed,
        line_color="#d62728",
        line_width=3,
        line_dash="solid",
        annotation_text=f"observed = {observed:.3f}",
        annotation_position="top",
    )
    fig_perm.add_vline(x=0, line_color="black", line_width=1, line_dash="dash")
    fig_perm.update_layout(
        xaxis_title="Sharpe（sign-flip null）",
        yaxis_title="頻率",
        height=400,
        margin={"l": 60, "r": 20, "t": 20, "b": 60},
        showlegend=False,
    )
    st.plotly_chart(fig_perm, use_container_width=True)

    st.caption(
        f"**p-value = {p_value:.4f}** = `(n_extreme + 1) / (1000 + 1)`（Phipson & Smyth 2010 unbiased）。"
        f"觀測 Sharpe 落在 null 分布{'外側 → reject H0' if p_value < 0.05 else '中間區 → 無法 reject H0'}。"
    )

st.divider()

# ===========================================================================
# Section 2.5 — Retail Cost Ablation
# ===========================================================================

st.header("2.5 Retail Cost Ablation（with-cost vs no-cost）")

st.markdown(
    """
    Retail 成本模型：手續費 NT$12 / 期交稅 10 bps（依 TAIFEX schedule，TXO premium × 0.001）/
    滑價 15 bps + worst-side fill。本 ablation 比對 with-cost vs no-cost Sharpe，判斷 retail 摩擦是否為 root cause。

    若 |Δ Sharpe| 顯著（如 > 0.5），說明拿掉成本策略就有 edge → retail friction 是 root cause；
    若 |Δ Sharpe| 微小（如 < 0.05），說明拿掉成本後 Sharpe 仍 deeply negative → strategy 真的沒 edge，與摩擦無關。

    code 路徑：[`src/backtest/execution.py::RetailCostModel`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/execution.py)
    """
)


@st.cache_data
def _load_cost_ablation():
    import pandas as pd
    from utils import REPORTS

    return pd.read_csv(REPORTS / "week6_5yr_no_cost_vs_with_cost.csv")


cost_df = _load_cost_ablation()

# Bar plot: with-cost vs no-cost Sharpe
sc_names_cost = [SCENARIO_DISPLAY_NAMES.get(s, s) for s in cost_df["scenario"]]
fig_cost = go.Figure()
fig_cost.add_trace(
    go.Bar(
        x=sc_names_cost,
        y=cost_df["agg_sharpe_no_cost"],
        name="No-cost Sharpe",
        marker={"color": "#aec7e8"},
        text=[f"{v:.3f}" for v in cost_df["agg_sharpe_no_cost"]],
        textposition="outside",
    )
)
fig_cost.add_trace(
    go.Bar(
        x=sc_names_cost,
        y=cost_df["agg_sharpe_with_cost"],
        name="With-cost Sharpe",
        marker={"color": "#1f77b4"},
        text=[f"{v:.3f}" for v in cost_df["agg_sharpe_with_cost"]],
        textposition="outside",
    )
)
fig_cost.add_hline(y=0, line_color="black", line_width=1, line_dash="dash")
fig_cost.update_layout(
    yaxis_title="Sharpe ratio",
    height=450,
    margin={"l": 60, "r": 20, "t": 30, "b": 100},
    barmode="group",
    legend={"orientation": "h", "yanchor": "bottom", "y": -0.4, "xanchor": "left", "x": 0},
    xaxis_tickangle=-15,
)

st.plotly_chart(fig_cost, use_container_width=True)

# Δ Sharpe 表
delta_df = cost_df[
    ["scenario", "agg_sharpe_no_cost", "agg_sharpe_with_cost", "delta_sharpe"]
].copy()
delta_df["scenario"] = delta_df["scenario"].map(SCENARIO_DISPLAY_NAMES).fillna(delta_df["scenario"])
delta_df.columns = ["Strategy", "No-cost Sharpe", "With-cost Sharpe", "Δ Sharpe"]
delta_df["|Δ|"] = delta_df["Δ Sharpe"].abs()

st.subheader("Δ Sharpe 對照")
st.dataframe(
    delta_df.style.format(
        {
            "No-cost Sharpe": "{:.4f}",
            "With-cost Sharpe": "{:.4f}",
            "Δ Sharpe": "{:+.4f}",
            "|Δ|": "{:.4f}",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

max_abs_delta = float(delta_df["|Δ|"].max())
st.success(
    f"**結論**：6 scenario 中最大 |Δ Sharpe| = **{max_abs_delta:.4f}** "
    f"（< 0.02）→ retail 摩擦**不是** root cause，strategy 真的沒 edge。",
    icon="✅",
)

st.divider()

# ===========================================================================
# Section 2.6 — Walk-forward 設計視覺化
# ===========================================================================

st.header("2.6 Walk-forward 設計與正確性 gate")

st.markdown(
    """
    **設定**（見 [`src/backtest/walk_forward.py::WalkForwardConfig`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/walk_forward.py)）：

    - `train_window_days = 252`（1 年 rolling train）
    - `test_window_days = 63`（1 季 disjoint OOS）
    - `step_days = 63`（**= test_window_days，確保 OOS 不重疊**）
    - `expanding = False`（rolling train，不累積）
    - `mark_policy = "mid_with_surface_fallback"`（缺 bid/ask 用 SVI surface model price 補）

    **Critical correctness gate**：`__post_init__` 強制 `step_days >= test_window_days`，否則 raise。
    為何：若 step < test，相鄰 fold 的 OOS 窗會重疊，concat daily PnL 會在同一日重複計算，
    導致 aggregate Sharpe / max drawdown / Calmar 全部 inflate（false positive 風險）。

    5 年 backtest 共 **15 disjoint quarterly fold**，每 fold 1 年 train + 1 季 OOS test。
    """
)


@st.cache_data
def _load_folds():
    import pandas as pd
    from utils import REPORTS

    return pd.read_csv(REPORTS / "week6_5yr_folds.csv")


import pandas as pd  # noqa: E402

folds_df = _load_folds()

# 用 IC_vanilla 的 15 fold 作 timeline 視覺化（每 scenario fold layout 一致）
ic_folds = folds_df[folds_df["scenario"] == "IC_vanilla"].copy()
for col in ("train_start", "train_end", "test_start", "test_end"):
    ic_folds[col] = pd.to_datetime(ic_folds[col])

fig_folds = go.Figure()
legend_shown_train = False
legend_shown_test = False
for _, row in ic_folds.iterrows():
    fold = int(row["fold_index"])
    fig_folds.add_trace(
        go.Scatter(
            x=[row["train_start"], row["train_end"]],
            y=[fold, fold],
            mode="lines",
            line={"width": 12, "color": "#aec7e8"},
            name="Train (252 日)",
            showlegend=not legend_shown_train,
            hovertemplate=f"Fold {fold} Train: %{{x|%Y-%m-%d}}<extra></extra>",
        )
    )
    legend_shown_train = True
    fig_folds.add_trace(
        go.Scatter(
            x=[row["test_start"], row["test_end"]],
            y=[fold, fold],
            mode="lines",
            line={"width": 12, "color": "#d62728"},
            name="OOS Test (63 日 disjoint)",
            showlegend=not legend_shown_test,
            hovertemplate=f"Fold {fold} OOS: %{{x|%Y-%m-%d}}<extra></extra>",
        )
    )
    legend_shown_test = True

fig_folds.update_layout(
    xaxis_title="Date",
    yaxis_title="Fold index",
    title="15 disjoint quarterly OOS folds（IC_vanilla 範例；6 scenario 共用此 layout）",
    height=550,
    margin={"l": 60, "r": 20, "t": 60, "b": 60},
    yaxis={"autorange": "reversed", "tickmode": "linear", "dtick": 1},
    legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
)

st.plotly_chart(fig_folds, use_container_width=True)

st.caption(
    "**讀法**：每行為 1 個 fold（共 15 個）；藍條 = 252 日 train 期、紅條 = 63 日 disjoint OOS test 期。"
    "相鄰 fold 紅條完全不重疊（critical correctness gate）；藍條 rolling 推進 1 季。"
)

st.divider()

# ---------------------------------------------------------------------------
# Cross-page navigation footer
# ---------------------------------------------------------------------------

st.markdown("### 接下來")
nav_cols = st.columns(3)
with nav_cols[0]:
    st.page_link("專案背景.py", label="← 專案背景", icon="🏠")
with nav_cols[1]:
    st.page_link("pages/1_定價核心.py", label="Page 1 — 定價核心", icon="🧮")
with nav_cols[2]:
    st.page_link("pages/3_Audit紀律與Bug修法.py", label="Page 3 — Audit 紀律 →", icon="🛡️")
