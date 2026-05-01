"""Domain types: OptionQuote / Order / StrategySignal / PortfolioState / RiskConfig.

5 new dataclasses introduced in Week 2 Day 1 to replace dict-based interfaces
between strategy / backtest / risk layers. Codex R4 + GPT-5.5 共識：dict 邊界
易產生 field name typo / qty sign 混亂 / fill price vs settle price 混用等
silent bugs，此模組為 typed domain model 的單一真實來源。

Existing dataclasses (`OptionLeg`, `Position`, `Fill`, `FillModel`,
`ChainQuote`) are re-exported here so callers have one canonical import path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pandas as pd

# Re-exports (single canonical import path: ``from src.common.types import ...``)
from src.backtest.execution import Fill, FillModel  # noqa: F401
from src.backtest.portfolio import OptionLeg, Position  # noqa: F401
from src.options.chain import ChainQuote  # noqa: F401

OptionType = Literal["call", "put"]
Side = Literal["buy", "sell"]
StrategyAction = Literal["open", "close", "adjust", "hold"]


@dataclass(frozen=True)
class OptionQuote:
    """Single option chain row as a typed view (replaces dict-of-Series usage).

    Covers the enriched-chain schema from `src.data.synthetic.generate_chain`:
    raw market data + iv / delta / underlying pre-computed by the caller.
    """

    date: pd.Timestamp
    expiry: pd.Timestamp
    strike: int
    option_type: OptionType
    settle: float
    bid: float
    ask: float
    iv: float
    delta: float
    underlying: float

    def __post_init__(self) -> None:
        if self.option_type not in ("call", "put"):
            raise ValueError(
                f"OptionQuote.option_type must be 'call'|'put', got {self.option_type!r}"
            )


@dataclass(frozen=True)
class Order:
    """Pre-fill order intent emitted by a strategy.

    Becomes a ``Fill`` after a ``FillModel`` resolves the price.
    """

    contract: str
    strike: int
    expiry: pd.Timestamp
    option_type: OptionType
    side: Side
    qty: int  # positive count of contracts to buy/sell

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"Order.qty must be > 0 (sign carried by `side`), got {self.qty}")
        if self.option_type not in ("call", "put"):
            raise ValueError(f"Order.option_type must be 'call'|'put', got {self.option_type!r}")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Order.side must be 'buy'|'sell', got {self.side!r}")


@dataclass(frozen=True)
class StrategySignal:
    """Strategy → engine signal (open / close / adjust / hold).

    ``orders`` is empty for ``"hold"``. ``metadata`` carries per-signal context
    (e.g. ``settle_credit`` / ``mid_credit`` / ``worst_side_credit`` for IC
    open signals; ``rejected_reason`` when risk check vetoes an open).
    """

    action: StrategyAction
    orders: list[Order]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in ("open", "close", "adjust", "hold"):
            raise ValueError(f"StrategySignal.action invalid, got {self.action!r}")
        if self.action == "hold" and self.orders:
            raise ValueError("'hold' signal must have empty orders list")
        if self.action != "hold" and not self.orders:
            raise ValueError(f"'{self.action}' signal requires at least 1 order")


@dataclass
class PortfolioState:
    """Read-mostly snapshot passed to strategies (replaces dict).

    The engine builds this from `Portfolio` before each strategy call.
    `aggregate_greeks` keys are exactly ``{"delta", "gamma", "theta", "vega"}``.

    Codex R5 P2: ``initial_capital`` is provided explicitly here (rather than
    derived from ``cash + realised_pnl``, which double-counts open-trade cash
    flows). ``check_risk`` uses it to compute the portfolio-loss-cap threshold.
    """

    cash: float
    positions: list[Position]
    realised_pnl: float
    unrealised_pnl: float
    initial_capital: float = 0.0  # set by engine; used by check_risk
    aggregate_greeks: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected_keys = {"delta", "gamma", "theta", "vega"}
        if self.aggregate_greeks and set(self.aggregate_greeks.keys()) != expected_keys:
            raise ValueError(
                f"PortfolioState.aggregate_greeks keys must be {expected_keys}, "
                f"got {set(self.aggregate_greeks.keys())}"
            )


@dataclass(frozen=True)
class RiskConfig:
    """Hard risk limits (Day 4 wired into IC strategy / engine).

    Phase 1 Week 2 establishes the contract; concrete limits picked per backtest.
    """

    max_loss_per_trade_twd: float
    max_capital_at_risk_twd: float
    max_concurrent_positions: int
    stop_loss_multiple: float
    portfolio_loss_cap_pct: float

    def __post_init__(self) -> None:
        if self.max_loss_per_trade_twd <= 0:
            raise ValueError(
                f"max_loss_per_trade_twd must be > 0, got {self.max_loss_per_trade_twd}"
            )
        if self.max_capital_at_risk_twd <= 0:
            raise ValueError(
                f"max_capital_at_risk_twd must be > 0, got {self.max_capital_at_risk_twd}"
            )
        if self.max_concurrent_positions <= 0:
            raise ValueError(
                f"max_concurrent_positions must be > 0, got {self.max_concurrent_positions}"
            )
        if self.stop_loss_multiple <= 0:
            raise ValueError(f"stop_loss_multiple must be > 0, got {self.stop_loss_multiple}")
        if not 0 < self.portfolio_loss_cap_pct < 1:
            raise ValueError(
                f"portfolio_loss_cap_pct must be in (0, 1) (decimal), "
                f"got {self.portfolio_loss_cap_pct}"
            )
