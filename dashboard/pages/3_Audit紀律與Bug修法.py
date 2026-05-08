"""Page 3 — Audit 紀律與 Bug 修法。

展示 senior engineering 紀律：14+1 輪 external review chain closed + 內部驗證抓 silent bug 修法。
重點為 framework 自我審計能力 demonstration，不是「我犯過多少錯」。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from utils import PROJECT_ROOT, SCENARIO_DISPLAY_NAMES  # noqa: F401

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Audit 紀律與 Bug 修法 | Options_Trading", page_icon="🛡️", layout="wide"
)

st.title("Page 3 — Audit 紀律與 Bug 修法")
st.caption(
    "14+1 輪 external review chain · 4 件 hard gate · "
    "agg_max_drawdown silent bug deep dive · Pro methodology 紀律"
)

st.divider()

# ===========================================================================
# Section 3.1 — 14+1 輪 External Review Chain Timeline
# ===========================================================================

st.header("3.1 External Review Chain 紀律")

st.markdown(
    """
    **External review chain** 為 adversary-in-good-faith 的獨立審查機制：每完成一個 milestone，
    將 commit + diff + test result 整理成 review prompt 送外部審查，
    主動找 silent bug、edge case、數學錯誤、單位混淆等問題。

    Phase 1 經 14 輪 R12.0 ~ R12.13 連續 review chain（2026-04-25 ~ 2026-05-02），全部 P0/P1 patches closed；
    2026-05-05 加碼一輪外部 review 抓到 `agg_max_drawdown` silent bug（見 Section 3.3 deep dive），
    1 行修法 + 2 條 regression test 收口。

    這個機制比單獨靠自我 review 多一層獨立驗證 — reviewer 看 portfolio 應該關注的不只「我寫了什麼」，
    更是「我接受多少外部 attacker challenge」。
    """
)

# Audit rounds summary table
audit_rounds = [
    (
        "R12.0",
        "2026-04-29",
        4,
        "permutation 改 sign-flip / walk-forward step disjoint / TAIFEX tax 10 bps",
    ),
    ("R12.1", "2026-04-30", 4, "Bootstrap CI / Deflated Sharpe / Calmar / cost ablation 加上"),
    ("R12.2", "2026-04-30", 1, "rejected_reasons accumulator per-fold snapshot"),
    ("R12.3", "2026-05-01", 1, "5yr surface coverage 100% gate（1227 shards）"),
    ("R12.4", "2026-05-01", 2, "_extract_rejected_reasons unwrap depth (16) + cycle safe"),
    (
        "R12.5",
        "2026-05-01",
        1,
        "n_fallback_settle_3rd audit metric 區分 surface degraded vs direct",
    ),
    ("R12.6", "2026-05-01", 2, "PIT correctness sweeps / FillModel side-specific NaN guards"),
    (
        "R12.7",
        "2026-05-02",
        1,
        "Mark policy hybrid: strict_mid → settle_fallback → surface_fallback",
    ),
    ("R12.8", "2026-05-02", 1, "engine `cum_pnl = realised + unrealised` invariant"),
    ("R12.9", "2026-05-02", 2, "TAIFEX schema 3-way drift detection / Big5 magic-bytes guard"),
    ("R12.10", "2026-05-02", 1, "HMM convergence warning tracking（792 warns over 5yr backtest）"),
    ("R12.11", "2026-05-02", 1, "Test baseline 463 → 465（+2 regression for next-round fix）"),
    (
        "R12.12",
        "2026-05-02",
        1,
        "audit_doc_drift.py automated gate（stale audit refs / absolute claim）",
    ),
    ("R12.13", "2026-05-02", 1, "Phase 1 chain 收口；Week 7 hedged IC + Quick A 驗證"),
    ("2026-05-05", "2026-05-05", 1, "agg_max_drawdown silent bug（Section 3.3 deep dive）"),
]

audit_df = pd.DataFrame(audit_rounds, columns=["Round", "Date", "P fixes", "Main fix categories"])

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Audit rounds", "14 + 1", help="R12.0 ~ R12.13 chain + 2026-05-05 加碼")
with col2:
    st.metric(
        "Total P fixes closed",
        f"{int(audit_df['P fixes'].sum())}",
        help="P0 (critical) + P1 (must-fix) + P2 (caveat)",
    )
with col3:
    st.metric("Days span", "2026-04-29 ~ 2026-05-05", help="Phase 1 audit chain 7 day span")

st.dataframe(
    audit_df.style.format({"P fixes": "{:.0f}"}),
    use_container_width=True,
    hide_index=True,
)

# Plotly timeline / cumulative fix count
audit_df["Date_dt"] = pd.to_datetime(audit_df["Date"])
audit_df = audit_df.sort_values("Date_dt").reset_index(drop=True)
audit_df["cum_fixes"] = audit_df["P fixes"].cumsum()

fig_timeline = go.Figure()
fig_timeline.add_trace(
    go.Scatter(
        x=audit_df["Date_dt"],
        y=audit_df["cum_fixes"],
        mode="lines+markers",
        line={"width": 3, "color": "#1f77b4"},
        marker={"size": 12, "color": "#1f77b4"},
        text=audit_df["Round"],
        hovertemplate=("<b>%{text}</b><br>%{x|%Y-%m-%d}<br>Cumulative fixes: %{y}<extra></extra>"),
        name="Cumulative P fixes",
    )
)
fig_timeline.update_layout(
    xaxis_title="Date",
    yaxis_title="Cumulative P fixes closed",
    title="Audit chain 累積 P fix 數",
    height=400,
    margin={"l": 60, "r": 20, "t": 50, "b": 60},
    showlegend=False,
)

st.plotly_chart(fig_timeline, use_container_width=True)

st.caption(
    "Review prompt 模板（每輪 milestone 重寫）封裝 commit / diff / test result，外部 reviewer 可 1-click 產出 P0/P1/P2 fix list。"
    "本 repo 紀律是收到 P fix 後**先 e2e regression test 再 commit**（避 silent bug 二次潛入）。"
)

st.divider()

# ===========================================================================
# Section 3.2 — 4 件 Hard Gate Latest Run
# ===========================================================================

st.header("3.2 4 件 Hard Gate Latest Run")

st.markdown(
    """
    每個 commit 必通過 4 件 hard gate（per CLAUDE.md §2 紀律）：

    | 項目 | 檢查內容 |
    |---|---|
    | `ruff check` | Lint（包含 `pep8-naming` / `pyflakes` / `isort` / `bugbear` / `simplify` / `numpy` / `pandas` 規則組） |
    | `ruff format --check` | 格式 PEP 8 強制（line length 100 / Black 風格 / trailing commas） |
    | `mypy` | 靜態型別檢查（含 pandas-stubs，98 source files） |
    | `pytest` | 全 regression（465 tests, 2 skipped, ~190 秒）|

    違反任一件則 commit 視為未完成；此外另有 `audit_doc_drift.py` 自動偵測過時 audit 引用 / stale baseline 數字 / absolute claim。
    """
)

# Hardcoded latest hard gate run（最後一次正式 verification, 2026-05-08）
gate_results = [
    {
        "Gate": "ruff check src tests config scripts",
        "Status": "✅ PASS",
        "Detail": "All checks passed (98 files)",
    },
    {
        "Gate": "ruff format --check src tests config scripts",
        "Status": "✅ PASS",
        "Detail": "97 files already formatted",
    },
    {
        "Gate": "mypy src tests config scripts",
        "Status": "✅ PASS",
        "Detail": "Success: no issues found in 98 source files",
    },
    {"Gate": "pytest tests/ -q", "Status": "✅ PASS", "Detail": "465 passed, 2 skipped (~190s)"},
    {
        "Gate": "python scripts/audit_doc_drift.py",
        "Status": "✅ PASS",
        "Detail": "0 drift, 2 pre-existing absolute_claim warnings",
    },
    {
        "Gate": "python scripts/_dummy_backtest_pipeline_check.py",
        "Status": "✅ PASS",
        "Detail": "End-to-end pipeline OK (41 days, 3 closed trades)",
    },
]
gate_df = pd.DataFrame(gate_results)


# Highlight green for all PASS
def _color_status(val: str) -> str:
    if "PASS" in val:
        return "color: #2ca02c; font-weight: bold;"
    return "color: #d62728; font-weight: bold;"


styled_gate = gate_df.style.map(_color_status, subset=["Status"])

st.dataframe(styled_gate, use_container_width=True, hide_index=True)

cols = st.columns(4)
with cols[0]:
    st.metric("Hard gate items", "4 件", delta="+ 2 supplementary", delta_color="off")
with cols[1]:
    st.metric(
        "Tests passed",
        "465 / 465",
        delta="2 skipped",
        delta_color="off",
        help="2 skip 為 position-still-open invariant 已知 case",
    )
with cols[2]:
    st.metric("Source files", "98", delta="all type-checked", delta_color="off")
with cols[3]:
    st.metric("Latest run", "2026-05-08", delta="conda options env", delta_color="off")

st.caption(
    "本 dashboard 頁載入時不重跑 hard gate（避免互動延遲）；"
    "上方為最近一次手動驗證結果。實際指令見專案根 `README.md` 的 Quick Start 段。"
)

st.divider()

# ===========================================================================
# Section 3.3 — agg_max_drawdown silent bug deep dive
# ===========================================================================

st.header("3.3 Critical Bug Catch: `agg_max_drawdown` silent metric inflation")

st.markdown(
    """
    **2026-05-05 external review 抓到 `walk_forward._aggregate_folds` 的口徑 bug**：
    把 daily PnL 直接傳入 `metrics.max_drawdown`，而 function contract 明定要 cumulative PnL。
    結果是所有 6 個 scenario 的 `agg_max_drawdown` 報表數字被 1.3 ~ 4.2 倍低估。

    這個 bug 本身展示一個 senior engineering reality：**function contract 違反靠 type system 抓不到**
    （兩者都是 `pd.Series[float]`），只能靠 docstring + 紀律 + cross-check。reviewer 看本案例
    應該關注的是「**框架抓得到 silent metric inflation 並補 regression test 防再犯**」。
    """
)

# --- 3.3.1 Bug 描述 ---
st.subheader("3.3.1 Bug 描述")

st.markdown(
    """
    **`metrics.max_drawdown` function contract**（[`src/backtest/metrics.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/metrics.py) docstring）：

    > Args:
    >     `cumulative_pnl`: Running cumulative PnL series (TWD), where index 0
    >         is the cumulative PnL **at end of day 1** (entry baseline = 0).

    函式內部用 `series.cummax()` 計算 running peak，再 `series - running_max` 取 min 得 max drawdown。
    這個算法**只在 cumulative PnL 上有經濟意義**（peak 是「累積盈到哪」，trough 是「peak 後虧到哪」）。
    對 daily PnL 跑 cummax 會把單日最大盈當 peak，得到無意義的 drawdown 數字。
    """
)

st.markdown("**Buggy caller**（修法前 `walk_forward.py:367`）：")
st.code(
    """# walk_forward._aggregate_folds (修法前)
agg_metrics["max_drawdown"] = max_drawdown(agg_pnl, initial_capital=initial_capital)
# ↑ agg_pnl 是 concat 所有 fold 的 daily PnL（不是 cumulative）— silent contract violation
""",
    language="python",
)

st.markdown(
    "**正確 caller**（[`engine.py:358-363`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/engine.py)）："
)
st.code(
    """# engine.run_backtest (主路徑寫對的)
cumulative = daily_pnl_series.cumsum()  # ← 先 cumsum 才符合 contract
metrics = {
    "sharpe": sharpe_ratio(daily_pnl_series, initial_capital=initial_capital),
    "max_drawdown": max_drawdown(cumulative, initial_capital=initial_capital),
    "win_rate": win_rate(trades_df) if not trades_df.empty else 0.0,
}
""",
    language="python",
)

st.markdown(
    """
    對照下：fold-level metrics（每 fold 的 `metric_max_drawdown`）走 engine.run_backtest 主路徑，**正確**；
    aggregate 層 `_aggregate_folds` 是唯一 silent 違反的地方。
    """
)

# --- 3.3.2 影響量化 ---
st.subheader("3.3.2 影響量化（published 報告 vs 正確值）")

st.markdown(
    """
    用相同 daily PnL 序列重算 max drawdown 兩種口徑（daily input vs cumulative input），
    對照 published `agg_max_drawdown` 與真值。倍數低估 = correct / published。
    """
)


@st.cache_data
def _compute_maxdd_comparison():
    """重算 6 scenario 的 daily-input vs cumulative-input maxDD，對照 published 值。"""
    import pandas as pd
    from utils import load_5yr_daily_pnl

    from src.backtest.metrics import max_drawdown

    initial_capital = 1_000_000.0
    daily = load_5yr_daily_pnl()

    rows = []
    for sc in daily["scenario"].unique():
        sub = daily[daily["scenario"] == sc].sort_values("date")
        pnl = sub["daily_pnl_twd"].astype(float).values
        # Bug behaviour: pass daily PnL directly
        s_daily = pd.Series(pnl)
        bug_dd = max_drawdown(s_daily, initial_capital=initial_capital)
        # Correct: pass cumulative PnL
        s_cum = pd.Series(pnl).cumsum()
        correct_dd = max_drawdown(s_cum, initial_capital=initial_capital)

        ratio = correct_dd / bug_dd if abs(bug_dd) > 1e-12 else float("nan")
        rows.append(
            {
                "scenario": sc,
                "Published (daily input, BUG)": bug_dd,
                "Correct (cumulative)": correct_dd,
                "倍數低估": ratio,
            }
        )
    return pd.DataFrame(rows)


comparison_df = _compute_maxdd_comparison()
display_comparison = comparison_df.copy()
display_comparison["scenario"] = (
    display_comparison["scenario"]
    .map(SCENARIO_DISPLAY_NAMES)
    .fillna(display_comparison["scenario"])
)
display_comparison.columns = ["Strategy", "Published (BUG)", "Correct (cumsum)", "倍數低估"]

st.dataframe(
    display_comparison.style.format(
        {"Published (BUG)": "{:.4%}", "Correct (cumsum)": "{:.4%}", "倍數低估": "{:.2f}x"},
        na_rep="—",
    ),
    use_container_width=True,
    hide_index=True,
)

# Bar chart: 低估倍數 by scenario
plot_df = comparison_df[comparison_df["倍數低估"].notna() & (comparison_df["倍數低估"] != 0)].copy()
fig_ratio = go.Figure()
fig_ratio.add_trace(
    go.Bar(
        x=[SCENARIO_DISPLAY_NAMES.get(s, s) for s in plot_df["scenario"]],
        y=plot_df["倍數低估"],
        marker={"color": "#d62728"},
        text=[f"{v:.2f}x" for v in plot_df["倍數低估"]],
        textposition="outside",
        hovertemplate="%{x}<br>低估 %{y:.2f}x<extra></extra>",
    )
)
fig_ratio.add_hline(
    y=1.0,
    line_color="black",
    line_width=2,
    line_dash="dash",
    annotation_text="1.0x = 無偏置",
    annotation_position="right",
)
fig_ratio.update_layout(
    yaxis_title="published / correct（低估倍數）",
    title="6 scenario maxDD 低估倍數（值 > 1 表示 published 數字偏小）",
    height=400,
    margin={"l": 60, "r": 20, "t": 50, "b": 100},
    xaxis_tickangle=-15,
    showlegend=False,
)
st.plotly_chart(fig_ratio, use_container_width=True)

st.warning(
    "**Vertical_vanilla 報表 maxDD -1.66% 真值 -6.94%（4.2x 低估）**；若 Phase 1 出口條件含 Max DD < 5% gate，"
    "錯誤值會誤判通過。本 bug 不變 GO/NO-GO 結論（Sharpe 全負本來就 fail Phase 1 主 gate），"
    "但口徑必須對 — 未來新策略若真到 18% 區，會被誤判通過 5% gate。",
    icon="⚠️",
)

# --- 3.3.3 檢測方法 ---
st.subheader("3.3.3 檢測方法")

st.markdown(
    """
    1. **External review prompt**：把 `walk_forward.py` + `metrics.py` 的 docstring contract +
       published `scenarios.csv` 數字打包送外部 reviewer 做 attacker review
    2. **Independent re-compute**：reviewer 跑 inline Python 對 `daily_pnl.csv` 重算 6 scenario 的 maxDD
       兩種口徑（daily-input vs cumulative-input）
    3. **Diff vs published**：對照表發現 daily-input 的結果**精確等於**published 數字
       → 確認 caller 路徑誤傳了 daily（而非 cumulative）
    4. **Function contract check**：回頭看 `metrics.py` docstring 與 grep 全 repo，確認 contract 寫的是 cumulative
       且 engine.py 主路徑（fold-level）有正確 `cumsum()`
    """
)

st.code(
    """# 重算驗證（inline Python，2026-05-05）
import pandas as pd
INIT_CAP = 1_000_000.0

daily = pd.read_csv('reports/week6_5yr_daily_pnl.csv', parse_dates=['date'])
for sc in daily.scenario.unique():
    pnl = daily[daily.scenario == sc].sort_values('date').daily_pnl_twd.values

    # Bug: pass daily directly
    s_d = pd.Series(pnl)
    rm_d = s_d.cummax().clip(lower=0.0)
    dd_d = (s_d - rm_d).min() / INIT_CAP

    # Correct: cumsum first
    s_c = pd.Series(pnl).cumsum()
    rm_c = s_c.cummax().clip(lower=0.0)
    dd_c = (s_c - rm_c).min() / INIT_CAP

    print(f'{sc}: published(bug)={dd_d:+.4%}, correct(cumsum)={dd_c:+.4%}')
""",
    language="python",
)

# --- 3.3.4 修法 + Regression Test ---
st.subheader("3.3.4 修法 + Regression Test")

st.markdown(
    """
    **1 行核心修法**（[`src/backtest/walk_forward.py:367`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/walk_forward.py)）：
    """
)

st.code(
    """# Before
agg_metrics["max_drawdown"] = max_drawdown(agg_pnl, initial_capital=initial_capital)

# After
# max_drawdown contract requires cumulative PnL (see metrics.py docstring).
# engine.run_backtest passes daily_pnl_series.cumsum(); mirror that here.
agg_metrics["max_drawdown"] = max_drawdown(
    agg_pnl.cumsum(), initial_capital=initial_capital
)
""",
    language="python",
)

st.markdown(
    """
    **2 條新 regression test**（[`tests/backtest/test_walk_forward.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/tests/backtest/test_walk_forward.py)）：
    """
)

with st.expander("🧪 test_aggregate_max_drawdown_uses_cumulative — 直接 known-case"):
    st.code(
        """def test_aggregate_max_drawdown_uses_cumulative() -> None:
    \"\"\"Direct: known daily PnL → cumulative trough → expected maxDD.

    daily PnL: [+100, -300, +50, -200] over 4 days
    cumulative: [+100, -200, -150, -350]
    running peak (incl. 0 baseline): [+100, +100, +100, +100]
    drawdown: [0, -300, -250, -450] → min = -450
    maxDD as fraction of $1M cap = -0.00045
    \"\"\"
    from src.backtest.walk_forward import _aggregate_folds

    initial_capital = 1_000_000.0
    dates = pd.date_range("2026-01-05", periods=4, freq="D")
    daily_pnl = pd.Series([100.0, -300.0, 50.0, -200.0], index=dates)
    fold = _make_fold(0, dates[0], dates[-1], daily_pnl)

    result = _aggregate_folds([fold], initial_capital=initial_capital)

    expected_max_dd = -450.0 / initial_capital  # = -0.00045
    assert result.metrics["max_drawdown"] == pytest.approx(expected_max_dd, abs=1e-12), (
        f"agg max_drawdown should reflect cumulative trough -450/-{initial_capital:.0f} "
        f"= {expected_max_dd:.6f}; got {result.metrics['max_drawdown']:.6f}. "
        f"If close to -0.0003 (=-300/1e6), bug regressed: daily PnL is being passed "
        f"to max_drawdown instead of cumsum."
    )
