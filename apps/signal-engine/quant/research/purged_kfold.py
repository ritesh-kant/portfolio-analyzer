"""Purged k-fold cross-validation with embargo.  López de Prado (2018).

Replaces the overlapping 3-month / 1-month walk-forward from the deleted
engine.py.  Adjacent folds in that design shared ~67% of their data — they
were not independent samples, and the 3-of-46 pass rate was even worse
than it looked because the wins were correlated folds.

Algorithm
---------
Input: a time-indexed dataset with T observations (business-day frequency).
Split into K consecutive folds by position.  For fold ``i``:

    test  = fold i
    purge = the ``holding_period_days`` positions immediately before
            ``test_start``.  A training label at position t whose holding
            period runs to t + h will bleed into the test window if
            t + h > test_start, i.e. t > test_start - h.  Removing these
            positions eliminates label-to-test information leakage.
    embargo = the ``embargo_days`` positions immediately after ``test_end``.
            Prices or features computed on test-set days may appear in
            rolling windows of near-future training samples.  The embargo
            prevents this.
    train = all positions NOT in (test ∪ purge ∪ embargo)

Position-based approach
-----------------------
Both ``holding_period_days`` and ``embargo_days`` are in business days
(rows in the sorted-date array), not calendar days.  This is correct as
long as the input ``dates`` array contains only business dates (the NSE
Bhavcopy lake guarantees this).

Hold-out is NOT part of this split.  See ``holdout_lock.py``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def purged_kfold_split(
    dates: pd.DatetimeIndex | pd.Series | list | np.ndarray,
    holding_period_days: int,
    k: int = 5,
    embargo_days: int | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Purged k-fold split with embargo for financial time series.

    Parameters
    ----------
    dates : array-like of datetime-castable values
        One entry per sample, representing the observation date.
        Should be business-day dates only (weekends/holidays excluded).
    holding_period_days : int
        Maximum holding period of the strategy's labels, in business days.
        Training samples whose label bleeds into the test window are purged.
        For PEAD with 5–10 day exits, pass 10 (conservative).
    k : int
        Number of folds (default 5).  Each fold is a consecutive time slice.
    embargo_days : int or None
        Number of business-day positions to embargo after each test fold.
        Defaults to ``max(holding_period_days * 2, 10)`` if None.

    Returns
    -------
    list of (train_indices, test_indices) tuples
        ``train_indices`` and ``test_indices`` are 1-D integer arrays
        indexing the original (unsorted) ``dates`` array.
        ``train_indices`` are already purged and embargoed.
        There is zero overlap between train and test in each tuple.

    Raises
    ------
    ValueError
        If ``len(dates) < k`` or ``k < 2``.

    Notes
    -----
    The function does NOT shuffle.  Fold boundaries are in chronological
    order: fold 0 is the earliest slice, fold k-1 the latest.  This
    ensures test folds are always strictly in the future of their
    training samples.
    """
    if k < 2:
        raise ValueError(f"k must be >= 2, got {k}.")

    if embargo_days is None:
        embargo_days = max(holding_period_days * 2, 10)

    dates_arr = pd.to_datetime(np.asarray(dates, dtype=object))
    n = len(dates_arr)

    if n < k:
        raise ValueError(
            f"Need at least k={k} observations for {k}-fold split, got {n}."
        )

    # Sort by date; ``order[i]`` = the original index of the i-th sorted sample
    order = np.argsort(dates_arr, kind="stable")  # shape (n,)

    # Split the *sorted* positions into K consecutive folds
    fold_pos_list: list[np.ndarray] = np.array_split(np.arange(n), k)

    splits: list[tuple[np.ndarray, np.ndarray]] = []

    for test_pos in fold_pos_list:
        test_idx = order[test_pos]           # original indices for test samples
        test_start_pos = int(test_pos[0])
        test_end_pos = int(test_pos[-1])

        # ── Purge zone: last holding_period_days positions before test ─────────
        # These training labels overlap the test window.
        purge_start_pos = max(0, test_start_pos - holding_period_days)
        purge_end_pos = test_start_pos - 1   # inclusive; -1 if test starts at 0

        # ── Embargo zone: first embargo_days positions after test ──────────────
        # Rolling-window features computed on test-period prices bleed here.
        embargo_start_pos = test_end_pos + 1
        embargo_end_pos = min(n - 1, test_end_pos + embargo_days)

        # ── Build excluded set (positions in sorted order) ─────────────────────
        excluded: set[int] = set()
        excluded.update(range(test_start_pos, test_end_pos + 1))          # test

        if purge_end_pos >= purge_start_pos:
            excluded.update(range(purge_start_pos, purge_end_pos + 1))    # purge

        if embargo_start_pos <= embargo_end_pos:
            excluded.update(range(embargo_start_pos, embargo_end_pos + 1))  # embargo

        # ── Train = everything else ────────────────────────────────────────────
        train_sorted_pos = [p for p in range(n) if p not in excluded]
        train_idx = (
            order[train_sorted_pos]
            if train_sorted_pos
            else np.array([], dtype=np.intp)
        )

        splits.append((train_idx, test_idx))

    return splits
