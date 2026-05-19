"""Strategy A — Post-Earnings Drift on Nifty Midcap 150. Month 3.

Pre-registered hypothesis (locked before any code is written):
    After a positive earnings surprise > 1 standard deviation of consensus,
    with day-of price reaction <= 50% of historical PEAD response, Nifty
    Midcap 150 stocks drift up 80-120 bps over the next 5 trading days
    net of costs.

Falsification criterion (also locked):
    If realized 5-day drift on the dev set (2023-07-01 to 2024-06-30) after
    the gate filter is < 40 bps mean or Sharpe < 0.5, the strategy is killed.
    No "let me tune one parameter and re-run".

See plan §5.1 + §3 (validation gate).

Universe: Nifty Midcap 150 (NOT Nifty 50/100 — those have no retail edge).
Holding period: 5-10 trading days from earnings announcement +1.
Capacity target: hold up to Rs 50L AUM with realistic slippage.
"""

from __future__ import annotations


def select_candidates(business_date: str):
    raise NotImplementedError("pead_midcap.select_candidates — Month 3")
