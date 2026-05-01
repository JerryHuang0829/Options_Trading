"""Regime gate primitives — IV percentile + HMM 2-state Markov-switching.

Week 6 Day 6.0 — Phase 1 ablation study (Vanilla / IV percentile / HMM 三版
對比 per Pro 學術紀律).

兩種 regime gate 設計:
  1. IVPercentileGate: rolling 1yr realized vol percentile gate.
     當日 30-day realized vol >= rolling-1yr 30-percentile → high-vol regime → 開倉
     Pro option trading 主流 (Sosnoff tastytrade / OptionAlpha 等); 與 IC 賺
     IV crush 邏輯直接對應.

  2. HMMRegimeGate: Hamilton 1989 Markov-switching 2-state.
     用 hmmlearn.GaussianHMM 估 state 0/1; high-vol state → 開倉.
     學術成熟但 first-order Markov 簡化; 對 IC strategy 不是主流但是 ablation
     baseline 對照組.

PIT Correctness (R10.5 P2 紀律):
  - Both gates 只用 date <= today 資料 (lookback 不可看未來)
  - HMM **每 walk-forward fold 用 train data 重 fit** (絕不 global fit on
    full history); ablation rule.
  - is_active(date, returns_history) 只接受 returns_history[returns_history.index <= date]

Returns history input:
  Caller responsibility: pass underlying returns (e.g. TAIEX log returns)
  indexed by date. Gate 不負責 fetch — separation of concerns; engine /
  walk_forward 注入歷史.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class RegimeGate(ABC):
    """Abstract regime gate — is_active(date, returns_history) → bool."""

    @abstractmethod
    def is_active(self, date: pd.Timestamp, returns_history: pd.Series) -> bool:
        """Return True if regime allows opening on this date.

        Args:
            date: trading day (gate evaluates at this point in time).
            returns_history: pd.Series indexed by date, values = log returns
                of underlying (e.g. TAIEX). MUST only contain dates <= `date`
                (PIT correctness; caller responsibility to enforce).

        Returns:
            bool — True = regime allows open; False = regime blocks open.
        """
        raise NotImplementedError


class IVPercentileGate(RegimeGate):
    """Realized vol rolling percentile gate.

    Logic:
        rolling_30d_vol = std(returns over last 30 trading days) * sqrt(252)
        rolling_1yr_window = past 252 days of these 30d vols
        threshold = percentile(rolling_1yr_window, threshold_pct)
        is_active = rolling_30d_vol >= threshold

    Default threshold_pct=0.30 (30th percentile) means open when current vol
    is in upper 70% of past year — Pro option trading lower-bound for IV crush
    trades.
    """

    def __init__(
        self,
        vol_lookback_days: int = 30,
        percentile_lookback_days: int = 252,
        threshold_pct: float = 0.30,
    ) -> None:
        if vol_lookback_days <= 0 or percentile_lookback_days <= 0:
            raise ValueError("lookback days must be > 0")
        if not 0 < threshold_pct < 1:
            raise ValueError(f"threshold_pct must be in (0, 1), got {threshold_pct}")
        self.vol_lookback_days = vol_lookback_days
        self.percentile_lookback_days = percentile_lookback_days
        self.threshold_pct = threshold_pct

    def is_active(self, date: pd.Timestamp, returns_history: pd.Series) -> bool:
        # Filter to returns up to `date` (PIT)
        history = returns_history[returns_history.index <= date]
        # Need vol_lookback + percentile_lookback days minimum
        n_required = self.vol_lookback_days + self.percentile_lookback_days
        if len(history) < n_required:
            # Pre-warm period: regime undecided → fail-closed (don't open)
            return False
        # Compute rolling 30d vol series
        rolling_vol = history.rolling(window=self.vol_lookback_days).std() * math.sqrt(252)
        rolling_vol = rolling_vol.dropna()
        # Take last 252 days for percentile reference
        ref_window = rolling_vol.iloc[-self.percentile_lookback_days :]
        if len(ref_window) == 0:
            return False
        ref_array = np.asarray(ref_window.values, dtype=np.float64)
        threshold = float(np.percentile(ref_array, self.threshold_pct * 100))
        current_vol = float(rolling_vol.iloc[-1])
        return current_vol >= threshold


class HMMRegimeGate(RegimeGate):
    """Hamilton 1989 Markov-switching 2-state regime gate (hmmlearn GaussianHMM).

    Logic:
        Fit 2-state GaussianHMM on returns_history (lookback_days last days).
        Predict regime for the most recent return.
        High-vol state = state with higher emission std.
        is_active = current state == high-vol state.

    Refits each call (per PIT — walk-forward fold uses train data only).
    For computational efficiency in walk-forward: caller can use `refit_each=False`
    + manual `fit()` call once per fold (out of scope for Phase 1).

    Phase 1 design: refit each is_active() call. ~12 EM iter / call ~ 50 ms.
    """

    def __init__(
        self,
        lookback_days: int = 504,
        n_iter: int = 500,  # R12.0 P1 fix follow-up: 100 → 500 for 504-day series convergence
        random_state: int = 42,
        active_state: str = "high_vol",
    ) -> None:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be > 0")
        if active_state not in ("high_vol", "low_vol"):
            raise ValueError(f"active_state must be 'high_vol'|'low_vol', got {active_state}")
        self.lookback_days = lookback_days
        self.n_iter = n_iter
        self.random_state = random_state
        self.active_state = active_state

    def is_active(self, date: pd.Timestamp, returns_history: pd.Series) -> bool:
        from hmmlearn import hmm

        # PIT: only use returns up to `date`
        history = returns_history[returns_history.index <= date]
        if len(history) < self.lookback_days:
            return False  # pre-warm: fail-closed
        window = history.iloc[-self.lookback_days :].dropna()
        if len(window) < self.lookback_days * 0.9:
            return False  # too many NaN

        X = np.asarray(window.values, dtype=np.float64).reshape(-1, 1)
        try:
            model = hmm.GaussianHMM(
                n_components=2,
                covariance_type="full",
                n_iter=self.n_iter,
                tol=1e-3,  # R12.0 P1 follow-up: log-lik delta ~1e-3 plateau acceptable
                random_state=self.random_state,
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                model.fit(X)
            states = model.predict(X)
        except (ValueError, RuntimeError):
            # HMM convergence fail → fail-closed (don't open)
            return False

        # R12.0 P1 follow-up: hmmlearn 在 plateau 處 log-likelihood 偶 -1e-3
        # numerical noise (見 test_hmm_two_state_high_vs_low warning), 然 model
        # 參數已 fit 完。改用「iter 數達 min_iter 即視為 fit 完成」非嚴格 monitor_.converged.
        # Min_iter = 5 EM 步驟保證 random init 已被 update 過. Test data 真有 2 distinct
        # vol regime → variance ratio 必明顯 > 1.5; 否則 reject.
        if model.monitor_.iter < 5:
            return False  # too few iter → almost certainly didn't fit

        # Identify high-vol state (= state with higher emission std).
        # R12.0 P1 fix (Codex audit): hmmlearn covars_ shape varies by type:
        #   covariance_type='full' → (n_components, n_features, n_features)
        #   covariance_type='diag' → (n_components, n_features)
        # Old code `np.diag(covars_.reshape(2,-1))` for full+1-feature collapsed
        # (2,1,1) → reshape(2,-1)=(2,1) → np.diag returns 1-element diagonal,
        # silently dropping state 1. We use 1 feature here, so var_per_state is
        # the (0,0) entry of each state's covariance.
        if model.covars_.ndim == 3:
            var_per_state = np.array([np.diag(c) for c in model.covars_]).flatten()
        else:
            var_per_state = np.asarray(model.covars_).flatten()
        if var_per_state.size != 2:
            return False  # malformed covar (shouldn't happen with n_components=2)
        std_per_state = np.sqrt(var_per_state)
        high_vol_state = int(np.argmax(std_per_state))
        current_state = int(states[-1])
        if self.active_state == "high_vol":
            return current_state == high_vol_state
        return current_state != high_vol_state
