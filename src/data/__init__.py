"""Data layer: loaders, caches, and broker abstractions.

Phase 1 Week 1-3 focus:
  synthetic       -- synthetic option chain factory (Week 1-2 smoke testing)
  taifex_loader   -- TAIFEX CSV download + parse (Week 3+ historical chain)
  cache           -- parquet cache for downloaded chain data
  brokers/        -- broker abstraction; Shioaji stub kept for Phase 2

TAIFEX quirks to remember (Pre-1 實證, 2026-04-27):
  - **CP950 encoding** (NOT Big5; CP950 是 Microsoft 超集，TAIFEX 用此)
  - Chinese column names (見 src/data/schema.py RAW_COLUMN_RENAME)
  - TXO + 個股選擇權 (CAO/CBO/...) + 電子 + 金融 all mixed in opt CSV
    (TXF 期貨 NOT 在 opt CSV — 期貨在 fut endpoint)
  - Weekly + monthly + multi-month expiries in one file
  - Pre-2025-12-08: 20 cols / Post-2025-12-08: 21 cols (含 `契約到期日` YYYYMMDD)
  - Header 20/data 21 trailing comma → `index_col=False` 避 silent shift (R10.7 F2)
"""
