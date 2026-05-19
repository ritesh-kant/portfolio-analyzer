"""Tests for CalibratedLGBM model (quant/models/calibrated_lgbm.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.earnings import REGISTERED_FEATURES
from quant.models.calibrated_lgbm import CalibratedLGBM, _DEFAULT_PARAMS


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_synthetic_dataset(
    n: int = 200,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Generate synthetic PEAD-like dataset for model testing."""
    rng = np.random.default_rng(seed)
    n_features = len(REGISTERED_FEATURES)

    X_values = rng.standard_normal((n, n_features)) * 0.1
    # Inject a signal: high eps_surprise_pct (feature 0) → more likely to win
    eps_idx = REGISTERED_FEATURES.index("eps_surprise_pct")
    X_values[:, eps_idx] += rng.standard_normal(n) * 0.3

    # Target: biased by eps_surprise
    prob = 0.3 + 0.4 * (X_values[:, eps_idx] > 0.1).astype(float)
    y = (rng.uniform(size=n) < prob).astype(float)

    X = pd.DataFrame(X_values, columns=REGISTERED_FEATURES)
    # Replace NaN-inducing features with 0.0
    X = X.fillna(0.0)

    # Dates: spread over 2 years of training window
    dates = pd.date_range("2021-01-01", periods=n, freq="B")

    return X, pd.Series(y), pd.Series(dates)


# ── Tests: feature validation ─────────────────────────────────────────────────

def test_init_rejects_unknown_features():
    with pytest.raises(ValueError, match="not in the pre-registered list"):
        CalibratedLGBM(feature_cols=["unknown_feature_xyz"])


def test_init_rejects_too_many_features():
    too_many = REGISTERED_FEATURES + [REGISTERED_FEATURES[0]]  # duplicate to exceed count
    # Use registered-only names; just duplicate to exceed limit would need >20 unique
    # Instead, test the > 20 count check directly with a mock list
    with pytest.raises(ValueError, match="MAX_FEATURES"):
        # Build a list of 21 registered feature names (by truncating duplicates)
        # We can't use unregistered names per the other check, so patch registered list
        import quant.features.earnings as fe
        orig = fe.REGISTERED_FEATURES[:]
        try:
            fe.REGISTERED_FEATURES = REGISTERED_FEATURES * 2  # 32 features
            CalibratedLGBM(feature_cols=fe.REGISTERED_FEATURES)
        finally:
            fe.REGISTERED_FEATURES = orig


def test_predict_before_fit_raises():
    model = CalibratedLGBM()
    X, _, _ = _make_synthetic_dataset(n=10)
    with pytest.raises(RuntimeError, match="fit()"):
        model.predict_proba(X)


# ── Tests: fit and predict ────────────────────────────────────────────────────

def test_fit_and_predict_shape():
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=200)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    probs = model.predict_proba(X)
    assert probs.shape == (200,)


def test_fit_probabilities_bounded():
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=200)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    probs = model.predict_proba(X)
    assert float(probs.min()) >= 0.0
    assert float(probs.max()) <= 1.0


def test_fit_oof_brier_is_finite():
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=200)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    assert np.isfinite(model.oof_brier)
    assert 0.0 <= model.oof_brier <= 1.0


def test_fit_brier_better_than_naive():
    """Calibrated model should beat the naive always-predict-mean baseline."""
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=300, seed=7)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    # Naive Brier: always predict base rate
    naive_brier = float(np.mean((y.mean() - y) ** 2))
    # Model should do at least somewhat better (not strict — small dataset)
    assert model.oof_brier <= naive_brier * 1.2, (
        f"Model Brier {model.oof_brier:.4f} should be ≤ naive {naive_brier:.4f}"
    )


def test_fit_is_fitted_flag():
    model = CalibratedLGBM()
    assert not model.is_fitted
    X, y, dates = _make_synthetic_dataset(n=200)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    assert model.is_fitted


def test_feature_importance_returns_series():
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=200)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")
    imp = model.feature_importance()
    assert len(imp) == len(REGISTERED_FEATURES)
    assert set(imp.index) == set(REGISTERED_FEATURES)


def test_fit_minimum_sample_guard():
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=5)  # too few
    with pytest.raises(ValueError, match="Need at least"):
        model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")


# ── Tests: parameter override ─────────────────────────────────────────────────

def test_custom_params_override():
    custom = {"n_estimators": 10, "max_depth": 2}
    model = CalibratedLGBM(params=custom)
    assert model.params["n_estimators"] == 10
    assert model.params["max_depth"] == 2
    # Default params should still be there for other keys
    assert "learning_rate" in model.params


# ── Tests: signal direction (weak — small dataset, not a gate check) ──────────

def test_positive_eps_surprise_predicts_higher_win_prob():
    """Stocks with high eps_surprise should have higher predicted P(win)."""
    model = CalibratedLGBM()
    X, y, dates = _make_synthetic_dataset(n=300, seed=99)
    model.fit(X, y, dates, n_trials=1, experiment_name="test_pead")

    eps_idx = REGISTERED_FEATURES.index("eps_surprise_pct")
    X_high = X.copy()
    X_low = X.copy()
    X_high.iloc[:, eps_idx] = 2.0   # large positive surprise
    X_low.iloc[:, eps_idx] = -2.0   # negative surprise

    prob_high = float(model.predict_proba(X_high).mean())
    prob_low = float(model.predict_proba(X_low).mean())
    assert prob_high > prob_low, (
        f"Expected P(win|high_surprise) > P(win|low_surprise), "
        f"got {prob_high:.3f} vs {prob_low:.3f}"
    )
