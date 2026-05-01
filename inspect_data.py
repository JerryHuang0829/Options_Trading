"""Quick data inspection script"""

import glob

import pandas as pd

# === 1. Read TAIEX spot CSV ===
print("=" * 60)
print("TAIEX Spot Data")
print("=" * 60)
taiex = pd.read_csv("data/taiex_daily.csv", parse_dates=["date"])
taiex = taiex.set_index("date")
print(f"Date range: {taiex.index.min().date()} ~ {taiex.index.max().date()}")
print(f"Total rows: {len(taiex)}")
print(f"\n2024-01-15 close: {taiex.loc['2024-01-15', 'close']:.2f}")
print("\nLast 5 days:")
print(taiex.tail().to_string())

# === 2. Read TXO chain for one day ===
print("\n" + "=" * 60)
print("TXO Chain (2024-01-15)")
print("=" * 60)
chain = pd.read_parquet("data/taifex_cache/strategy_view/2024/2024-01-15.parquet")
print(f"Contracts: {len(chain)}")
print(f"Columns: {chain.columns.tolist()}")

spot = taiex.loc["2024-01-15", "close"]
print(f"\nTAIEX = {spot:.2f}")

filtered = chain[
    (chain["strike"] >= 16000) & (chain["strike"] <= 18000) & (chain["volume"] > 0)
].sort_values(["expiry", "option_type", "strike"])
print(f"\nFiltered (liquid contracts): {len(filtered)} rows")
print(filtered.head(20).to_string())

# === 3. Batch read 2024 January ===
print("\n" + "=" * 60)
print("Batch Read - 2024 January")
print("=" * 60)
files = sorted(glob.glob("data/taifex_cache/strategy_view/2024/2024-01-*.parquet"))
print(f"Found {len(files)} trading days")

dfs = [pd.read_parquet(f) for f in files]
month_chain = pd.concat(dfs, ignore_index=True)
print(f"Total rows merged: {len(month_chain)}")
print(f"Date span: {month_chain['date'].min().date()} ~ {month_chain['date'].max().date()}")
