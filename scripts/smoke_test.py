"""End-to-end smoke test for Phase 1 Week 1 implementation.

Pipeline exercised (Day 1-4):
    1. Day 4 ``SyntheticChainConfig`` + ``generate_chain`` → 24-col enriched chain
    2. Day 1 ``bsm_price`` (used internally by synthetic) — implicit
    3. Day 2 ``delta`` (used internally by synthetic for the ``delta`` column) — implicit
    4. Day 3 ``filter_by_dte`` → narrow to 28-32 DTE band (typical IC entry)
    5. Day 3 ``select_by_delta`` → pick ~16-delta short call + short put,
        and ~8-delta long wings (the canonical 4-leg Iron Condor)
    6. Compute aggregate Greeks for the IC position
    7. Print human-readable sample report

Acceptance:
    - Runs < 30 s end-to-end on a typical machine.
    - No exceptions; report shows reasonable IC structure
      (short legs nearer ATM than long legs; net delta ≈ 0).

Usage::

    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Bootstrap: when run as ``python scripts/smoke_test.py`` the cwd-anchored
# imports of ``config`` / ``src`` fail. Prepend repo root so they resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from config.constants import TXO_MULTIPLIER  # noqa: E402
from src.data.synthetic import SyntheticChainConfig, generate_chain  # noqa: E402
from src.options.chain import filter_by_dte, select_by_delta  # noqa: E402


def _format_leg(leg: pd.Series, side: str) -> str:
    return (
        f"  {side:5s} {leg['option_type']:4s} K={int(leg['strike']):>6d}  "
        f"settle={leg['settle']:8.2f}  delta={leg['delta']:+.4f}  "
        f"bid={leg['bid']:8.2f}  ask={leg['ask']:8.2f}"
    )


def main() -> int:
    t0 = time.perf_counter()

    # 1. Synthetic chain (~3 months, 4 expiries).
    config = SyntheticChainConfig(
        start_date="2026-01-01",
        end_date="2026-03-31",
        spot_start=16800.0,
        sigma=0.20,
        seed=42,
    )
    chain = generate_chain(config)
    t1 = time.perf_counter()

    # 2. Pick a mid-month trading day so the next-month 3rd-Wed expiry sits in
    # the typical 21-45 DTE band (synthetic uses fixed monthly expiries).
    target_date = chain["date"].unique()[10]
    today_chain = chain.loc[chain["date"] == target_date].copy()
    spot = today_chain["underlying"].iloc[0]

    # 3. Filter to 21-45 DTE entry band (covers next-monthly TXO expiry).
    candidates = filter_by_dte(today_chain, min_dte=21, max_dte=45)

    # 4. Build the 4-leg Iron Condor: short 0.16 / long 0.08 wings each side.
    short_call = select_by_delta(candidates, target_delta=0.16, option_type="call")
    long_call = select_by_delta(candidates, target_delta=0.08, option_type="call")
    short_put = select_by_delta(candidates, target_delta=-0.16, option_type="put")
    long_put = select_by_delta(candidates, target_delta=-0.08, option_type="put")

    # 5. Aggregate Greeks (signed: short = -1, long = +1, qty = 1 each).
    legs = [
        ("SHORT", short_call, -1),
        ("LONG ", long_call, +1),
        ("SHORT", short_put, -1),
        ("LONG ", long_put, +1),
    ]
    net_delta = sum(qty * leg["delta"] for _, leg, qty in legs)
    net_credit = sum(
        -qty * leg["settle"] for _, leg, qty in legs
    )  # short legs collect; long legs pay

    # 6. Report.
    t2 = time.perf_counter()
    print("=" * 70)
    print("Phase 1 Week 1 — End-to-End Smoke Test")
    print("=" * 70)
    print(
        f"\nConfig:  {config.start_date} → {config.end_date}, "
        f"spot_start={config.spot_start}, sigma={config.sigma}"
    )
    print(
        f"Chain:   {len(chain):,} rows ({len(chain.columns)} columns); generated in {t1 - t0:.2f}s"
    )
    print(f"\nEntry date: {pd.Timestamp(target_date).date()}    underlying spot: {spot:,.2f}")
    print(
        f"DTE band  : {int(candidates['dte'].iloc[0])} days  ({len(candidates):,} candidate rows)"
    )
    print("\nIron Condor (4 legs):")
    for side, leg, _ in legs:
        print(_format_leg(leg, side))
    print("\nNet position Greeks:")
    print(f"  delta (1 contract each): {net_delta:+.4f}  (should be near 0 for delta-neutral IC)")
    print(
        f"  credit collected       : {net_credit:>8.2f} pts  "
        f"(× TXO multiplier {TXO_MULTIPLIER} = NT${net_credit * TXO_MULTIPLIER:,.0f})"
    )
    print("\nWidth (each side):")
    call_width = int(long_call["strike"] - short_call["strike"])
    put_width = int(short_put["strike"] - long_put["strike"])
    print(f"  call wing: {call_width}    put wing: {put_width}")
    print(f"\nTotal smoke runtime: {t2 - t0:.2f}s")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
