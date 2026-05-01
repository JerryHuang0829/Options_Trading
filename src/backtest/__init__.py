"""Backtest engine for options strategies.

Phase 1 Week 2-3 event-driven loop:
  - Iterate over trading days
  - Each day: feed option chain -> strategy -> portfolio update -> metrics
  - Fill semantics delegated to ``execution.FillModel`` (settle / mid /
    worst-side / slippage), configurable per backtest — NOT hard-coded.

Modules:
  engine     -- main backtest loop, day-level orchestration
  portfolio  -- position tracking, mark-to-market, P&L per day; OptionLeg / Position dataclasses
  metrics    -- Sharpe / max drawdown / win rate / trade statistics
  execution  -- FillModel abstraction + Fill dataclass
"""
