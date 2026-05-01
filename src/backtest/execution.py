"""Fill model abstractions for backtest execution assumptions.

Codex Round 1 flagged that "settle used as fill price" hard-coded in the
engine is too optimistic for short-premium strategies (IC / Vertical):
market makers quote bid < settle < ask, and a seller realises something
close to bid, not settle. Using settle overstates premium collected by
roughly half the spread × multiplier per leg.

This module defines an abstract ``FillModel`` so strategies and engine
can pick the fill assumption that matches their research question:

  - ``SettleFillModel``: legacy; use settle as fill (optimistic for sellers)
  - ``MidFillModel``: use (bid+ask)/2; requires bid/ask in chain
  - ``WorstSideFillModel``: seller fills at bid, buyer fills at ask (realistic)
  - ``SlippageFillModel``: apply bps slippage off mid/settle

Week 2 Day 5: concrete implementations. GPT-5.5 + Codex R4 共識：
``WorstSideFillModel`` is the default in `engine.run_backtest`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pandas as pd


# Closed sets for typed string fields (Codex R2: prevent "sel"/"mdi" typos).
Side = Literal["buy", "sell"]
OptionType = Literal["call", "put"]
SlippageBase = Literal["mid", "settle"]


@dataclass(frozen=True)
class RetailCostModel:
    """Retail-realistic execution cost dataclass (Week 6 Day 6.4).

    Per `feedback_pro_research_standard.md` 紀律: TXO retail backtest 必含
    commission + 期交稅 + slippage 三項摩擦。FillModel 任一 subclass 在 __init__
    可注入此 cost_model；fill() 先算 raw fill_price (worst_side / mid / settle)
    再 layer slippage + commission + tax 進 Fill dataclass cost fields。

    Defaults 對齊 TAIFEX TXO 真規則 (R12.0 P4a fix per Codex audit):
      - commission_per_contract = NT$12 (retail 永豐 / 新光 / 元大 typical)
      - tax_bps = 10.0 (TXO 期交稅 0.001 decimal = 0.1% on premium notional;
        TAIFEX fee schedule TXO/TEO/TFO/ETF Options rate 0.001 of premium ×
        multiplier; per leg per side). 原 default 2.0 是 5x 低估錯誤 (Codex
        R12.0 audit 抓到)。Pro caller 可 override 至 30 bps 做 conservative buffer.
      - slippage_bps = 15.0 (10-20 bps retail TXO market order typical;
        在 RetailCostFillModel 加成於 base fill_price)

    Tax base computation (per TAIFEX schedule):
        tax = premium_pts × multiplier × qty × tax_bps / 10000
            (premium notional in NTD × rate)

    Attack #5 (Pattern 0): cost_model.slippage_bps + SlippageFillModel.slippage_bps
        相加而非取大 (compounding) — caller 若 SlippageFillModel base 已含 slippage
        再 wrap RetailCostModel.slippage_bps 會 double count, 警告 docstring 標。
    """

    commission_per_contract: float = 12.0
    tax_bps: float = 10.0  # R12.0 P3 fix: TAIFEX 0.001 = 10 bps (was 2.0 = 5x low)
    slippage_bps: float = 15.0

    def __post_init__(self) -> None:
        if self.commission_per_contract < 0:
            raise ValueError(
                f"commission_per_contract must be >= 0, got {self.commission_per_contract!r}"
            )
        if self.tax_bps < 0:
            raise ValueError(f"tax_bps must be >= 0, got {self.tax_bps!r}")
        if self.slippage_bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {self.slippage_bps!r}")


@dataclass(frozen=True)
class Fill:
    """Single leg fill event emitted by a FillModel.

    Attributes:
        date: Trading day of the fill.
        contract: Contract identifier (e.g. "TXO202507C17000").
        strike: Strike price.
        option_type: ``"call"`` or ``"put"`` (closed set).
        side: ``"buy"`` or ``"sell"`` (closed set).
        qty: Positive number of contracts (>= 1; sign convention lives on OptionLeg).
        fill_price: Actual fill price in index points (retail slippage already applied
            if FillModel was constructed with cost_model).
        model_name: Which FillModel produced this (for audit).
        commission: TWD commission for this fill (qty × commission_per_contract;
            0 if no cost_model).
        tax: TWD 期交稅 for this fill (|fill_price| × multiplier × qty × tax_bps/1e4;
            0 if no cost_model).
    """

    date: pd.Timestamp
    contract: str
    strike: int
    option_type: OptionType
    side: Side
    qty: int
    fill_price: float
    model_name: str
    commission: float = 0.0
    tax: float = 0.0

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"Fill.qty must be positive, got {self.qty!r}")
        if self.option_type not in ("call", "put"):
            raise ValueError(f"Fill.option_type must be 'call'|'put', got {self.option_type!r}")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Fill.side must be 'buy'|'sell', got {self.side!r}")
        if self.commission < 0:
            raise ValueError(f"Fill.commission must be >= 0, got {self.commission!r}")
        if self.tax < 0:
            raise ValueError(f"Fill.tax must be >= 0, got {self.tax!r}")


TXO_MULTIPLIER_DEFAULT = 50  # TWD per index point (TAIFEX TXO contract spec)


class FillModel(ABC):
    """Abstract fill model — defines how theoretical orders become fills.

    Day 6.4 加 cost_model 注入: subclass __init__ 接受 RetailCostModel | None。
    當 cost_model is None (default) → fill_price / commission / tax 與 Day 5
    既有行為完全相同 (backward compat)。當 cost_model is set → fill_price 套
    slippage; commission + tax 寫入 Fill cost fields，由 caller (engine /
    portfolio) 從 cash + realised_pnl_total 同步扣 (R10.x cum_pnl invariant
    紀律: cum_pnl = realised_pnl_total + unrealised — costs 算 realised loss)。
    """

    def __init__(self, cost_model: RetailCostModel | None = None) -> None:
        self.cost_model = cost_model

    @abstractmethod
    def fill(
        self,
        chain_row: pd.Series,
        side: Side,
        qty: int,
    ) -> Fill:
        """Return a ``Fill`` given a chain quote row + side + quantity.

        Args:
            chain_row: One row from an option chain DataFrame (has settle, bid, ask).
            side: ``"buy"`` or ``"sell"``.
            qty: Positive number of contracts.
        """
        raise NotImplementedError

    def _apply_retail_costs(
        self,
        raw_price: float,
        side: Side,
        qty: int,
    ) -> tuple[float, float, float]:
        """Layer cost_model on top of raw fill_price.

        Returns:
            (adjusted_fill_price, commission_twd, tax_twd)
            cost_model is None → (raw_price, 0.0, 0.0) (backward compat).

        Cost model:
            adjusted_price = raw_price * (1 - slip) if sell else raw_price * (1 + slip)
            commission = qty * commission_per_contract  (TWD; per-leg)
            tax = |adjusted_price| * multiplier * qty * tax_bps / 1e4  (TWD)
        """
        if self.cost_model is None:
            return raw_price, 0.0, 0.0
        slip = self.cost_model.slippage_bps / 10_000.0
        adjusted_price = raw_price * (1.0 - slip) if side == "sell" else raw_price * (1.0 + slip)
        commission = qty * self.cost_model.commission_per_contract
        tax = (
            abs(adjusted_price) * TXO_MULTIPLIER_DEFAULT * qty * self.cost_model.tax_bps / 10_000.0
        )
        return adjusted_price, commission, tax


def _build_contract(chain_row: pd.Series) -> str:
    """Build canonical contract id from a chain row (matches IronCondor convention)."""
    import pandas as pd_local

    expiry_str = pd_local.Timestamp(chain_row["expiry"]).strftime("%Y%m%d")
    flag = "C" if chain_row["option_type"] == "call" else "P"
    return f"TXO{expiry_str}{flag}{int(chain_row['strike'])}"


class SettleFillModel(FillModel):
    """Use settle as fill price. Optimistic; matches legacy retail tools."""

    def fill(self, chain_row: pd.Series, side: Side, qty: int) -> Fill:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        import pandas as pd_local

        raw_price = float(chain_row["settle"])
        price, commission, tax = self._apply_retail_costs(raw_price, side, qty)
        return Fill(
            date=pd_local.Timestamp(chain_row["date"]),
            contract=_build_contract(chain_row),
            strike=int(chain_row["strike"]),
            option_type=chain_row["option_type"],
            side=side,
            qty=qty,
            fill_price=price,
            model_name="settle",
            commission=commission,
            tax=tax,
        )


def _assert_executable(chain_row: pd.Series, side: Side) -> None:
    """R10.10 NaN guard (Codex P1-2 + decision 2c, 2026-04-28).

    Side-specific check (decision 3ii): sell needs bid, buy needs ask.
    Per OptionMetrics / ORATS institutional standard: 不可成交不該 fill.
    """
    import pandas as pd_local

    if side == "sell":
        bid = chain_row.get("bid")
        if bid is None or pd_local.isna(bid):
            raise ValueError(
                f"non_executable: side='sell' requires bid but bid is NaN/None "
                f"(strike={chain_row.get('strike')}, type={chain_row.get('option_type')}, "
                f"expiry={chain_row.get('expiry')}, date={chain_row.get('date')})"
            )
    else:  # buy
        ask = chain_row.get("ask")
        if ask is None or pd_local.isna(ask):
            raise ValueError(
                f"non_executable: side='buy' requires ask but ask is NaN/None "
                f"(strike={chain_row.get('strike')}, type={chain_row.get('option_type')}, "
                f"expiry={chain_row.get('expiry')}, date={chain_row.get('date')})"
            )


class MidFillModel(FillModel):
    """Use (bid + ask) / 2 as fill. Requires bid/ask in chain."""

    def fill(self, chain_row: pd.Series, side: Side, qty: int) -> Fill:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        # R10.10: side-specific NaN guard (Mid 需 bid+ask 兩邊都要)
        _assert_executable(chain_row, "sell")  # need bid
        _assert_executable(chain_row, "buy")  # need ask
        import pandas as pd_local

        bid = float(chain_row["bid"])
        ask = float(chain_row["ask"])
        raw_price = (bid + ask) / 2.0
        price, commission, tax = self._apply_retail_costs(raw_price, side, qty)
        return Fill(
            date=pd_local.Timestamp(chain_row["date"]),
            contract=_build_contract(chain_row),
            strike=int(chain_row["strike"]),
            option_type=chain_row["option_type"],
            side=side,
            qty=qty,
            fill_price=price,
            model_name="mid",
            commission=commission,
            tax=tax,
        )


class WorstSideFillModel(FillModel):
    """Sell fills at bid, buy fills at ask — realistic for retail market orders.

    Engine default per GPT-5.5 + Codex R4 共識: settle-as-fill overstates
    premium collected on shorts and understates premium paid on longs.
    """

    def fill(self, chain_row: pd.Series, side: Side, qty: int) -> Fill:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        # R10.10: side-specific NaN guard (decision 3ii)
        _assert_executable(chain_row, side)
        import pandas as pd_local

        raw_price = float(chain_row["bid"]) if side == "sell" else float(chain_row["ask"])
        price, commission, tax = self._apply_retail_costs(raw_price, side, qty)
        return Fill(
            date=pd_local.Timestamp(chain_row["date"]),
            contract=_build_contract(chain_row),
            strike=int(chain_row["strike"]),
            option_type=chain_row["option_type"],
            side=side,
            qty=qty,
            fill_price=price,
            model_name="worst_side",
            commission=commission,
            tax=tax,
        )


class SlippageFillModel(FillModel):
    """Apply a bps slippage off mid or settle (configurable).

    Convention: slippage hurts the trader on both sides.
      - sell: fill_price = base * (1 - slippage_bps / 10_000)
      - buy:  fill_price = base * (1 + slippage_bps / 10_000)
    """

    def __init__(
        self,
        slippage_bps: float = 10.0,
        base: SlippageBase = "mid",
        cost_model: RetailCostModel | None = None,
    ) -> None:
        super().__init__(cost_model=cost_model)
        if base not in ("mid", "settle"):
            raise ValueError(f"SlippageFillModel.base must be 'mid'|'settle', got {base!r}")
        if slippage_bps < 0:
            raise ValueError(f"slippage_bps must be >= 0, got {slippage_bps!r}")
        self.slippage_bps = slippage_bps
        self.base: SlippageBase = base

    def fill(self, chain_row: pd.Series, side: Side, qty: int) -> Fill:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        # R10.10: NaN guard 只 mid base 需要 (settle base 不需 bid/ask)
        if self.base == "mid":
            _assert_executable(chain_row, "sell")  # need bid
            _assert_executable(chain_row, "buy")  # need ask
        import pandas as pd_local

        if self.base == "mid":
            base_price = float((chain_row["bid"] + chain_row["ask"]) / 2.0)
        else:
            base_price = float(chain_row["settle"])
        slip = self.slippage_bps / 10_000.0
        slippage_adjusted = (
            base_price * (1.0 - slip) if side == "sell" else base_price * (1.0 + slip)
        )
        # cost_model.slippage_bps compounds on top of native slippage_bps if both set;
        # caller responsibility — see RetailCostModel docstring (Pattern 0 attack #4).
        price, commission, tax = self._apply_retail_costs(slippage_adjusted, side, qty)
        return Fill(
            date=pd_local.Timestamp(chain_row["date"]),
            contract=_build_contract(chain_row),
            strike=int(chain_row["strike"]),
            option_type=chain_row["option_type"],
            side=side,
            qty=qty,
            fill_price=price,
            model_name=f"slippage_{self.base}_{self.slippage_bps:.0f}bps",
            commission=commission,
            tax=tax,
        )
