"""Risk management layer.

Phase 1 Week 2 Day 4 introduces hard risk gates that strategies / engine
must pass before opening new positions or holding losing ones. Codex R4 +
GPT-5.5 共識：IC 是 short gamma + short vega 策略，沒風控骨架的回測會給
'paper alpha' 假信心。

Core APIs (see `limits` submodule):
  - check_risk(state, signal, config) -> (allowed, reason)
  - trigger_stop_loss(position, current_unrealised_pnl, config) -> bool
"""
