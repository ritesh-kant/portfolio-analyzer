"""Calibrated LightGBM base model. To be implemented Month 3 with Strategy A.

Structure:
    1. LightGBM classifier predicts P(target) — binary win/lose outcome
    2. LightGBM regressor predicts E[R-multiple | win] — expected payoff size
    3. Isotonic regression on validation fold for probability calibration
    4. Brier score + reliability plot logged to MLflow

Anti-overfitting discipline (plan §3.3):
    - Hyperparameters chosen ONCE via grid search inside the purged k-fold
      validator; never tuned after seeing dev-set residuals
    - Feature additions require a new pre-registered hypothesis
    - Re-fit cadence: monthly (not on demand)
"""

from __future__ import annotations


class CalibratedLGBM:
    """Wraps a LightGBM model + isotonic calibrator + experiment tracking."""

    def __init__(self) -> None:
        raise NotImplementedError("CalibratedLGBM — Month 3")
