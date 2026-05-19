"""Tests for quant.research.purged_kfold.purged_kfold_split.

Plan completion criterion (Month 2):
    pytest tests/test_purged_kfold.py passes
    — verifies no train/test overlap and correct embargo/purge zones.

The tests verify the two critical correctness properties:
    1. Zero train/test overlap in any fold.
    2. Purge zone: holding_period_days rows before test start excluded.
    3. Embargo zone: embargo_days rows after test end excluded.

These are the properties that the deleted engine.py violated — its
3-month/1-month overlapping windows shared ~67% of data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.research.purged_kfold import purged_kfold_split


# ── Helpers ───────────────────────────────────────────────────────────────────

def _business_dates(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=periods)


# ── Core correctness properties ───────────────────────────────────────────────

class TestCorrectnessProperties:
    """The properties that make purged k-fold scientifically sound."""

    def test_no_train_test_overlap(self):
        """Train and test index sets must be disjoint in every fold."""
        dates = _business_dates("2020-01-01", 200)
        splits = purged_kfold_split(dates, holding_period_days=10, k=5)

        for fold_i, (train, test) in enumerate(splits):
            overlap = set(train) & set(test)
            assert len(overlap) == 0, (
                f"Fold {fold_i}: found {len(overlap)} overlapping indices "
                f"between train and test sets. "
                "This is the bug that destroyed the previous system."
            )

    def test_test_folds_cover_all_indices(self):
        """Test folds (union across all folds) must cover every index exactly once."""
        dates = _business_dates("2020-01-01", 200)
        splits = purged_kfold_split(dates, holding_period_days=10, k=5)

        all_test_indices: list[int] = []
        for _, test in splits:
            all_test_indices.extend(test.tolist())

        assert sorted(all_test_indices) == list(range(len(dates))), (
            "Each sample must appear in exactly one test fold."
        )

    def test_purge_zone_is_excluded_from_train(self):
        """Positions within holding_period_days before test start must not be in train."""
        holding = 10
        dates = _business_dates("2020-01-01", 200)
        order = np.argsort(dates)  # original -> sorted mapping

        splits = purged_kfold_split(dates, holding_period_days=holding, k=5)

        for fold_i, (train, test) in enumerate(splits):
            # Find sorted positions for test
            # (We need to identify what positions in sorted order are in test)
            sorted_positions = np.arange(len(dates))
            test_set = set(test.tolist())
            # Get sorted position of first test index
            test_sorted_positions = [i for i in sorted_positions if order[i] in test_set]
            if not test_sorted_positions:
                continue
            test_start_sorted = min(test_sorted_positions)
            purge_start_sorted = max(0, test_start_sorted - holding)

            train_set = set(train.tolist())
            for sorted_pos in range(purge_start_sorted, test_start_sorted):
                orig_idx = order[sorted_pos]
                assert orig_idx not in train_set, (
                    f"Fold {fold_i}: sorted position {sorted_pos} (original index "
                    f"{orig_idx}) is in the purge zone but appears in train. "
                    "This is a label-leakage bug."
                )

    def test_embargo_zone_is_excluded_from_train(self):
        """Positions within embargo_days after test end must not be in train."""
        embargo = 10
        dates = _business_dates("2020-01-01", 200)
        order = np.argsort(dates)

        splits = purged_kfold_split(dates, holding_period_days=5, k=5, embargo_days=embargo)

        for fold_i, (train, test) in enumerate(splits):
            sorted_positions = np.arange(len(dates))
            test_set = set(test.tolist())
            test_sorted_positions = [i for i in sorted_positions if order[i] in test_set]
            if not test_sorted_positions:
                continue
            test_end_sorted = max(test_sorted_positions)
            embargo_end_sorted = min(len(dates) - 1, test_end_sorted + embargo)

            train_set = set(train.tolist())
            for sorted_pos in range(test_end_sorted + 1, embargo_end_sorted + 1):
                orig_idx = order[sorted_pos]
                assert orig_idx not in train_set, (
                    f"Fold {fold_i}: sorted position {sorted_pos} is in the "
                    f"embargo zone but appears in train."
                )

    def test_train_is_nonempty_for_reasonable_input(self):
        """With 200 observations and k=5, every fold should have a non-empty train set."""
        dates = _business_dates("2020-01-01", 200)
        splits = purged_kfold_split(dates, holding_period_days=10, k=5)

        for fold_i, (train, test) in enumerate(splits):
            assert len(train) > 0, f"Fold {fold_i} has an empty train set."

    def test_test_fold_sizes_are_approximately_equal(self):
        """Test folds should be balanced: each ~N/k observations."""
        n = 200
        k = 5
        dates = _business_dates("2020-01-01", n)
        splits = purged_kfold_split(dates, holding_period_days=10, k=k)

        fold_sizes = [len(test) for _, test in splits]
        expected = n // k
        for fi, sz in enumerate(fold_sizes):
            # allow off-by-one from array_split rounding
            assert abs(sz - expected) <= 1, (
                f"Fold {fi} test size {sz} deviates too much from expected {expected}."
            )


# ── Default embargo ───────────────────────────────────────────────────────────

class TestDefaultEmbargo:
    def test_default_embargo_is_max_of_2x_holding_or_10(self):
        """Default embargo = max(holding_period * 2, 10)."""
        dates = _business_dates("2020-01-01", 300)

        # With holding=3, default embargo should be max(6, 10) = 10
        splits_default = purged_kfold_split(dates, holding_period_days=3)
        splits_explicit = purged_kfold_split(dates, holding_period_days=3, embargo_days=10)

        for (train_d, test_d), (train_e, test_e) in zip(splits_default, splits_explicit):
            assert sorted(train_d.tolist()) == sorted(train_e.tolist())
            assert sorted(test_d.tolist()) == sorted(test_e.tolist())

        # With holding=20, default embargo should be max(40, 10) = 40
        splits_default2 = purged_kfold_split(dates, holding_period_days=20)
        splits_explicit2 = purged_kfold_split(dates, holding_period_days=20, embargo_days=40)

        for (train_d, test_d), (train_e, test_e) in zip(splits_default2, splits_explicit2):
            assert sorted(train_d.tolist()) == sorted(train_e.tolist())


# ── Input validation ──────────────────────────────────────────────────────────

class TestInputValidation:
    def test_raises_if_k_exceeds_n(self):
        """Should raise ValueError if fewer observations than folds."""
        dates = _business_dates("2020-01-01", 3)
        with pytest.raises(ValueError, match="at least k=5"):
            purged_kfold_split(dates, holding_period_days=1, k=5)

    def test_raises_if_k_less_than_2(self):
        """k=1 is degenerate (all train, no test); should raise."""
        dates = _business_dates("2020-01-01", 100)
        with pytest.raises(ValueError, match="k must be >= 2"):
            purged_kfold_split(dates, holding_period_days=1, k=1)

    def test_k_equals_2_works(self):
        """k=2 is the minimal valid case."""
        dates = _business_dates("2020-01-01", 100)
        splits = purged_kfold_split(dates, holding_period_days=5, k=2)
        assert len(splits) == 2
        # No overlap check
        for train, test in splits:
            assert len(set(train) & set(test)) == 0

    def test_accepts_list_input(self):
        """Input can be a plain Python list of date strings."""
        dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08",
                 "2020-01-09", "2020-01-10", "2020-01-13", "2020-01-14", "2020-01-15"]
        splits = purged_kfold_split(dates, holding_period_days=2, k=2)
        assert len(splits) == 2

    def test_accepts_pandas_datetimeindex(self):
        """Input can be a pd.DatetimeIndex."""
        dates = pd.bdate_range("2021-01-01", periods=50)
        splits = purged_kfold_split(dates, holding_period_days=5, k=5)
        assert len(splits) == 5


# ── Temporal ordering ─────────────────────────────────────────────────────────

class TestTemporalOrdering:
    def test_test_folds_are_in_chronological_order(self):
        """Test fold 0 must be earlier in time than fold 1, etc."""
        dates = _business_dates("2020-01-01", 200)
        splits = purged_kfold_split(dates, holding_period_days=10, k=5)

        test_date_maxima = []
        for _, test in splits:
            test_dates = pd.to_datetime(dates[test])
            test_date_maxima.append(test_dates.max())

        for i in range(len(test_date_maxima) - 1):
            assert test_date_maxima[i] < test_date_maxima[i + 1], (
                f"Test fold {i} ends after test fold {i+1} — folds are not chronological."
            )

    def test_all_train_samples_are_outside_test_period(self):
        """For each fold, no training sample should have a date inside the test window."""
        dates = _business_dates("2020-01-01", 200)
        splits = purged_kfold_split(dates, holding_period_days=10, k=5)

        for fold_i, (train, test) in enumerate(splits):
            if len(train) == 0:
                continue
            train_dates = pd.to_datetime(dates[train])
            test_dates = pd.to_datetime(dates[test])
            test_start = test_dates.min()
            test_end = test_dates.max()

            inside = (train_dates >= test_start) & (train_dates <= test_end)
            assert not inside.any(), (
                f"Fold {fold_i}: {inside.sum()} training samples fall inside "
                "the test date range."
            )
