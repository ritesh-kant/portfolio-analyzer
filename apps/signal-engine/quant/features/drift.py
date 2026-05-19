"""Feature drift monitor — Population Stability Index (PSI). Month 2.

For each feature, computes PSI between the live distribution (rolling 30 days)
and the training distribution.

    PSI < 0.10 — no shift
    0.10–0.25 — moderate shift (watch)
    > 0.25    — material shift; trigger warning + Bayesian P(strategy is dead) update
    > 0.50    — severe shift; freeze new entries on strategies using this feature

This is the single mechanism that would have caught the 2022 zero-trade
pattern in the previous system — features drifted, the strategy silently
sat in cash, no alarm fired.
"""

from __future__ import annotations


def compute_psi(reference: list[float], live: list[float], bins: int = 10) -> float:
    raise NotImplementedError("drift.compute_psi — Month 2")
