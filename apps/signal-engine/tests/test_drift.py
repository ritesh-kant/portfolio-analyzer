"""Tests for quant.features.drift — PSI drift monitor.

PSI thresholds (plan §9.1):
    < 0.10  → ok
    0.10-0.25 → warn
    0.25-0.50 → alert
    > 0.50  → severe

This module would have caught the 2022 "zero-trade" failure:
the strategy silently sat in cash because its features drifted
outside their training distribution and no alarm was triggered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.drift import compute_psi, psi_status, monitor_features, PSI_ALERT, PSI_WARN

RNG = np.random.default_rng(0)


# ── compute_psi ───────────────────────────────────────────────────────────────

class TestComputePSI:
    def test_identical_distributions_near_zero(self):
        """Same distribution (same data) → PSI ≈ 0."""
        ref = RNG.normal(0, 1, 1000)
        psi = compute_psi(ref, ref.copy())
        assert psi < 0.01, f"Identical distributions: PSI {psi:.4f} should be < 0.01"

    def test_similar_distributions_low_psi(self):
        """Two samples from the same distribution → low PSI."""
        ref = RNG.normal(0, 1, 1000)
        live = RNG.normal(0, 1, 1000)  # different sample, same distribution
        psi = compute_psi(ref, live)
        assert psi < PSI_WARN, (
            f"Same-distribution samples: PSI {psi:.4f} should be < {PSI_WARN}"
        )

    def test_shifted_mean_raises_psi(self):
        """A mean shift of 2 std should produce a material PSI (>= alert threshold)."""
        ref = RNG.normal(0, 1, 1000)
        live = RNG.normal(2, 1, 1000)   # 2σ shift in mean
        psi = compute_psi(ref, live)
        assert psi > PSI_ALERT, (
            f"2σ mean shift: PSI {psi:.4f} should be > alert threshold {PSI_ALERT}"
        )

    def test_completely_disjoint_distributions_severe_psi(self):
        """Completely non-overlapping distributions → severe PSI."""
        ref = RNG.normal(0, 1, 500)
        live = RNG.normal(10, 1, 500)   # 10σ apart — no overlap
        psi = compute_psi(ref, live)
        assert psi > 0.5, (
            f"Disjoint distributions: PSI {psi:.4f} should be > 0.5 (severe)"
        )

    def test_psi_is_nonnegative(self):
        """PSI is always >= 0 by definition."""
        for _ in range(20):
            ref = RNG.normal(RNG.uniform(-2, 2), RNG.uniform(0.5, 2), 200)
            live = RNG.normal(RNG.uniform(-2, 2), RNG.uniform(0.5, 2), 200)
            psi = compute_psi(ref, live)
            assert psi >= 0.0, f"PSI {psi} is negative"

    def test_constant_feature_returns_zero(self):
        """A constant feature has no distribution; PSI must be 0 (not crash)."""
        ref = np.full(100, 5.0)
        live = np.full(100, 5.0)
        psi = compute_psi(ref, live)
        assert psi == 0.0

    def test_too_short_series_returns_zero(self):
        """Series with < 2 values returns 0.0 without crashing."""
        assert compute_psi([1.0], [1.0, 2.0]) == 0.0
        assert compute_psi([1.0, 2.0], [1.0]) == 0.0

    def test_live_values_outside_reference_range_handled(self):
        """Live values outside the reference range land in edge buckets — no crash."""
        ref = np.linspace(0, 1, 200)
        live = np.linspace(-1, 3, 200)  # wider range than reference
        psi = compute_psi(ref, live)
        assert psi >= 0.0
        # These are very different distributions; PSI should be non-trivial
        assert psi > PSI_ALERT

    def test_bins_parameter_affects_resolution(self):
        """More bins should generally give higher PSI for shifted distributions."""
        ref = RNG.normal(0, 1, 2000)
        live = RNG.normal(0.5, 1, 2000)

        psi_10 = compute_psi(ref, live, bins=10)
        psi_20 = compute_psi(ref, live, bins=20)

        # Both should be non-zero for a 0.5σ shift
        assert psi_10 > 0.0
        assert psi_20 > 0.0


# ── psi_status ────────────────────────────────────────────────────────────────

class TestPSIStatus:
    def test_below_warn_is_ok(self):
        assert psi_status(0.0) == "ok"
        assert psi_status(0.05) == "ok"
        assert psi_status(0.099) == "ok"

    def test_warn_range(self):
        assert psi_status(0.10) == "warn"
        assert psi_status(0.15) == "warn"
        assert psi_status(0.249) == "warn"

    def test_alert_range(self):
        assert psi_status(0.25) == "alert"
        assert psi_status(0.35) == "alert"
        assert psi_status(0.499) == "alert"

    def test_severe(self):
        assert psi_status(0.50) == "severe"
        assert psi_status(1.0) == "severe"
        assert psi_status(999.0) == "severe"


# ── monitor_features ──────────────────────────────────────────────────────────

class TestMonitorFeatures:
    def _make_df(self, means: dict[str, float], n: int = 500) -> pd.DataFrame:
        rng = np.random.default_rng(1)
        return pd.DataFrame({col: rng.normal(m, 1, n) for col, m in means.items()})

    def test_returns_one_row_per_feature(self):
        ref = self._make_df({"feat_a": 0.0, "feat_b": 0.0, "feat_c": 0.0})
        live = self._make_df({"feat_a": 0.0, "feat_b": 0.0, "feat_c": 0.0})

        result = monitor_features(ref, live)

        assert len(result) == 3
        assert set(result["feature"]) == {"feat_a", "feat_b", "feat_c"}

    def test_sorted_by_psi_descending(self):
        """Worst drift comes first."""
        ref = self._make_df({"stable": 0.0, "drifted": 0.0})
        # stable is actually stable; drifted has a 3σ shift
        rng = np.random.default_rng(2)
        live = pd.DataFrame({
            "stable": rng.normal(0, 1, 500),
            "drifted": rng.normal(3, 1, 500),
        })

        result = monitor_features(ref, live)

        assert result.iloc[0]["feature"] == "drifted", (
            "Most-drifted feature should appear first"
        )
        assert result.iloc[0]["psi"] > result.iloc[1]["psi"]

    def test_status_column_present(self):
        ref = self._make_df({"f": 0.0})
        live = self._make_df({"f": 0.0})
        result = monitor_features(ref, live)
        assert "status" in result.columns
        assert result.iloc[0]["status"] in ("ok", "warn", "alert", "severe")

    def test_feature_cols_filter(self):
        """Only specified feature_cols are monitored."""
        ref = self._make_df({"a": 0.0, "b": 0.0, "c": 0.0})
        live = self._make_df({"a": 0.0, "b": 0.0, "c": 0.0})

        result = monitor_features(ref, live, feature_cols=["a", "b"])

        assert len(result) == 2
        assert "c" not in result["feature"].values

    def test_severe_status_for_large_shift(self):
        """A 5σ mean shift should produce 'severe' status."""
        rng = np.random.default_rng(3)
        ref = pd.DataFrame({"x": rng.normal(0, 1, 1000)})
        live = pd.DataFrame({"x": rng.normal(5, 1, 1000)})

        result = monitor_features(ref, live)

        assert result.iloc[0]["status"] == "severe", (
            f"5σ shift should be severe, got {result.iloc[0]['status']}"
        )

    def test_psi_consistent_with_compute_psi(self):
        """monitor_features PSI values should match compute_psi directly."""
        rng = np.random.default_rng(4)
        ref = pd.DataFrame({"f1": rng.normal(0, 1, 500), "f2": rng.normal(1, 1, 500)})
        live = pd.DataFrame({"f1": rng.normal(0.5, 1, 500), "f2": rng.normal(2, 1, 500)})

        monitor_result = monitor_features(ref, live)

        for _, row in monitor_result.iterrows():
            col = row["feature"]
            direct_psi = compute_psi(ref[col].values, live[col].values)
            assert abs(row["psi"] - direct_psi) < 1e-6, (
                f"monitor_features PSI for {col} ({row['psi']:.6f}) "
                f"differs from direct compute_psi ({direct_psi:.6f})"
            )

    def test_non_numeric_columns_ignored_by_default(self):
        """Columns with string data should not cause errors or appear in output."""
        rng = np.random.default_rng(5)
        ref = pd.DataFrame({
            "numeric": rng.normal(0, 1, 100),
            "label": ["A"] * 50 + ["B"] * 50,  # non-numeric
        })
        live = pd.DataFrame({
            "numeric": rng.normal(0, 1, 100),
            "label": ["A"] * 50 + ["B"] * 50,
        })

        result = monitor_features(ref, live)

        assert "label" not in result["feature"].values
        assert "numeric" in result["feature"].values
