"""Bayesian posterior-Kelly position sizer. Month 3.

Replaces the deleted static-Kelly chain in backtest/order_simulator.py.
The fundamental fix: win probabilities are *estimated* from realized trade
outcomes, not authored as constants.

    p_win  = Beta(alpha + wins_in_strata, beta + losses_in_strata).mean()
             stratified by (strategy, regime, model_decile)
    r      = E[R-multiple | win] from the L3 model's regression head
    f_kelly = (p_win * r - (1 - p_win)) / r
    f_used = clip(f_kelly / 4, 0, 0.05)   # quarter-Kelly, max 5% per position

Quarter-Kelly (not half) because:
    a) posterior uncertainty is real;
    b) backtest-realized win rates over-state live by 5-10pp typically;
    c) emotional drawdown tolerance is half what you think.

Each closed trade updates the posterior. Cold-start: weakly-informative
prior alpha = beta = 2 (one-each) so the first ~30 trades barely move
size from the prior mean of 0.5.
"""

from __future__ import annotations


def size_position(strategy: str, regime: str, model_decile: int, expected_r: float):
    raise NotImplementedError("posterior_kelly.size_position — Month 3")
