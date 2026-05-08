"""Page 1 — 定價核心：BSM-Merton 公式、py_vollib 交叉驗證、Strategy payoff。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# 匯入 utils 會把 PROJECT_ROOT 加到 sys.path，後續才能 import src/
from utils import PROJECT_ROOT  # noqa: F401

from src.options.pricing import bsm_price  # noqa: E402

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="定價核心 | Options_Trading", page_icon="🧮", layout="wide")

st.title("Page 1 — 定價核心")
st.caption(
    "Black-Scholes-Merton（BSM-Merton）封閉解 · Newton-Raphson IV solver · "
    "對 50 random sample 與 `py_vollib.black_scholes_merton` 對齊至 1e-8 精度"
)

st.divider()

# ===========================================================================
# Section 1.1 — BSM-Merton 公式
# ===========================================================================

st.header("1.1 Black-Scholes-Merton 公式")

st.markdown(
    """
    台指選擇權（TXO）標的 TAIEX 為 price index（價格指數），成分股配息會在除權日造成 schedule drop。
    純 BSM（`q=0`）會系統性偏置 ATM delta 約 `q·T·S` 並破壞 Put-Call Parity。
    本 repo 採 **Merton 1973 form** 顯式包含連續股利率 `q`：
    """
)

st.latex(r"d_1 = \frac{\ln(S/K) + (r - q + \sigma^2/2) \cdot T}{\sigma \sqrt{T}}")
st.latex(r"d_2 = d_1 - \sigma \sqrt{T}")
st.latex(r"\text{Call} = S \cdot e^{-qT} \cdot N(d_1) - K \cdot e^{-rT} \cdot N(d_2)")
st.latex(r"\text{Put}  = K \cdot e^{-rT} \cdot N(-d_2) - S \cdot e^{-qT} \cdot N(-d_1)")

st.markdown("**Put-Call Parity（Merton form）**：")
st.latex(r"C - P = S \cdot e^{-qT} - K \cdot e^{-rT}")

st.markdown(
    """
    | Symbol | 意義 | 範例值 |
    |---|---|---|
    | `S` | 標的現貨價（Spot） | 18000（TAIEX）|
    | `K` | 履約價（Strike） | 17400 / 18000 / 18600 |
    | `T` | 到期日（Time to expiry, 年）| 30/365 ≈ 0.0822 |
    | `r` | 無風險利率 | 0.015（台灣 1Y）|
    | `q` | 連續股利率（dividend yield）| 0.035（TAIEX 歷史平均）|
    | `σ` | 隱含波動率（implied volatility）| 0.20（20%）|
    | `N(·)` | 標準常態 CDF | — |

    code 路徑：[`src/options/pricing.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/options/pricing.py)
    """
)

st.divider()

# ===========================================================================
# Section 1.2 — py_vollib Cross-Validation 50-sample
# ===========================================================================

st.header("1.2 py_vollib 交叉驗證（50 random sample）")

st.markdown(
    """
    `py_vollib.black_scholes_merton` 為業界標準 reference 實作。本 repo 自寫 BSM-Merton 對 50 random sample
    交叉驗證，確認 price diff < 1e-8（floating-point 精度上限）。

    對照測試位於 [`tests/options/test_pricing.py::test_bsm_matches_py_vollib`](https://github.com/JerryHuang0829/Options_Trading/blob/main/tests/options/test_pricing.py)。
    本 dashboard 重新跑同邏輯，視覺化 `my_price` vs `py_vollib_price` 的對齊程度。
    """
)


@st.cache_data
def _run_cross_validation(n_samples: int = 50, seed: int = 42) -> pd.DataFrame:
    """Inline 重跑 BSM-Merton vs py_vollib 對照（cached for fast rerun）。"""
    from py_vollib.black_scholes_merton import black_scholes_merton as pv_bsm

    rng = np.random.default_rng(seed=seed)
    rows = []
    for _ in range(n_samples):
        S = float(rng.uniform(16000, 20000))
        K = float(S + rng.uniform(-2000, 2000))
        T = float(rng.uniform(7, 60)) / 365.0
        r = 0.015
        q = float(rng.uniform(0.0, 0.05))
        sigma = float(rng.uniform(0.10, 0.40))
        flag = rng.choice(["c", "p"])
        opt_type = "call" if flag == "c" else "put"
        my_p = bsm_price(S, K, T, r, q, sigma, option_type=opt_type)
        pv_p = pv_bsm(flag=flag, S=S, K=K, t=T, r=r, sigma=sigma, q=q)
        rows.append(
            {
                "S": S,
                "K": K,
                "T_days": T * 365,
                "q": q,
                "sigma": sigma,
                "type": opt_type,
                "my_price": my_p,
                "pv_price": pv_p,
                "diff": my_p - pv_p,
            }
        )
    return pd.DataFrame(rows)


cv_df = _run_cross_validation()

# 三個指標 column：max abs diff / mean abs diff / sample size
col1, col2, col3 = st.columns(3)
with col1:
    max_abs_diff = float(cv_df["diff"].abs().max())
    st.metric("Max |diff|", f"{max_abs_diff:.2e}", help="最大絕對誤差（< 1e-8 為通過）")
with col2:
    mean_abs_diff = float(cv_df["diff"].abs().mean())
    st.metric("Mean |diff|", f"{mean_abs_diff:.2e}", help="平均絕對誤差")
with col3:
    st.metric(
        "Samples",
        f"{len(cv_df)}",
        delta=f"call={int((cv_df['type'] == 'call').sum())} / put={int((cv_df['type'] == 'put').sum())}",
        delta_color="off",
    )

# Plotly scatter + line of equality
left, right = st.columns(2)

with left:
    st.subheader("Scatter: my_price vs pv_price")
    fig_scatter = go.Figure()
    fig_scatter.add_trace(
        go.Scatter(
            x=cv_df["pv_price"],
            y=cv_df["my_price"],
            mode="markers",
            marker={"size": 10, "color": cv_df["type"].map({"call": "#1f77b4", "put": "#d62728"})},
            text=[
                f"S={r.S:.0f}, K={r.K:.0f}, σ={r.sigma:.2f}, type={r.type}"
                for r in cv_df.itertuples()
            ],
            hovertemplate="%{text}<br>my=%{y:.6f}<br>pv=%{x:.6f}<extra></extra>",
            name="samples",
        )
    )
    # Line of equality y=x
    lo = float(min(cv_df["pv_price"].min(), cv_df["my_price"].min()))
    hi = float(max(cv_df["pv_price"].max(), cv_df["my_price"].max()))
    fig_scatter.add_trace(
        go.Scatter(
            x=[lo, hi],
            y=[lo, hi],
            mode="lines",
            line={"dash": "dash", "color": "gray"},
            name="y = x",
            showlegend=True,
        )
    )
    fig_scatter.update_layout(
        xaxis_title="py_vollib price",
        yaxis_title="自寫 BSM-Merton price",
        height=400,
        margin={"l": 60, "r": 20, "t": 20, "b": 60},
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

with right:
    st.subheader("Diff distribution (my - pv)")
    fig_hist = go.Figure()
    fig_hist.add_trace(
        go.Histogram(
            x=cv_df["diff"],
            nbinsx=20,
            marker={"color": "#2ca02c"},
            name="diff",
        )
    )
    fig_hist.update_layout(
        xaxis_title="my_price − pv_price",
        yaxis_title="樣本數",
        height=400,
        margin={"l": 60, "r": 20, "t": 20, "b": 60},
    )
    st.plotly_chart(fig_hist, use_container_width=True)

with st.expander("📋 50-sample 原始資料"):
    st.dataframe(
        cv_df.style.format(
            {
                "S": "{:.1f}",
                "K": "{:.1f}",
                "T_days": "{:.1f}",
                "q": "{:.4f}",
                "sigma": "{:.4f}",
                "my_price": "{:.6f}",
                "pv_price": "{:.6f}",
                "diff": "{:.2e}",
            }
        )
    )

st.divider()

# ===========================================================================
# Section 1.3 — Strategy Payoff Diagram
# ===========================================================================

st.header("1.3 到期 Payoff Diagram")

st.markdown(
    """
    **到期日 payoff** 是策略「最差情況下能賺多少 / 賠多少」的核心視覺化。下方 3 個 tab 展示
    本 repo 三種主力策略在到期日（T=0）的 PnL 分佈。x 軸為到期日標的價格 `S_T`，y 軸為策略 PnL（index points × NT$50/contract）。

    範例參數：spot 18000、選 strike 16800/17400/18600/19200、IC 收 net premium 200 點 ≈ NT$10,000/contract。
    """
)


def _ic_payoff(S_T: float, K1: float, K2: float, K3: float, K4: float, net_credit: float) -> float:
    """4-leg Iron Condor payoff at expiry (in index points).

    Long put K1 + Short put K2 + Short call K3 + Long call K4.
    Assumes K1 < K2 < K3 < K4.
    """
    long_put = max(K1 - S_T, 0)
    short_put = -max(K2 - S_T, 0)
    short_call = -max(S_T - K3, 0)
    long_call = max(S_T - K4, 0)
    return long_put + short_put + short_call + long_call + net_credit


def _bull_put_payoff(S_T: float, K1: float, K2: float, net_credit: float) -> float:
    """Bull put spread: long put K1 + short put K2 (K1 < K2). Bullish bias."""
    return max(K1 - S_T, 0) - max(K2 - S_T, 0) + net_credit


def _bear_call_payoff(S_T: float, K3: float, K4: float, net_credit: float) -> float:
    """Bear call spread: short call K3 + long call K4 (K3 < K4). Bearish bias."""
    return -max(S_T - K3, 0) + max(S_T - K4, 0) + net_credit


# Sample params
SPOT = 18000
ic_strikes = (16800, 17400, 18600, 19200)
ic_credit = 200
bull_strikes = (16800, 17400)
bull_credit = 100
bear_strikes = (18600, 19200)
bear_credit = 100

S_T_range = np.linspace(15500, 20500, 501)


def _make_payoff_fig(payoffs: np.ndarray, breakevens: list[float], title: str) -> go.Figure:
    fig = go.Figure()
    # Profit / loss color split
    colors = ["#2ca02c" if p > 0 else "#d62728" for p in payoffs]
    fig.add_trace(
        go.Scatter(
            x=S_T_range,
            y=payoffs,
            mode="lines",
            line={"width": 3, "color": "#1f77b4"},
            name="到期 PnL",
            fill="tozeroy",
            fillcolor="rgba(31, 119, 180, 0.15)",
        )
    )
    # 0 line
    fig.add_hline(y=0, line_color="black", line_width=1)
    # Spot vertical line
    fig.add_vline(
        x=SPOT,
        line_color="orange",
        line_dash="dot",
        annotation_text=f"spot {SPOT}",
        annotation_position="top",
    )
    # Breakeven points
    for be in breakevens:
        fig.add_vline(
            x=be,
            line_color="gray",
            line_dash="dash",
            annotation_text=f"BE {be:.0f}",
            annotation_position="top",
        )
    fig.update_layout(
        xaxis_title="到期 spot price (S_T)",
        yaxis_title="到期 PnL（index points）",
        title=title,
        height=400,
        margin={"l": 60, "r": 20, "t": 60, "b": 60},
    )
    # Suppress lint var unused
    _ = colors
    return fig


tab_ic, tab_bull, tab_bear = st.tabs(["Iron Condor", "Bull Put Spread", "Bear Call Spread"])

with tab_ic:
    payoffs_ic = np.array([_ic_payoff(s, *ic_strikes, ic_credit) for s in S_T_range])
    be_lower = ic_strikes[1] - ic_credit  # short put strike - credit
    be_upper = ic_strikes[2] + ic_credit  # short call strike + credit
    st.plotly_chart(
        _make_payoff_fig(
            payoffs_ic,
            [be_lower, be_upper],
            f"Iron Condor: long {ic_strikes[0]} put / short {ic_strikes[1]} put / "
            f"short {ic_strikes[2]} call / long {ic_strikes[3]} call (credit {ic_credit})",
        ),
        use_container_width=True,
    )
    cols = st.columns(4)
    cols[0].metric("Max Profit", f"+{ic_credit}", help="收 net credit；spot 落在 short K 中間時")
    cols[1].metric(
        "Max Loss",
        f"-{(ic_strikes[3] - ic_strikes[2]) - ic_credit}",
        help="(K4 − K3) − net credit；wing spread 寬度減去 credit",
    )
    cols[2].metric("Breakeven (lower)", f"{be_lower}", help="short put strike − credit")
    cols[3].metric("Breakeven (upper)", f"{be_upper}", help="short call strike + credit")

with tab_bull:
    payoffs_bull = np.array([_bull_put_payoff(s, *bull_strikes, bull_credit) for s in S_T_range])
    be_bull = bull_strikes[1] - bull_credit
    st.plotly_chart(
        _make_payoff_fig(
            payoffs_bull,
            [be_bull],
            f"Bull Put Spread: long {bull_strikes[0]} put / short {bull_strikes[1]} put "
            f"(credit {bull_credit})",
        ),
        use_container_width=True,
    )
    cols = st.columns(3)
    cols[0].metric("Max Profit", f"+{bull_credit}", help="spot 留在短 put 之上")
    cols[1].metric(
        "Max Loss",
        f"-{(bull_strikes[1] - bull_strikes[0]) - bull_credit}",
        help="spread 寬度 − net credit",
    )
    cols[2].metric("Breakeven", f"{be_bull}", help="short put strike − credit")

with tab_bear:
    payoffs_bear = np.array([_bear_call_payoff(s, *bear_strikes, bear_credit) for s in S_T_range])
    be_bear = bear_strikes[0] + bear_credit
    st.plotly_chart(
        _make_payoff_fig(
            payoffs_bear,
            [be_bear],
            f"Bear Call Spread: short {bear_strikes[0]} call / long {bear_strikes[1]} call "
            f"(credit {bear_credit})",
        ),
        use_container_width=True,
    )
    cols = st.columns(3)
    cols[0].metric("Max Profit", f"+{bear_credit}", help="spot 留在短 call 之下")
    cols[1].metric(
        "Max Loss",
        f"-{(bear_strikes[1] - bear_strikes[0]) - bear_credit}",
        help="spread 寬度 − net credit",
    )
    cols[2].metric("Breakeven", f"{be_bear}", help="short call strike + credit")

st.divider()

# ===========================================================================
# Section 1.4 — Greeks Sensitivity 互動 slider
# ===========================================================================

from src.options.greeks import delta, gamma, rho, theta, vega  # noqa: E402

st.header("1.4 Greeks Sensitivity 互動 slider")

st.markdown(
    """
    **Greeks** 是期權價格對各輸入參數的偏微分（partial derivative），
    衡量「該參數變動 1 單位時期權價格變多少」。**左側 sidebar 6 個 slider** 控制 BSM-Merton 模型輸入，
    右側 5-panel 圖即時重算 Δ / Γ / Θ / ν / ρ 對 spot price `S` 的曲線。

    **每個 slider 的意義**：

    | Slider | BSM 參數 | 影響 |
    |---|---|---|
    | `Strike K` | 履約價 | Δ / Γ 在 spot = K 達到 ATM peak；K 改變 → 整條曲線左右平移 |
    | `Time to expiry T（天）` | 距離到期天數 | T 越短 → Γ 越尖、Θ（時間衰減）越大；T = 0 時 Greeks 退化（undefined） |
    | `Implied vol σ %` | 隱含波動率 | σ 越大期權越貴；ν 反映「σ 變動 1.0 時期權價格變多少」 |
    | `Risk-free rate r %` | 年化無風險利率 | 影響折現項；ρ 反映期權對 r 的敏感度 |
    | `Dividend yield q %` | 連續股利率 | TAIEX 歷史 ≈ 3.5%；q 越大 call delta 越小（dividend drag） |
    | `Option type` | call / put | Δ 與 ρ 符號相反；Γ / ν 在 call / put 相同 |

    **單位慣例**（per CLAUDE.md §1，與 `py_vollib` 交叉對照）：
    - Δ Γ：per 1.0 spot move（無單位差）
    - ν：per 1.0 sigma move（py_vollib 對照需 ×0.01 換算 per 1%）
    - Θ：per-year calendar（÷365 才是 per-day decay）
    - ρ：per 1.0 rate move（py_vollib 對照需 ×0.01 換算 per 1%）

    code 路徑：[`src/options/greeks.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/options/greeks.py)
    """
)

# Sliders + option type radio (sidebar)
with st.sidebar:
    st.markdown("### Greeks 參數（Section 1.4）")
    st.caption("拖動以下 6 個參數，右側 5-panel 圖即時重算")
    g_K = st.slider(
        "Strike K（履約價）",
        14000,
        22000,
        18000,
        step=100,
        help="期權執行價。K 影響 Δ/Γ 曲線在何處達 ATM peak（即 spot=K 時）。",
    )
    g_T_days = st.slider(
        "Time to expiry T（到期天數）",
        1,
        90,
        30,
        help="距離到期日的天數。T 越短 → Γ 越尖銳、Θ（時間衰減）越大。",
    )
    g_sigma_pct = st.slider(
        "Implied vol σ %（隱含波動率）",
        5,
        60,
        20,
        help="市場 implied 的年化波動率。σ 越大期權越貴；ν（vega）反映期權對 σ 的敏感度。",
    )
    g_r_pct = st.slider(
        "Risk-free rate r %（無風險利率）",
        0.0,
        5.0,
        1.5,
        step=0.1,
        help="年化無風險利率（台灣 1Y 國債 ≈ 1.5%）。ρ（rho）反映期權對 r 的敏感度。",
    )
    g_q_pct = st.slider(
        "Dividend yield q %（股利率）",
        0.0,
        6.0,
        3.5,
        step=0.1,
        help="連續股利率（TAIEX 歷史平均約 3.5%）。q 越大 call delta 越小（dividend drag）。",
    )
    g_opt_type = st.radio(
        "Option type",
        ["call", "put"],
        horizontal=True,
        help="買權（call）vs 賣權（put）。Δ 與 ρ 符號相反；Γ / ν 在 call/put 相同。",
    )

# Convert slider units to internal (BSM expects decimal, T in years)
g_T = g_T_days / 365.0
g_sigma = g_sigma_pct / 100.0
g_r = g_r_pct / 100.0
g_q = g_q_pct / 100.0

# Spot range for x-axis
g_spot_range = np.linspace(g_K * 0.85, g_K * 1.15, 121)


@st.cache_data
def _compute_greeks_curve(
    spots: tuple[float, ...],
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    opt_type: str,
) -> dict[str, list[float]]:
    """Compute 5 Greeks per spot point. Cached on (slider) parameter tuple."""
    out = {"delta": [], "gamma": [], "theta": [], "vega": [], "rho": []}
    for S in spots:
        out["delta"].append(delta(S, K, T, r, q, sigma, option_type=opt_type))
        out["gamma"].append(gamma(S, K, T, r, q, sigma))
        out["theta"].append(theta(S, K, T, r, q, sigma, option_type=opt_type))
        out["vega"].append(vega(S, K, T, r, q, sigma))
        out["rho"].append(rho(S, K, T, r, q, sigma, option_type=opt_type))
    return out


greeks_data = _compute_greeks_curve(
    tuple(g_spot_range.tolist()), g_K, g_T, g_r, g_q, g_sigma, g_opt_type
)

# 5-panel subplot: Δ / Γ / Θ / ν / ρ
fig_greeks = make_subplots(
    rows=2,
    cols=3,
    subplot_titles=("Δ Delta", "Γ Gamma", "Θ Theta (per-year)", "ν Vega (per 1.0 σ)", "ρ Rho"),
    horizontal_spacing=0.08,
    vertical_spacing=0.18,
)

panel_layout = [
    ("delta", 1, 1),
    ("gamma", 1, 2),
    ("theta", 1, 3),
    ("vega", 2, 1),
    ("rho", 2, 2),
]
panel_colors = {
    "delta": "#1f77b4",
    "gamma": "#ff7f0e",
    "theta": "#d62728",
    "vega": "#2ca02c",
    "rho": "#9467bd",
}

for greek_name, row, col in panel_layout:
    fig_greeks.add_trace(
        go.Scatter(
            x=g_spot_range,
            y=greeks_data[greek_name],
            mode="lines",
            line={"width": 2.5, "color": panel_colors[greek_name]},
            showlegend=False,
        ),
        row=row,
        col=col,
    )
    # Strike vertical line
    fig_greeks.add_vline(x=g_K, line_color="gray", line_dash="dot", row=row, col=col)
    fig_greeks.update_xaxes(title_text="Spot S", row=row, col=col)

fig_greeks.update_layout(
    height=550,
    margin={"l": 60, "r": 20, "t": 50, "b": 50},
    title_text=f"K={g_K} / T={g_T_days}d / σ={g_sigma_pct}% / r={g_r_pct}% / q={g_q_pct}% / {g_opt_type}",
)

st.plotly_chart(fig_greeks, use_container_width=True)

# 顯示 ATM 數值（S=K）
atm_delta = delta(g_K, g_K, g_T, g_r, g_q, g_sigma, option_type=g_opt_type)
atm_gamma = gamma(g_K, g_K, g_T, g_r, g_q, g_sigma)
atm_theta = theta(g_K, g_K, g_T, g_r, g_q, g_sigma, option_type=g_opt_type)
atm_vega = vega(g_K, g_K, g_T, g_r, g_q, g_sigma)
atm_rho = rho(g_K, g_K, g_T, g_r, g_q, g_sigma, option_type=g_opt_type)

st.markdown("**ATM Greeks（S = K）**：")
cols = st.columns(5)
cols[0].metric("Δ", f"{atm_delta:+.4f}")
cols[1].metric("Γ", f"{atm_gamma:.6f}")
cols[2].metric("Θ (per yr)", f"{atm_theta:+.2f}", help=f"per-day ≈ {atm_theta / 365:.4f}")
cols[3].metric("ν (per 1.0 σ)", f"{atm_vega:.2f}", help=f"per 1% σ ≈ {atm_vega * 0.01:.4f}")
cols[4].metric("ρ", f"{atm_rho:+.2f}", help=f"per 1% rate ≈ {atm_rho * 0.01:.4f}")

st.divider()

# ===========================================================================
# Section 1.5 — SVI Vol Surface 3D
# ===========================================================================

import json  # noqa: E402

from utils import SURFACE_CACHE, list_surface_fit_dates, load_surface_fit  # noqa: E402

st.header("1.5 SVI Vol Surface 3D")

st.markdown(
    """
    本 repo 對 5 年回測窗口（2021-04 ~ 2026-04，1227 個交易日）每日 fit per-expiry 的 vol surface（SVI 5 參數
    優先 → SABR 4 參數 fallback → 多項式 degree-2 last resort）。

    SVI raw form（Gatheral 2014）：

    ```
    w(k) = a + b · {ρ·(k − m) + √((k − m)² + σ²)}
    ```

    其中 `w = σ²·T` 為 total variance，`k = ln(K/F)` 為 log-moneyness（F = forward = `S·exp((r−q)T)`）。
    arb-free 守衛：Lee (2004) `b ≤ 4/(T·(1+|ρ|))` + butterfly check + `|ρ| < 1` + `σ > 0`。

    code 路徑：[`src/options/vol_surface.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/options/vol_surface.py)
    + [`src/options/surface_cache.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/options/surface_cache.py)
    """
)

if not SURFACE_CACHE.exists():
    st.warning(
        "Surface cache 不存在 — 請先跑 `python scripts/_validate_surface_mark_5_4a.py` 建置 1227 shards。",
        icon="⚠️",
    )
else:
    available_dates = list_surface_fit_dates()
    if not available_dates:
        st.warning("Surface cache 內無 parquet 檔。", icon="⚠️")
    else:
        # Default to 2025-09-15 (or middle of available range)
        default_idx = (
            available_dates.index("2025-09-15")
            if "2025-09-15" in available_dates
            else len(available_dates) // 2
        )
        selected_date = st.selectbox(
            f"選擇 fit date（共 {len(available_dates)} 個交易日）",
            available_dates,
            index=default_idx,
        )

        fit_df = load_surface_fit(selected_date)
        # 只看 SVI fit + converged
        svi_df = fit_df[(fit_df["model_type"] == "svi") & fit_df["converged"]].reset_index(
            drop=True
        )

        if svi_df.empty:
            st.warning(f"{selected_date} 無 SVI converged fit。請選其他日期。", icon="⚠️")
        else:
            st.markdown(
                f"**{selected_date}**：{len(svi_df)} 個 expiry SVI fit converged，"
                f"forward = `{svi_df['forward'].iloc[0]:.2f}`"
            )

            # Compute SVI implied vol on a common log-moneyness grid
            k_grid = np.linspace(-0.30, 0.30, 81)
            iv_matrix = np.zeros((len(svi_df), len(k_grid)))
            expiries: list[str] = []
            T_values: list[float] = []
            forwards: list[float] = []

            for i, row in svi_df.iterrows():
                params = json.loads(row["params_json"])
                a, b, m, rho_p, sig = (
                    params["a"],
                    params["b"],
                    params["m"],
                    params["rho"],
                    params["sigma"],
                )
                # SVI total variance w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2))
                w = a + b * (rho_p * (k_grid - m) + np.sqrt((k_grid - m) ** 2 + sig**2))
                w = np.maximum(w, 1e-12)  # guard against tiny negative numerical residual
                T_yrs = float(row["T"])
                iv = np.sqrt(w / max(T_yrs, 1e-9))
                iv_matrix[i, :] = iv
                expiries.append(str(row["expiry"]))
                T_values.append(T_yrs)
                forwards.append(float(row["forward"]))

            # Compute strike grid for each expiry: K = F · exp(k)
            forward_const = forwards[0]  # All same forward in single-day data
            strike_grid = forward_const * np.exp(k_grid)
            T_array = np.array(T_values)
            dte_array = T_array * 365

            left, right = st.columns([3, 2])

            with left:
                st.subheader("3D Surface（IV vs Strike × DTE）")
                fig_3d = go.Figure(
                    data=[
                        go.Surface(
                            x=strike_grid,
                            y=dte_array,
                            z=iv_matrix,
                            colorscale="Viridis",
                            colorbar={"title": "IV"},
                            hovertemplate="K=%{x:.0f}<br>DTE=%{y:.0f}d<br>IV=%{z:.4f}<extra></extra>",
                        )
                    ]
                )
                fig_3d.update_layout(
                    scene={
                        "xaxis_title": "Strike K",
                        "yaxis_title": "DTE (days)",
                        "zaxis_title": "Implied Vol σ",
                    },
                    height=500,
                    margin={"l": 0, "r": 0, "t": 30, "b": 0},
                )
                st.plotly_chart(fig_3d, use_container_width=True)

            with right:
                st.subheader("IV Smile 截面")
                # Pick a single expiry (DTE-closest-to-30)
                idx_closest = int(np.argmin(np.abs(dte_array - 30)))
                exp_label = expiries[idx_closest]
                iv_slice = iv_matrix[idx_closest, :]

                fig_smile = go.Figure()
                fig_smile.add_trace(
                    go.Scatter(
                        x=strike_grid,
                        y=iv_slice,
                        mode="lines",
                        line={"width": 3, "color": "#1f77b4"},
                        name=f"DTE {dte_array[idx_closest]:.0f}d",
                    )
                )
                fig_smile.add_vline(
                    x=forward_const,
                    line_color="orange",
                    line_dash="dot",
                    annotation_text=f"F={forward_const:.0f}",
                )
                fig_smile.update_layout(
                    xaxis_title="Strike K",
                    yaxis_title="IV σ",
                    title=f"Expiry {exp_label}",
                    height=400,
                    margin={"l": 60, "r": 20, "t": 50, "b": 60},
                )
                st.plotly_chart(fig_smile, use_container_width=True)

            # SVI 參數表
            st.subheader("SVI 5 參數（per expiry）")
            params_rows = []
            for i, row in svi_df.iterrows():
                p = json.loads(row["params_json"])
                params_rows.append(
                    {
                        "expiry": row["expiry"],
                        "DTE": f"{T_values[i] * 365:.0f}d",
                        "a": p["a"],
                        "b": p["b"],
                        "m": p["m"],
                        "ρ": p["rho"],
                        "σ_param": p["sigma"],
                        "n_points": int(row["n_points"]),
                        "RMSE": row["in_sample_rmse"],
                    }
                )
            st.dataframe(
                pd.DataFrame(params_rows).style.format(
                    {
                        "a": "{:.4f}",
                        "b": "{:.4f}",
                        "m": "{:.4f}",
                        "ρ": "{:.4f}",
                        "σ_param": "{:.4f}",
                        "RMSE": "{:.4f}",
                    }
                )
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
    st.page_link("pages/2_Walk-forward結果.py", label="Page 2 — Walk-forward 結果 →", icon="📈")
with nav_cols[2]:
    st.page_link("pages/3_Audit紀律與Bug修法.py", label="Page 3 — Audit 紀律 →", icon="🛡️")
