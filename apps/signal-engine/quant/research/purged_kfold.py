"""Purged k-fold with embargo (Lopez de Prado). Month 2.

Replaces the overlapping 3-month / 1-month walk-forward from the deleted
engine.py. Adjacent folds in the old design shared 67% of their data —
not independent samples; the 3-of-46 pass rate was even worse than it
looked.

This validator splits the train/dev period into K=5 folds. For each fold:
    1. Test set = the fold itself
    2. Train set = all other folds MINUS:
        - rows whose target overlaps test-set rows (purge)
        - rows within `embargo` business days after test set end
    3. Embargo defaults to max(holding_period * 2, 10 business days)

Hold-out (2024-07-01 onwards) is OUTSIDE this validator. See holdout_lock.py.
"""

from __future__ import annotations


def purged_kfold_split(dates, holding_period_days: int, k: int = 5, embargo_days: int = 10):
    raise NotImplementedError("purged_kfold_split — Month 2")