""",
        language="python",
    )

with st.expander("🧪 test_aggregate_max_drawdown_consistent_with_engine_path — cross-consistency"):
    st.code(
        """def test_aggregate_max_drawdown_consistent_with_engine_path() -> None:
    \"\"\"Cross-consistency: feed concat'd daily PnL through both
    (a) _aggregate_folds (walk_forward path) and (b) engine.metrics path
    (cumsum then max_drawdown). Both must agree to ~float precision.
    \"\"\"
    from src.backtest.metrics import max_drawdown
    from src.backtest.walk_forward import _aggregate_folds

    initial_capital = 1_000_000.0
    rng = np.random.default_rng(seed=42)
    daily_pnl_values = rng.normal(loc=-50.0, scale=500.0, size=120)
    dates = pd.date_range("2026-01-02", periods=120, freq="B")
    daily_pnl = pd.Series(daily_pnl_values, index=dates)
    fold0 = _make_fold(0, dates[0], dates[59], daily_pnl.iloc[:60])
    fold1 = _make_fold(1, dates[60], dates[-1], daily_pnl.iloc[60:])

    agg = _aggregate_folds([fold0, fold1], initial_capital=initial_capital)

    expected = max_drawdown(daily_pnl.cumsum(), initial_capital=initial_capital)
    assert agg.metrics["max_drawdown"] == pytest.approx(expected, abs=1e-12)
