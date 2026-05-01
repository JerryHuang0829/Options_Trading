"""Tests for src/backtest/execution.py (FillModel concrete impls).

Week 2 Day 5: 4 concrete FillModel classes + slippage convention tests.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.execution import (
    Fill,
    MidFillModel,
    SettleFillModel,
    SlippageFillModel,
    WorstSideFillModel,
)


@pytest.fixture
def chain_row() -> pd.Series:
    """Single TXO call row with bid<settle<ask."""
    return pd.Series(
        {
            "date": pd.Timestamp("2026-02-01"),
            "expiry": pd.Timestamp("2026-02-19"),
            "strike": 17000,
            "option_type": "call",
            "bid": 99.0,
            "ask": 105.0,
            "settle": 100.0,
        }
    )


def test_settle_fill_uses_settle_price(chain_row: pd.Series) -> None:
    fill = SettleFillModel().fill(chain_row, "sell", qty=1)
    assert fill.fill_price == pytest.approx(100.0)
    assert fill.model_name == "settle"
    assert fill.contract == "TXO20260219C17000"


def test_mid_fill_uses_bid_ask_midpoint(chain_row: pd.Series) -> None:
    fill = MidFillModel().fill(chain_row, "sell", qty=1)
    assert fill.fill_price == pytest.approx(102.0)
    assert fill.model_name == "mid"


def test_worst_side_seller_fills_at_bid(chain_row: pd.Series) -> None:
    fill = WorstSideFillModel().fill(chain_row, "sell", qty=1)
    assert fill.fill_price == pytest.approx(99.0)
    assert fill.side == "sell"


def test_worst_side_buyer_fills_at_ask(chain_row: pd.Series) -> None:
    fill = WorstSideFillModel().fill(chain_row, "buy", qty=1)
    assert fill.fill_price == pytest.approx(105.0)
    assert fill.side == "buy"


def test_slippage_fill_reduces_seller_premium(chain_row: pd.Series) -> None:
    """50 bps off mid: sell fills at mid * 0.995, buy fills at mid * 1.005."""
    model = SlippageFillModel(slippage_bps=50.0, base="mid")
    sell_fill = model.fill(chain_row, "sell", qty=1)
    buy_fill = model.fill(chain_row, "buy", qty=1)
    assert sell_fill.fill_price == pytest.approx(102.0 * 0.995)
    assert buy_fill.fill_price == pytest.approx(102.0 * 1.005)
    assert sell_fill.fill_price < buy_fill.fill_price


def test_slippage_fill_off_settle_base(chain_row: pd.Series) -> None:
    model = SlippageFillModel(slippage_bps=20.0, base="settle")
    fill = model.fill(chain_row, "sell", qty=1)
    assert fill.fill_price == pytest.approx(100.0 * 0.998)
    assert "settle" in fill.model_name


def test_fill_invalid_qty_raises(chain_row: pd.Series) -> None:
    with pytest.raises(ValueError):
        WorstSideFillModel().fill(chain_row, "sell", qty=0)


# =====================================================================
# R10.10 NaN guard tests (Codex P1-2 + decision 2c + 3ii, 2026-04-28)
# Per OptionMetrics / ORATS institutional standard:
#   sell needs bid; buy needs ask. Missing → raise (no silent NaN fill).
# =====================================================================


@pytest.fixture
def chain_row_nan_bid() -> pd.Series:
    """illiquid row: bid is NaN, ask exists."""
    return pd.Series(
        {
            "date": pd.Timestamp("2026-02-01"),
            "expiry": pd.Timestamp("2026-02-19"),
            "strike": 17000,
            "option_type": "call",
            "bid": float("nan"),
            "ask": 105.0,
            "settle": 100.0,
        }
    )


@pytest.fixture
def chain_row_nan_ask() -> pd.Series:
    """illiquid row: ask is NaN, bid exists."""
    return pd.Series(
        {
            "date": pd.Timestamp("2026-02-01"),
            "expiry": pd.Timestamp("2026-02-19"),
            "strike": 17000,
            "option_type": "call",
            "bid": 99.0,
            "ask": float("nan"),
            "settle": 100.0,
        }
    )


def test_worst_side_sell_raises_on_nan_bid(chain_row_nan_bid: pd.Series) -> None:
    """R10.10 decision 3ii: sell side needs bid; NaN → raise (was silent NaN fill)."""
    with pytest.raises(ValueError, match="non_executable.*sell.*bid"):
        WorstSideFillModel().fill(chain_row_nan_bid, "sell", qty=1)


def test_worst_side_buy_raises_on_nan_ask(chain_row_nan_ask: pd.Series) -> None:
    """R10.10 decision 3ii: buy side needs ask; NaN → raise."""
    with pytest.raises(ValueError, match="non_executable.*buy.*ask"):
        WorstSideFillModel().fill(chain_row_nan_ask, "buy", qty=1)


def test_worst_side_sell_ok_with_nan_ask(chain_row_nan_ask: pd.Series) -> None:
    """R10.10 decision 3ii: sell only needs bid; NaN ask should NOT block sell."""
    fill = WorstSideFillModel().fill(chain_row_nan_ask, "sell", qty=1)
    assert fill.fill_price == pytest.approx(99.0)


def test_worst_side_buy_ok_with_nan_bid(chain_row_nan_bid: pd.Series) -> None:
    """R10.10 decision 3ii: buy only needs ask; NaN bid should NOT block buy."""
    fill = WorstSideFillModel().fill(chain_row_nan_bid, "buy", qty=1)
    assert fill.fill_price == pytest.approx(105.0)


def test_mid_fill_raises_on_nan_bid(chain_row_nan_bid: pd.Series) -> None:
    """MidFillModel needs both bid+ask; NaN bid → raise."""
    with pytest.raises(ValueError, match="non_executable"):
        MidFillModel().fill(chain_row_nan_bid, "sell", qty=1)


def test_mid_fill_raises_on_nan_ask(chain_row_nan_ask: pd.Series) -> None:
    """MidFillModel needs both bid+ask; NaN ask → raise."""
    with pytest.raises(ValueError, match="non_executable"):
        MidFillModel().fill(chain_row_nan_ask, "buy", qty=1)


def test_slippage_mid_base_raises_on_nan_bid(chain_row_nan_bid: pd.Series) -> None:
    """SlippageFillModel base='mid' needs both bid+ask; NaN → raise."""
    model = SlippageFillModel(slippage_bps=10.0, base="mid")
    with pytest.raises(ValueError, match="non_executable"):
        model.fill(chain_row_nan_bid, "sell", qty=1)


def test_slippage_settle_base_ok_with_nan_bid_ask(chain_row_nan_bid: pd.Series) -> None:
    """SlippageFillModel base='settle' does NOT need bid/ask; NaN bid OK."""
    model = SlippageFillModel(slippage_bps=10.0, base="settle")
    fill = model.fill(chain_row_nan_bid, "sell", qty=1)
    assert fill.fill_price == pytest.approx(100.0 * 0.999)


def test_settle_fill_ok_with_nan_bid_ask(chain_row_nan_bid: pd.Series) -> None:
    """SettleFillModel uses settle only; NaN bid/ask should NOT block."""
    fill = SettleFillModel().fill(chain_row_nan_bid, "sell", qty=1)
    assert fill.fill_price == pytest.approx(100.0)
    with pytest.raises(ValueError):
        SettleFillModel().fill(chain_row_nan_bid, "buy", qty=-1)


def test_slippage_invalid_bps_raises() -> None:
    with pytest.raises(ValueError):
        SlippageFillModel(slippage_bps=-1.0)


def test_fill_dataclass_validates() -> None:
    """Sanity: Fill rejects invalid qty / option_type / side."""
    with pytest.raises(ValueError):
        Fill(
            date=pd.Timestamp("2026-02-01"),
            contract="TXO",
            strike=17000,
            option_type="call",
            side="sell",
            qty=0,  # invalid
            fill_price=100.0,
            model_name="settle",
        )
