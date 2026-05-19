"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014). Month 2.

The primary metric replacing raw Sharpe (per plan §3.1, §3.2).

Raw Sharpe is biased upward by multiple testing: if you tried N strategy
variants, the maximum observed Sharpe is higher than the true Sharpe of
any of them by an amount that grows with log(N). DSR backs that bias out.

    DSR = Phi( (SR - E[max SR | N trials]) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )

where:
    Phi is the standard normal CDF
    SR is the observed annualized Sharpe
    T is the number of return observations
    skew, kurt are the higher moments of the return series
    E[max SR | N trials] uses the order-statistic approximation

The trial count N is read from MLflow (every experiment counts, even
ones that 'failed' — peeking still costs you).

Gate threshold (plan §3.2): DSR >= 0.5 on dev; >= 0.4 on hold-out.
"""

from __future__ import annotations


def deflated_sharpe(returns: list[float], n_trials: int) -> float:
    raise NotImplementedError("deflated_sharpe — Month 2")
