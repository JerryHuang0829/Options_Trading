"""Hard risk limit checks for strategy / engine integration.

Codex R4 + GPT-5.5 共識：IC 是 short gamma + short vega 策略，必須在開倉前
透過 ``check_risk`` 與已有部位 / 累計 capital / 日損 cap 做 hard gating。
Phase 1 Week 2 Day 4 引入：

  - ``check_risk(state, signal, config) -> (allowed, reason)``
  - ``trigger_stop_loss(position, current_unrealised_pnl, config) -> bool``

Risk model assumes:
  - max_defined_risk = ``signal.metadata['max_defined_risk_twd']`` (set by IC.open_position)
  - capital_at_risk per position = ``Position.tags['max_defined_risk_twd']``
    (engine populates at open from the matching ``open`` signal)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants import TXO_MULTIPLIER

if TYPE_CHECKING:
    from src.common.types import (
        PortfolioState,
        Position,
        RiskConfig,
        StrategySignal,
    )


def _capital_at_risk(state: PortfolioState) -> float:
    """Sum of `max_defined_risk_twd` across all open positions."""
    total = 0.0
    for pos in state.positions:
        total += float(pos.tags.get("max_defined_risk_twd", 0.0))
    return total


def check_risk(
    state: PortfolioState,
    signal: StrategySignal,
    config: RiskConfig,
) -> tuple[bool, str | None]:
    """Apply hard limits to a proposed open signal.

    Returns ``(allowed, reason)``:
      - ``(True, None)`` if all checks pass
      - ``(False, reason_str)`` on first failing limit

    Only ``signal.action == "open"`` is gated; close / adjust / hold pass-through.

    Checks (short-circuit on first failure):
      1. Single-trade max loss: signal's ``max_defined_risk_twd`` > limit
      2. Concurrent positions: state already at ``max_concurrent_positions``
      3. Cumulative capital at risk: existing + new > limit
      4. Portfolio daily loss cap: ``state.realised_pnl + state.unrealised_pnl``
         is more negative than ``-portfolio_loss_cap_pct × state.initial_capital``.
         (R5 P2 fix: ``initial_capital`` is an explicit ``PortfolioState`` field
         supplied by the engine; we no longer derive it from cash + realised
         which double-counts open-position cash flows.)
    """
    if signal.action != "open":
        return (True, None)

    proposed_risk = float(signal.metadata.get("max_defined_risk_twd", 0.0))

    # 1. Single-trade max loss limit.
    if proposed_risk > config.max_loss_per_trade_twd:
        return (
            False,
            f"max_loss_per_trade exceeded: proposed={proposed_risk:.0f} "
            f"> limit={config.max_loss_per_trade_twd:.0f}",
        )

    # 2. Concurrent positions limit.
    if len(state.positions) >= config.max_concurrent_positions:
        return (
            False,
            f"max_concurrent_positions reached: open={len(state.positions)} "
            f">= limit={config.max_concurrent_positions}",
        )

    # 3. Cumulative capital at risk.
    existing_risk = _capital_at_risk(state)
    if existing_risk + proposed_risk > config.max_capital_at_risk_twd:
        return (
            False,
            f"max_capital_at_risk exceeded: existing={existing_risk:.0f} + "
            f"proposed={proposed_risk:.0f} > limit={config.max_capital_at_risk_twd:.0f}",
        )

    # 4. Portfolio daily loss cap.
    # Codex R5 P2 fix: use state.initial_capital (engine-supplied) not
    # cash + realised. The latter double-counts open-position cash flows
    # (cash at any moment already reflects collected premium).
    if state.initial_capital <= 0:
        raise ValueError(
            f"PortfolioState.initial_capital must be > 0 for loss-cap check, "
            f"got {state.initial_capital}"
        )
    total_pnl = state.realised_pnl + state.unrealised_pnl
    loss_cap_threshold = -config.portfolio_loss_cap_pct * state.initial_capital
    if total_pnl < loss_cap_threshold:
        return (
            False,
            f"portfolio_loss_cap reached: total_pnl={total_pnl:.0f} "
            f"< threshold={loss_cap_threshold:.0f} "
            f"({config.portfolio_loss_cap_pct * 100:.1f}% of initial_capital="
            f"{state.initial_capital:.0f})",
        )

    return (True, None)


def trigger_stop_loss(
    position: Position,
    current_unrealised_pnl: float,
    config: RiskConfig,
) -> bool:
    """Decide whether to force-close a position based on stop-loss multiple.

    Args:
        position: Open position; must have ``entry_credit_mid`` (in **points**)
            tagged at open.
        current_unrealised_pnl: Position's current unrealised PnL **in TWD**
            (matches ``Portfolio.mark_to_market`` output).
        config: Risk configuration; ``stop_loss_multiple`` is the multiplier of
            entry credit beyond which the position is forced closed.

    Returns:
        True if ``current_unrealised_pnl <= -entry_credit_mid × multiplier
        × TXO_MULTIPLIER`` (both sides in TWD); False otherwise.

    Codex R8 P1: previous version compared a TWD-side PnL against a
    points-side threshold without ``TXO_MULTIPLIER``, so a -160 TWD draw on a
    NT$8,000 expected threshold would falsely trigger. Both sides now in TWD.

    Returns False (no trigger) if position lacks ``entry_credit_mid``.
    """
    entry_credit = position.tags.get("entry_credit_mid")
    if entry_credit is None:
        return False

    threshold_twd = -float(entry_credit) * config.stop_loss_multiple * TXO_MULTIPLIER
    return current_unrealised_pnl <= threshold_twd
