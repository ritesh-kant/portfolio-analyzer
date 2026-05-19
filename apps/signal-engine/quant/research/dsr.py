"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

The primary metric replacing raw Sharpe.  See plan §3.1, §3.2.

Motivation
----------
Raw Sharpe is biased upward by multiple testing: if you evaluated N strategy
variants and reported the best, the observed maximum Sharpe is systematically
higher than the true Sharpe of any strategy by an amount that grows with N.

DSR quantifies the probability that the observed Sharpe of the *best*
strategy across N trials exceeds zero **after correcting for this bias**.
Threshold: DSR >= 0.5 on dev; >= 0.4 on hold-out (plan §3.2).

Mathematics
-----------
The Probabilistic Sharpe Ratio (PSR) asks:
    "Given one observed SR_hat, what is the probability the true SR > SR_0?"

    PSR(SR_0) = Phi[ (SR_hat - SR_0) * sqrt(T - 1)
                     / sqrt(1 - skew * SR_hat + (kurt - 1) / 4 * SR_hat^2) ]

Where ``SR_hat`` is the non-annualised (per-period / daily) Sharpe,
``T`` is the number of return observations, and kurt is regular kurtosis
(3 for Gaussian; NOT excess kurtosis).

The DSR sets SR_0 = E[max SR under N trials]:

    E[max SR] ≈ [(1 - gamma) * Phi^{-1}(1 - 1/N) + gamma * Phi^{-1}(1 - 1/(N*e))]
                / sqrt(T - 1)

where gamma ≈ 0.5772 is the Euler-Mascheroni constant, and the division by
sqrt(T-1) converts from z-score units (std-normal max) to per-period SR units
(where sigma(SR_hat) ≈ 1/sqrt(T-1) under the null).

Trial count
-----------
``n_trials`` is read from MLflow in production (plan §8.1).  Every experiment
counts — including failed ones and exploratory runs — because any peek at dev
results increases the multiple-testing penalty.

Calibration (rough, Gaussian returns)
--------------------------------------
T = 756 obs (3 years dev), N = 50 trials:
    Annualised SR ≈ 0.8  →  DSR ≈ 0.31  (below gate)
    Annualised SR ≈ 1.0  →  DSR ≈ 0.44  (just below gate)
    Annualised SR ≈ 1.1  →  DSR ≈ 0.58  (above gate)
    Annualised SR ≈ 1.5  →  DSR ≈ 0.77

Reference: Bailey, D. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
Journal of Portfolio Management.
"""

from __future__ import annotations

import math

import numpy as np
import scipy.stats as scipy_stats

# Euler-Mascheroni constant (γ)
_EULER_MASCHERONI: float = 0.5772156649015328

TRADING_DAYS_PER_YEAR: int = 252


def deflated_sharpe(
    returns: list[float] | np.ndarray,
    n_trials: int,
) -> float:
    """Deflated Sharpe Ratio.

    Parameters
    ----------
    returns : array-like of float
        Daily arithmetic return series (e.g. 0.01 = +1%).
        The series should cover only the dev or train split — never
        include hold-out observations.
    n_trials : int
        Total number of strategy/parameter experiments logged in MLflow
        for this research programme.  Minimum 1.

    Returns
    -------
    float in [0.0, 1.0]
        Probability that the true Sharpe Ratio exceeds zero after
        correcting for selection bias across ``n_trials`` experiments.
        Gate thresholds: >= 0.5 (dev), >= 0.4 (hold-out).
    """
    rets = np.asarray(returns, dtype=float)
    T = len(rets)
    if T < 5:
        return 0.0
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}.")

    # ── Per-period (daily, non-annualised) Sharpe ──────────────────────────────
    # The PSR formula is derived for non-annualised SR.  Annualising here would
    # require also adjusting the benchmark, so we keep everything per-period.
    mean_r = float(rets.mean())
    std_r = float(rets.std(ddof=1))
    if std_r < 1e-10:
        return 1.0 if mean_r > 0.0 else 0.0

    sr_hat = mean_r / std_r  # daily Sharpe

    # ── Higher moments ─────────────────────────────────────────────────────────
    skew = float(scipy_stats.skew(rets))
    # Regular kurtosis (NOT excess): 3.0 for Gaussian.
    # Bailey & LdP use (γ4 - 1)/4; with excess kurtosis that would give
    # negative values for leptokurtic series and is structurally wrong.
    kurt = float(scipy_stats.kurtosis(rets, fisher=False))

    # ── PSR denominator: accounts for non-normality of SR estimator ───────────
    denom_sq = 1.0 - skew * sr_hat + (kurt - 1.0) / 4.0 * sr_hat ** 2
    if denom_sq <= 0.0:
        # Numerically degenerate (very short or pathological series)
        return 0.0 if sr_hat <= 0.0 else 1.0

    # ── Expected maximum SR z-score across N independent trials ───────────────
    # Equation 10 from Bailey & LdP (2014):
    #   E[max of N iid N(0,1)] ≈ (1-γ)*Φ^{-1}(1-1/N) + γ*Φ^{-1}(1-1/(N*e))
    # Divide by sqrt(T-1) to convert to per-period SR units
    # (since sigma(SR_hat) ≈ 1/sqrt(T-1) under the null).
    if n_trials == 1:
        benchmark_sr = 0.0          # no multiple-testing adjustment needed
    else:
        z_N = (
            (1.0 - _EULER_MASCHERONI) * scipy_stats.norm.ppf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * scipy_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
        )
        benchmark_sr = z_N / math.sqrt(T - 1)

    # ── Z-score (PSR, equation 6 from Bailey & LdP) ───────────────────────────
    z = (sr_hat - benchmark_sr) * math.sqrt(T - 1) / math.sqrt(denom_sq)
    return float(scipy_stats.norm.cdf(z))


def annualised_sr_to_dsr(
    annualised_sr: float,
    n_observations: int,
    n_trials: int,
    skew: float = 0.0,
    excess_kurt: float = 0.0,
) -> float:
    """Convenience wrapper: compute DSR from an annualised Sharpe ratio.

    Converts ``annualised_sr`` to per-period SR for the PSR formula.

    Parameters
    ----------
    annualised_sr : float
        Annualised Sharpe ratio (e.g. 1.2).
    n_observations : int
        Number of daily return observations used to compute the SR.
    n_trials : int
        Total MLflow experiments.
    skew : float
        Skewness of daily returns (default 0 = Gaussian).
    excess_kurt : float
        *Excess* kurtosis of daily returns (default 0 = Gaussian).
        This is converted to regular kurtosis internally (kurt = excess + 3).
    """
    if n_observations < 5:
        return 0.0

    sr_daily = annualised_sr / math.sqrt(TRADING_DAYS_PER_YEAR)
    kurt = excess_kurt + 3.0  # regular kurtosis

    denom_sq = 1.0 - skew * sr_daily + (kurt - 1.0) / 4.0 * sr_daily ** 2
    if denom_sq <= 0.0:
        return 0.0 if sr_daily <= 0.0 else 1.0

    if n_trials == 1:
        benchmark_sr = 0.0
    else:
        z_N = (
            (1.0 - _EULER_MASCHERONI) * scipy_stats.norm.ppf(1.0 - 1.0 / n_trials)
            + _EULER_MASCHERONI * scipy_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
        )
        benchmark_sr = z_N / math.sqrt(n_observations - 1)

    z = (sr_daily - benchmark_sr) * math.sqrt(n_observations - 1) / math.sqrt(denom_sq)
    return float(scipy_stats.norm.cdf(z))
