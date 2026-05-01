"""Stress test: IC under IV / spot shock scenarios (Week 2 Day 6).

Goal: answer GPT-5.5 north-star questions on a synthetic IC entry:
  1. PnL in IV crush -30% / IV expand +50% / spot gap ±5-7%?
  2. Worst-case loss vs max defined risk?
  3. Does any scenario trigger stop-loss?

Pipeline:
  1. Generate synthetic chain (3-month).
  2. Pick an IC entry date with 4 strikes at 0.16 / 0.08 deltas.
  3. Open the IC at WorstSide fills (mid baseline for entry credit).
  4. Apply 4 shock scenarios on the entry-day chain (sigma & spot shocks):
     - Re-price each option via BSM-Merton with shocked sigma + spot.
     - Mark-to-market the IC under each shocked chain.
  5. Print a table: scenario × (PnL in TWD, Δ Greeks vs baseline, hit stop?).

This script is **synthetic-only**: it stresses the framework, not the real IC
edge. Vol surface modelling (SVI / SABR) is Week 5-6 once we have TAIFEX data.

Usage::

    python scripts/stress_test.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Bootstrap: when run as ``python scripts/stress_test.py``, prepend repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from config.constants import (  # noqa: E402
    CALENDAR_DAYS_PER_YEAR,
    DIVIDEND_YIELD_DEFAULT,
    RISK_FREE_RATE_DEFAULT,
    TXO_MULTIPLIER,
)
from src.backtest.execution import WorstSideFillModel  # noqa: E402
from src.backtest.portfolio import OptionLeg, Portfolio  # noqa: E402
from src.common.types import RiskConfig  # noqa: E402
from src.data.synthetic import SyntheticChainConfig, generate_chain  # noqa: E402
from src.options.chain import filter_by_dte, select_by_delta  # noqa: E402
from src.options.greeks import delta as bsm_delta  # noqa: E402
from src.options.pricing import bsm_price  # noqa: E402


@dataclass(frozen=True)
class StressScenario:
    """One stress scenario: relative shock to sigma and spot."""

    name: str
    sigma_shock: float  # multiplicative; +0.5 = 50% IV expand; -0.3 = 30% crush
    spot_shock: float  # multiplicative; +0.05 = 5% gap up


# 4 plan-sanctioned scenarios.
SCENARIOS: list[StressScenario] = [
    StressScenario(name="iv_crush", sigma_shock=-0.30, spot_shock=0.0),
    StressScenario(name="iv_expand", sigma_shock=+0.50, spot_shock=0.0),
    StressScenario(name="spot_gap_up", sigma_shock=+0.20, spot_shock=+0.05),
    StressScenario(name="spot_gap_dn", sigma_shock=+0.30, spot_shock=-0.07),
]


def shock_chain(chain_today: pd.DataFrame, scenario: StressScenario) -> pd.DataFrame:
    """Re-price every chain row under shocked (sigma, spot).

    Single-date input is required (R5 P2). Multi-date chains would silently
    misprice because we use the first row's date as ``today`` for all T values.

    Steps per row:
      1. sigma' = sigma * (1 + sigma_shock)
      2. spot'  = underlying * (1 + spot_shock)
      3. settle' = bsm_price(spot', K, T, r, q, sigma', option_type)
         (We approximate bid'/ask' via the same proportional spread as original.)
      4. delta' = greeks.delta(spot', K, T, r, q, sigma', option_type)
      5. underlying' = spot' (so downstream consumers see shocked spot)
    """
    if chain_today.empty:
        raise ValueError("shock_chain: chain_today is empty")
    unique_dates = chain_today["date"].unique()
    if len(unique_dates) > 1:
        raise ValueError(
            f"shock_chain expects a single-date snapshot; got {len(unique_dates)} unique dates"
        )
    out = chain_today.copy()
    today = pd.Timestamp(out["date"].iloc[0])
    new_settles: list[float] = []
    new_bids: list[float] = []
    new_asks: list[float] = []
    new_deltas: list[float] = []
    new_underlyings: list[float] = []

    spot_factor = 1.0 + scenario.spot_shock
    sigma_factor = 1.0 + scenario.sigma_shock

    for _, row in out.iterrows():
        S_old = float(row["underlying"])
        S_new = S_old * spot_factor
        sigma_new = max(float(row["iv"]) * sigma_factor, 1e-6)
        K = float(row["strike"])
        T = max((row["expiry"] - today).days / CALENDAR_DAYS_PER_YEAR, 1e-6)
        opt_type = row["option_type"]

        new_settle = bsm_price(
            S_new, K, T, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT, sigma_new, opt_type
        )
        # Preserve original proportional bid/ask spread.
        old_settle = float(row["settle"])
        if old_settle > 0:
            bid_factor = float(row["bid"]) / old_settle
            ask_factor = float(row["ask"]) / old_settle
        else:
            bid_factor = 0.97
            ask_factor = 1.03
        new_bid = max(new_settle * bid_factor, 0.01)
        new_ask = max(new_settle * ask_factor, new_bid + 0.01)

        new_delta = bsm_delta(
            S_new, K, T, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT, sigma_new, opt_type
        )

        new_settles.append(new_settle)
        new_bids.append(new_bid)
        new_asks.append(new_ask)
        new_deltas.append(new_delta)
        new_underlyings.append(S_new)

    out["settle"] = new_settles
    out["bid"] = new_bids
    out["ask"] = new_asks
    out["delta"] = new_deltas
    out["underlying"] = new_underlyings
    out["iv"] = out["iv"] * sigma_factor
    return out


def _open_ic_at_date(
    chain_today: pd.DataFrame,
    fill_model: WorstSideFillModel,
) -> tuple[Portfolio, dict]:
    """Open a 4-leg IC on the given chain_today; return (portfolio, metadata)."""
    # 28-32 ± 7 = 21-39 DTE band covers next-month TXO expiry from a typical
    # mid-month entry (synthetic uses fixed 3rd-Wed monthly expiries).
    candidates = filter_by_dte(chain_today, min_dte=21, max_dte=45)

    short_call = select_by_delta(candidates, target_delta=+0.16, option_type="call")
    long_call = select_by_delta(candidates, target_delta=+0.08, option_type="call")
    short_put = select_by_delta(candidates, target_delta=-0.16, option_type="put")
    long_put = select_by_delta(candidates, target_delta=-0.08, option_type="put")

    today = pd.Timestamp(chain_today["date"].iloc[0])
    rows_sides: list[tuple[pd.Series, Literal["buy", "sell"], int]] = [
        (short_call, "sell", -1),
        (long_call, "buy", +1),
        (short_put, "sell", -1),
        (long_put, "buy", +1),
    ]
    legs: list[OptionLeg] = []
    for row, side, signed_qty in rows_sides:
        fill = fill_model.fill(row, side, qty=1)
        legs.append(
            OptionLeg(
                contract=fill.contract,
                strike=fill.strike,
                expiry=row["expiry"],
                option_type=fill.option_type,
                qty=signed_qty,
                entry_date=today,
                entry_price=fill.fill_price,
            )
        )

    # Mid-baseline entry credit for stop-loss reference.
    def _mid(row: pd.Series) -> float:
        return float((row["bid"] + row["ask"]) / 2.0)

    entry_credit_mid = _mid(short_call) + _mid(short_put) - _mid(long_call) - _mid(long_put)
    call_wing = int(long_call["strike"] - short_call["strike"])
    put_wing = int(short_put["strike"] - long_put["strike"])
    max_wing = max(call_wing, put_wing)
    # worst-case credit at open (worst-side fills already taken into legs)
    worst_credit = sum(-leg.qty * leg.entry_price for leg in legs)
    max_defined_risk_twd = float(max_wing * TXO_MULTIPLIER - worst_credit * TXO_MULTIPLIER)

    portfolio = Portfolio(initial_capital=1_000_000.0)
    portfolio.open(
        legs,
        strategy_name="IC_stress",
        entry_credit_mid=entry_credit_mid,
        max_defined_risk_twd=max_defined_risk_twd,
    )
    metadata = {
        "entry_credit_mid": entry_credit_mid,
        "max_defined_risk_twd": max_defined_risk_twd,
        "call_wing_pts": call_wing,
        "put_wing_pts": put_wing,
        "short_call_strike": int(short_call["strike"]),
        "short_put_strike": int(short_put["strike"]),
        "spot_at_entry": float(chain_today["underlying"].iloc[0]),
    }
    return portfolio, metadata


def run_stress_test() -> dict:
    """Run all 4 scenarios + return a structured result dict."""
    t0 = time.perf_counter()

    config = SyntheticChainConfig(
        start_date="2026-01-01",
        end_date="2026-03-31",
        spot_start=16800.0,
        sigma=0.20,
        seed=42,
    )
    chain = generate_chain(config)

    # Pick a mid-month entry so the next-month 3rd-Wed expiry sits comfortably
    # in the 21-45 DTE band (smoke_test.py uses a similar choice).
    entry_date = chain["date"].unique()[10]
    chain_today = chain[chain["date"] == entry_date].copy()

    fill_model = WorstSideFillModel()
    portfolio, meta = _open_ic_at_date(chain_today, fill_model)

    # Risk config to test stop-loss trigger (entry credit × 2.0 stop).
    risk_config = RiskConfig(
        max_loss_per_trade_twd=200_000.0,
        max_capital_at_risk_twd=400_000.0,
        max_concurrent_positions=3,
        stop_loss_multiple=2.0,
        portfolio_loss_cap_pct=0.05,
    )

    # Baseline: mark-to-market on the unshocked entry-day chain.
    baseline_unrealised = portfolio.mark_to_market(chain_today)
    baseline_greeks = portfolio.aggregate_greeks(chain_today)

    rows: list[dict] = []
    for scenario in SCENARIOS:
        shocked = shock_chain(chain_today, scenario)
        unrealised = portfolio.mark_to_market(shocked)
        greeks = portfolio.aggregate_greeks(shocked)

        # Stop-loss trigger: PnL_pts ≤ -entry_credit × stop_loss_multiple
        # PnL in points = unrealised / TXO_MULTIPLIER
        pnl_pts = unrealised / TXO_MULTIPLIER
        threshold_pts = -float(meta["entry_credit_mid"]) * risk_config.stop_loss_multiple
        hit_stop = pnl_pts <= threshold_pts

        rows.append(
            {
                "scenario": scenario.name,
                "sigma_shock": scenario.sigma_shock,
                "spot_shock": scenario.spot_shock,
                "pnl_twd": unrealised,
                "pnl_pct_of_max_risk": unrealised / meta["max_defined_risk_twd"],
                "delta_change": greeks["delta"] - baseline_greeks["delta"],
                "vega_change": greeks["vega"] - baseline_greeks["vega"],
                "theta_change": greeks["theta"] - baseline_greeks["theta"],
                "hit_stop_loss": hit_stop,
            }
        )

    elapsed = time.perf_counter() - t0
    result_df = pd.DataFrame(rows)

    return {
        "entry_meta": meta,
        "baseline_unrealised": baseline_unrealised,
        "baseline_greeks": baseline_greeks,
        "stress_table": result_df,
        "risk_config": risk_config,
        "elapsed_sec": elapsed,
    }


def _print_report(result: dict) -> None:
    meta = result["entry_meta"]
    print("=" * 78)
    print("Phase 1 Week 2 Day 6 — IC Stress Test (4 scenarios)")
    print("=" * 78)
    print(f"\nEntry: spot={meta['spot_at_entry']:,.2f}")
    print(
        f"  short call K={meta['short_call_strike']}, "
        f"short put K={meta['short_put_strike']}, "
        f"call wing={meta['call_wing_pts']}, put wing={meta['put_wing_pts']}"
    )
    print(f"  entry_credit_mid       = {meta['entry_credit_mid']:.2f} pts")
    print(f"  max_defined_risk_twd   = NT${meta['max_defined_risk_twd']:,.0f}")
    print(f"  baseline unrealised    = NT${result['baseline_unrealised']:,.2f}")

    table = result["stress_table"].copy()
    print("\n--- Stress table ---")
    print(
        f"{'scenario':<14} {'sigma_shk':>10} {'spot_shk':>9} "
        f"{'pnl_twd':>12} {'pnl/maxR':>10} {'Δdelta':>10} {'Δvega':>12} "
        f"{'Δtheta':>12} {'stop?':>6}"
    )
    for _, row in table.iterrows():
        print(
            f"{row['scenario']:<14} {row['sigma_shock']:>+10.2f} {row['spot_shock']:>+9.2f} "
            f"{row['pnl_twd']:>12,.0f} {row['pnl_pct_of_max_risk']:>+10.2%} "
            f"{row['delta_change']:>+10.2f} {row['vega_change']:>+12.2f} "
            f"{row['theta_change']:>+12.2f} {'YES' if row['hit_stop_loss'] else 'no':>6}"
        )
    print(f"\nRuntime: {result['elapsed_sec']:.2f}s")
    print("=" * 78)


def main() -> int:
    result = run_stress_test()
    _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