""",
        language="python",
    )

st.markdown(
    """
    這兩條 test 形成防線：第一條 lock 算法（known case）；第二條 lock caller 路徑與 engine.py 主路徑一致。
    任一條 fail 都代表 caller 違反 contract。修完後重跑 `_validate_week6_5yr.py` 與
    `_validate_week7_hedged_ic.py` 重生 reports，published `agg_max_drawdown` 數字隨之修正。
    """
)

# --- 3.3.5 教訓 ---
st.subheader("3.3.5 教訓")

st.markdown(
    """
    **1. Function contract 違反靠 type system 抓不到。** `daily_pnl: pd.Series` 與 `cumulative_pnl: pd.Series`
    在 mypy 看來完全一樣。Type 系統能抓「型別」，抓不到「語意」。

    **2. Docstring 是 caller 的 source of truth；caller 必須讀。** Function 寫 `cumulative_pnl` 的 caller
    若不讀 docstring 直接傳 daily PnL，是 silent 違約。

    **3. Cross-consistency 是最強守衛。** 同一份 input 兩條獨立路徑算同一個 metric，結果應一致；
    新加的 cross-consistency test 直接 lock 此不變式。

    **4. `cumsum()` 一行修法的代價。** Bug 是 1 行誤用 → 6 個 scenario 5yr 報表 maxDD 全錯 → 重跑兩個 validate
    script ~38 分鐘 → 文件同步更新。Senior engineering 紀律：寫 caller 前先看 callee docstring；寫 helper 前先想
    contract violation 的 attack vector。

    **5. External review chain 真的會抓到這種 bug。** 前 14 輪沒抓到 → 第 15 輪外加（2026-05-05）抓到。
    Reviewer 看 portfolio 重點是「歡迎被攻破」的態度，不是「我從沒犯錯」的假象。
    """
)

st.divider()

# ===========================================================================
# Section 3.4 — Pro Methodology 紀律 4 badges
# ===========================================================================

st.header("3.4 Pro Methodology 紀律")

st.markdown(
    """
    本 repo 採學術界 + buy-side 量化 reference 作 statistical tooling，4 件 Pro methodology
    封裝在 [`src/backtest/stats.py`](https://github.com/JerryHuang0829/Options_Trading/blob/main/src/backtest/stats.py)。
    每件含 paper reference + 內部 contract + 對應 caller path。
    """
)

c1, c2 = st.columns(2)

with c1, st.container(border=True):
    st.markdown("### 📊 Bootstrap Percentile CI")
    st.markdown(
        """
            對 daily PnL **resample with replacement** N 次（預設 1000），每次重算 statistic（預設 Sharpe），
            取 2.5% / 97.5% percentile 作 95% CI 下/上界。**不假設常態分布**，捕捉長尾風險。

            **判讀**：CI 完全在 0 一側 → 統計顯著；CI 跨零 → 無法 reject H0（Sharpe = 0）。

            **Function**：`bootstrap_ci(daily_pnl, statistic, n_iter, ci, seed) → (lower, upper)`
            支援 `'sharpe' | 'mean' | 'total_return' | callable` 4 種 statistic。
            """
    )
    st.caption(
        "**Reference**：Efron & Tibshirani (1993). *An Introduction to the Bootstrap*. Chapman & Hall."
    )

with c2, st.container(border=True):
    st.markdown("### 🎲 Sign-flip Permutation Test")
    st.markdown(
        """
            對每筆 PnL 獨立翻轉 ±1（Bernoulli 0.5）共 N 次，計算 null 分布下 |Sharpe|，
            **p-value = `(n_extreme + 1) / (n_iter + 1)`**（Phipson & Smyth 2010 unbiased）。

            **為何不用 random shuffle**：Sharpe = mean / std × √N 在 shuffle 下完全不變
            （mean 與 std 都是排列不變量），原始 shuffle p-value 沒意義。Sign-flip 才能讓 mean 真正變動。

            **Function**：`permutation_test(daily_pnl, n_iter, seed) → (observed, null_dist, p_value)`
            """
    )
    st.caption(
        "**Reference**：Politis & Romano (2010). *K-sample subsampling*. JMVA 101(2). + "
        "Phipson & Smyth (2010). *Permutation P-values should never be zero*. SAGMB 9(1)."
    )

c3, c4 = st.columns(2)

with c3, st.container(border=True):
    st.markdown("### 📐 Deflated Sharpe Ratio (DSR)")
    st.markdown(
        """
            校正 **selection bias**（多策略 ablation 時 raw Sharpe 偏高估）+ **non-normality**（skew / kurt）。

            $$
            \\text{DSR} = \\Phi\\left(\\frac{(SR_{obs} - SR_0)\\sqrt{T-1}}{\\sqrt{1 - \\text{skew}\\cdot SR + \\frac{\\text{kurt}-1}{4} SR^2}}\\right)
            $$

            其中 $SR_0$ 為「N 個獨立 random strategy 期望 max Sharpe」（Bonferroni-like）。

            **判讀**：DSR > 0.95 才算「真實顯著」（vs raw Sharpe 已知會 over-state）。

            **Function**：`deflated_sharpe(observed_sharpe, n_trials, T, skew, kurt) → DSR`
            """
    )
    st.caption(
        "**Reference**：López de Prado (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, "
        "Backtest Overfitting, and Non-Normality*. JPM 40(5)."
    )

with c4, st.container(border=True):
    st.markdown("### 📈 Calmar Ratio")
    st.markdown(
        """
            **年化報酬 / 絕對 max drawdown**，回測界傳統 risk-adjusted 度量。

            $$
            \\text{Calmar} = \\frac{\\text{Annualised Return}}{|\\text{Max Drawdown}|}
            $$

            **判讀**：Calmar > 0.5 是 Phase 1 Pro 出口條件之一；負 Calmar = 策略整體虧損。

            **本 repo 實作的 nuance**：`calmar_ratio()` 自帶 `np.cumsum + maximum.accumulate`
            計算 max DD（不依賴 `metrics.max_drawdown`），所以**不被 Section 3.3 的 caller bug 污染** —
            獨立路徑作 cross-check。

            **Function**：`calmar_ratio(daily_pnl, initial_capital, periods_per_year) → ratio`
            """
    )
    st.caption(
        "**Reference**：Young (1991). *Calmar Ratio: A Smoother Tool*. Futures Magazine. "
        "+ Eling & Schuhmacher (2007). *Does the choice of performance measure influence the evaluation of hedge funds?*"
    )

st.divider()

# ---------------------------------------------------------------------------
# Cross-page navigation footer（all pages 共用 pattern）
# ---------------------------------------------------------------------------

st.markdown("### 接下來")

nav_cols = st.columns(3)
with nav_cols[0]:
    st.page_link("專案背景.py", label="← 專案背景", icon="🏠")
with nav_cols[1]:
    st.page_link("pages/1_定價核心.py", label="Page 1 — 定價核心", icon="🧮")
with nav_cols[2]:
    st.page_link("pages/2_Walk-forward結果.py", label="Page 2 — Walk-forward 結果", icon="📈")

st.caption(
    "本 dashboard 為 Options_Trading repo 的 portfolio showcase。詳細 source code 與 commit 歷史見"
    " [GitHub](https://github.com/JerryHuang0829/Options_Trading)。"
)
