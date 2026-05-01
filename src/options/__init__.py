"""Options pricing maths core.

Self-written Black-Scholes-Merton (BSM) + Greeks + option chain utilities.
``py_vollib`` is used only as a pytest cross-reference, never at runtime.

Modules:
  pricing  -- bsm_price, implied_vol
  greeks   -- delta / gamma / theta / vega / rho (closed-form)
  chain    -- option chain filtering, pivoting, delta-based strike selection
"""
