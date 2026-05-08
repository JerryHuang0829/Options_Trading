"""Dashboard 共用 path constant + cached data loader。

Mirror Stock-Trading dashboard/utils.py pattern：集中 path 常數避免散落各 page，
資料 loader 加 @st.cache_data 防止每次 page rerun 重讀大檔。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Path constants（多 page 共用）
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS = PROJECT_ROOT / "reports"
SURFACE_CACHE = PROJECT_ROOT / "data" / "taifex_cache" / "surface_fits"
STRATEGY_VIEW_CACHE = PROJECT_ROOT / "data" / "taifex_cache" / "strategy_view"

# Add repo root to sys.path so dashboard pages can `from src.options.pricing import ...`
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 6 scenario 顯示名稱對照（Walk-forward 結果頁用）
# ---------------------------------------------------------------------------

SCENARIO_DISPLAY_NAMES = {
    "IC_vanilla": "Iron Condor (vanilla)",
    "IC_IV_percentile": "Iron Condor (IV percentile gate)",
    "IC_HMM": "Iron Condor (HMM 2-state regime)",
    "Vertical_vanilla": "Vertical (vanilla)",
    "Vertical_IV_percentile": "Vertical (IV percentile gate)",
    "Vertical_HMM": "Vertical (HMM 2-state regime)",
}

# 6 scenario plot 顏色（Plotly default cycling friendly）
SCENARIO_COLORS = {
    "IC_vanilla": "#1f77b4",
    "IC_IV_percentile": "#aec7e8",
    "IC_HMM": "#7f7f7f",
    "Vertical_vanilla": "#d62728",
    "Vertical_IV_percentile": "#ff9896",
    "Vertical_HMM": "#bcbd22",
}

# Phase 1 路線圖（首頁時間軸用）
PHASE1_TIMELINE = [
    ("Week 1", "Options 數學核心", "BSM-Merton + 5 Greeks + IV solver + py_vollib 交叉驗證"),
    (
        "Week 2",
        "Strategy + Backtest + Risk",
        "Iron Condor / Vertical 策略 + 4 hard gate Risk Layer + 4 FillModel",
    ),
    ("Week 3", "TAIFEX 資料管線", "8 年 1963-shard 真期權鏈 + Big5 解碼 + parquet 雙層 cache"),
    (
        "Week 4-5",
        "Vol Surface 波動率曲面",
        "SVI 5 參數 + SABR 4 參數 + 多項式 fallback；arb-free guard；1227 shard",
    ),
    (
        "Week 6",
        "5 年 walk-forward 回測",
        "6 scenario × 15 disjoint quarterly OOS folds + Pro 統計工具鏈",
    ),
    (
        "Week 7",
        "Hedged IC + Cost Ablation",
        "Calendar hedge overlay + retail cost ablation + Quick A 收尾",
    ),
]


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------


@st.cache_data
def load_5yr_scenarios() -> pd.DataFrame:
    """6 scenario × walk-forward 摘要（Sharpe / CI / DSR / maxDD / Calmar / trades）。"""
    return pd.read_csv(REPORTS / "week6_5yr_scenarios.csv")


@st.cache_data
def load_5yr_daily_pnl() -> pd.DataFrame:
    """5 年 daily PnL（5670 row = 6 scenario × 945 obs）；date 欄轉 Timestamp。"""
    df = pd.read_csv(REPORTS / "week6_5yr_daily_pnl.csv")
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data
def load_5yr_folds() -> pd.DataFrame:
    """Per-fold metrics（90 row = 6 scenario × 15 fold）。"""
    return pd.read_csv(REPORTS / "week6_5yr_folds.csv")


@st.cache_data
def load_5yr_no_cost_vs_with_cost() -> pd.DataFrame:
    """Retail cost ablation 對照（with-cost vs no-cost Sharpe）。"""
    return pd.read_csv(REPORTS / "week6_5yr_no_cost_vs_with_cost.csv")


@st.cache_data
def load_surface_fit(date_str: str) -> pd.DataFrame:
    """單日 SVI / SABR / poly fit 結果（vol surface 3D 用）。

    Args:
        date_str: ISO 日期（如 "2025-12-15"），檔案位於 surface_fits/<YYYY>/<date>.parquet。
    """
    year = date_str[:4]
    return pd.read_parquet(SURFACE_CACHE / year / f"{date_str}.parquet")


@st.cache_data
def list_surface_fit_dates() -> list[str]:
    """列出所有可用 surface fit 日期（用 dropdown 選擇）。"""
    if not SURFACE_CACHE.exists():
        return []
    paths = sorted(SURFACE_CACHE.rglob("*.parquet"))
    return [p.stem for p in paths]
