"""One-shot prep: fetch TAIEX (^TWII) 5yr daily OHLC → data/taiex_daily.csv.

D-soft Pre-2 (2026-04-28). Run **once** to materialize the spot reference
series Day 4 enrich.add_underlying() consumes. Production pipeline does NOT
depend on yfinance — only this prep script does.

Usage:
    pip install "yfinance>=0.2.40,<0.3"   # one-shot, NOT in requirements.txt
    python scripts/fetch_taiex.py

CAVEATS (Codex R10.5 P2 acknowledged — do NOT silently override):

  1. **^TWII is PRICE INDEX, not total return index** — dividend re-investment
     not reflected. q PIT estimation has systematic bias.

  2. **TAIEX cash close time != TXO settlement time** — intraday gap unmodelled.
     Cash market closes 13:30; TXO regular session settles 13:45 (±). Spot we
     pin per-date is the cash close, not the TXO settlement spot.

  3. **Used for q PIT AUDIT only; NEVER fed into tradable signal generation.**
     plan v6 §4 Day 4: enrich.add_iv_per_strike default q_source='fallback'
     (DIVIDEND_YIELD_DEFAULT=0.035), q_pit only feeds audit log.

  4. **For production-grade backtest, replace with audited vendor data series**
     (Bloomberg / Refinitiv / TWSE official spot series) — yfinance has known
     historical-correction issues and unannounced schema changes.

Output:
    data/taiex_daily.csv (~50KB, git-tracked)
    Schema: date (ISO YYYY-MM-DD), open, high, low, close, volume

Date range: 2021-01-01 to today (covers Week 6+ 5yr backtest window 2021-04→2026-04).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yfinance as yf

OUTPUT_PATH = Path("data/taiex_daily.csv")
SYMBOL = "^TWII"
# 2026-04-28 user pivot 5yr → 7yr: backfill 7 yr (2018-2025) + sensitivity vs 5 yr main.
# Earlier start_date covers COVID 2020 stress test + 2018-2019 normal regime.
START_DATE = "2018-01-01"


def main() -> int:
    print(f"Fetching {SYMBOL} from {START_DATE} to today via yfinance...")
    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(start=START_DATE, auto_adjust=False)

    if df.empty:
        print(f"ERROR: yfinance returned empty DataFrame for {SYMBOL}", file=sys.stderr)
        return 1

    # Normalise: keep date / open / high / low / close / volume; lowercase cols
    df = df.reset_index()
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df = df.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    keep = ["date", "open", "high", "low", "close", "volume"]
    df = df[keep]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)

    n_rows = len(df)
    first = df["date"].iloc[0]
    last = df["date"].iloc[-1]
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Wrote {OUTPUT_PATH} ({n_rows} rows, {first} → {last}, {size_kb:.1f} KB)")
    print("Sample (first 3 rows):")
    print(df.head(3).to_string(index=False))
    print()
    print("Caveats reminder:")
    print("  - ^TWII is PRICE INDEX, not total return")
    print("  - q PIT AUDIT only; tradable signals use fallback q=0.035")
    return 0


if __name__ == "__main__":
    sys.exit(main())
