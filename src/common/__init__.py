"""Cross-cutting domain types shared by strategies / backtest / risk layers.

This module is the **single source of truth** for the typed domain model used
across the trading pipeline. Codex R4 + GPT-5.5 共識：dict-based interfaces
between strategy / backtest / risk layers leak field names and produce silent
bugs (qty sign confusion, fill price vs settle price mix-up). Everything that
crosses module boundaries should live here as a dataclass.

Re-exports OptionLeg / Position / Fill / FillModel / ChainQuote from their
home modules so callers have one canonical import path.
"""
