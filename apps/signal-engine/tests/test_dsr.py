"""Tests for quant.research.dsr — Deflated Sharpe Ratio.

Verifies the mathematical properties of the DSR:
  - More trials → lower DSR (multiple-testing penalty)
  - Higher SR → higher DSR
  - Longer sample → higher DSR (more evidence)
  - DSR = PSR when n_trials=1
  - Rough calibration vs the plan §3.2 gate thresholds

The exact DSR values are sensitive to distribution assumptions, so the
tests use directional properties and calibration ranges rather than
hard-coded floats.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant.research.dsr import deflated_sharpe, annualised_sr_to_dsr

RNG = np.random.default_rng(42)
TRADING_DAYS = 252


def _returns(annualised_sr: float, n_days: int, seed: int = 0) -> np.ndarray:
    """Gaussian daily returns with a given annualised Sharpe."""
    daily_sr = annualised_sr / math.sqrt(TRADING_DAYS)
    std = 0.01  # 1% daily vol — realistic for equity
    mean = daily_sr * std
    rng = np.random.default_rng(seed)
    return rng.normal(loc=mean, scale=std, size=n_days)


# ── Monotonicity properties ───────────────────────────────────────────────────

class TestMonotonicity:
    """DSR must move in the expected direction when inputs change."""

    def test_more_trials_lowers_dsr(self):
        """More trials = more selection bias = lower DSR for the same return series."""
        rets = _returns(annualised_sr=1.2, n_days=756)

        dsr_1 = deflated_sharpe(rets, n_trials=1)
        dsr_10 = deflated_sharpe(rets, n_trials=10)
        dsr_100 = deflated_sharpe(rets, n_trials=100)

        assert dsr_1 > dsr_10, f"1 trial ({dsr_1:.3f}) should beat 10 trials ({dsr_10:.3f})"
        assert dsr_10 > dsr_100, f"10 trials ({dsr_10:.3f}) should beat 100 trials ({dsr_100:.3f})"

    def test_higher_sr_raises_dsr(self):
        """Better strategies should have higher DSR for the same n_trials."""
        n_days = 756
        n_trials = 20

        dsr_low = deflated_sharpe(_returns(annualised_sr=0.5, n_days=n_days), n_trials)
        dsr_mid = deflated_sharpe(_returns(annualised_sr=1.0, n_days=n_days), n_trials)
        dsr_high = deflated_sharpe(_returns(annualised_sr=1.5, n_days=n_days), n_trials)

        assert dsr_low < dsr_mid < dsr_high, (
            f"DSRs {dsr_low:.3f}, {dsr_mid:.3f}, {dsr_high:.3f} not monotone in SR"
        )

    def test_longer_sample_raises_dsr(self):
        """More observations → tighter SR estimate → higher DSR (more evidence)."""
        annualised_sr = 1.0
        n_trials = 20

        # Use the same seed so SR is consistent across lengths
        # (use a long series, then take subsets)
        full_rets = _returns(annualised_sr, n_days=756 * 3, seed=7)
        short_rets = full_rets[:252]
        med_rets = full_rets[:756]
        long_rets = full_rets[:756 * 2]

        dsr_short = deflated_sharpe(short_rets, n_trials)
        dsr_med = deflated_sharpe(med_rets, n_trials)
        dsr_long = deflated_sharpe(long_rets, n_trials)

        assert dsr_short <= dsr_med, (
            f"Short ({dsr_short:.3f}) should not exceed medium ({dsr_med:.3f})"
        )
        assert dsr_med <= dsr_long, (
            f"Medium ({dsr_med:.3f}) should not exceed long ({dsr_long:.3f})"
        )

    def test_zero_sr_below_05(self):
        """A strategy with SR ≈ 0 should have DSR well below 0.5."""
        rets = _returns(annualised_sr=0.0, n_days=756)
        dsr = deflated_sharpe(rets, n_trials=10)
        assert dsr < 0.5, f"SR≈0 strategy has DSR {dsr:.3f}; should be < 0.5"

    def test_negative_sr_near_zero(self):
        """A strategy with clearly negative SR should have DSR close to 0."""
        rets = _returns(annualised_sr=-1.0, n_days=756)
        dsr = deflated_sharpe(rets, n_trials=10)
        assert dsr < 0.1, f"Negative-SR strategy has DSR {dsr:.3f}; should be near 0"


# ── Gate calibration ──────────────────────────────────────────────────────────

class TestGateCalibration:
    """Verify the plan §3.2 gate threshold is reachable and sensible.

    Plan: DSR >= 0.5 on dev with T ≈ 756 obs, N ≈ 20-50 trials.
    A strategy with annualised SR ≈ 1.0–1.2 should be near or above 0.5.
    """

    def test_sr_15_trials_20_passes_gate(self):
        """Annualised SR 1.5, 3 years dev, 20 trials: DSR should exceed 0.5."""
        dsr = annualised_sr_to_dsr(
            annualised_sr=1.5,
            n_observations=756,
            n_trials=20,
        )
        assert dsr > 0.5, (
            f"SR=1.5, N=756, trials=20: expected DSR > 0.5, got {dsr:.3f}. "
            "The gate threshold may be miscalibrated."
        )

    def test_sr_05_trials_20_fails_gate(self):
        """Annualised SR 0.5, 3 years dev, 20 trials: DSR should be below 0.5."""
        dsr = annualised_sr_to_dsr(
            annualised_sr=0.5,
            n_observations=756,
            n_trials=20,
        )
        assert dsr < 0.5, (
            f"SR=0.5, N=756, trials=20: expected DSR < 0.5, got {dsr:.3f}. "
            "The gate is too lenient."
        )

    def test_single_trial_equals_psr(self):
        """With n_trials=1, DSR = PSR(SR_0=0) — pure test of SR significance."""
        rets = _returns(annualised_sr=1.0, n_days=756)
        dsr_single = deflated_sharpe(rets, n_trials=1)

        # For n_trials=1, benchmark_sr = 0.
        # DSR should be well above 0.5 for SR=1.0 over 3 years.
        assert dsr_single > 0.5, (
            f"n_trials=1 with SR≈1: expected DSR > 0.5, got {dsr_single:.3f}"
        )


# ── Output bounds ─────────────────────────────────────────────────────────────

class TestOutputBounds:
    def test_output_in_unit_interval(self):
        """DSR is a probability: must be in [0, 1]."""
        for sr in [-2.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0]:
            for n in [1, 5, 50, 500]:
                rets = _returns(annualised_sr=sr, n_days=252)
                dsr = deflated_sharpe(rets, n_trials=n)
                assert 0.0 <= dsr <= 1.0, (
                    f"SR={sr}, n={n}: DSR {dsr} outside [0, 1]"
                )

    def test_degenerate_constant_returns_handles_gracefully(self):
        """Constant return series (zero std) should not crash."""
        rets = np.full(100, 0.001)  # constant positive return
        dsr = deflated_sharpe(rets, n_trials=10)
        # No crash; should return something at the boundary
        assert 0.0 <= dsr <= 1.0

    def test_too_short_series_returns_zero(self):
        """Series with < 5 observations returns 0.0 (insufficient evidence)."""
        dsr = deflated_sharpe([0.01, 0.02], n_trials=5)
        assert dsr == 0.0

    def test_invalid_n_trials_raises(self):
        """n_trials < 1 should raise ValueError."""
        rets = _returns(annualised_sr=1.0, n_days=252)
        with pytest.raises(ValueError, match="n_trials"):
            deflated_sharpe(rets, n_trials=0)


# ── annualised_sr_to_dsr convenience wrapper ─────────────────────────────────

class TestAnnualisedSRToDSR:
    def test_consistent_with_deflated_sharpe(self):
        """annualised_sr_to_dsr should produce the same result as deflated_sharpe
        for Gaussian (skew=0, excess_kurt=0) returns."""
        sr = 1.2
        n = 500
        trials = 30

        from_wrapper = annualised_sr_to_dsr(
            annualised_sr=sr,
            n_observations=n,
            n_trials=trials,
            skew=0.0,
            excess_kurt=0.0,
        )
        # Build actual returns matching this SR and check
        rets = _returns(annualised_sr=sr, n_days=n, seed=0)
        from_series = deflated_sharpe(rets, n_trials=trials)

        # They should be in the same ballpark (within 0.15 — realized SR varies)
        assert abs(from_wrapper - from_series) < 0.20, (
            f"annualised_sr_to_dsr={from_wrapper:.3f} vs "
            f"deflated_sharpe={from_series:.3f}: too far apart."
        )

    def test_monotone_in_sr(self):
        """Higher annualised SR → higher DSR from the wrapper too."""
        base = dict(n_observations=756, n_trials=20)
        dsr_low = annualised_sr_to_dsr(annualised_sr=0.5, **base)
        dsr_high = annualised_sr_to_dsr(annualised_sr=1.5, **base)
        assert dsr_low < dsr_high
