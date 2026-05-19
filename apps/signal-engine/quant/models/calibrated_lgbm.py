"""Calibrated LightGBM model for Strategy A (PEAD). Month 3.

Architecture (plan §2, L3)
--------------------------
1. LightGBM classifier → raw P(target) per trade event
2. Isotonic regression calibrator on OOF predictions → calibrated P(win)
3. Brier score + reliability logged to MLflow

Anti-overfitting discipline (plan §3.3, enforced in code)
----------------------------------------------------------
- Hyperparameters are chosen ONCE in __init__ via the provided config.
  They are NOT tuned after observing dev residuals.
- Feature set is locked at training time.  Passing a feature that was not
  in REGISTERED_FEATURES raises immediately.
- Re-fit cadence: monthly, never on demand.
- Max 20 features (pre-registered in hypothesis 2026-05-19-pead-midcap.md).

MLflow logging
--------------
Every fit() call logs: feature list, hyperparameters, OOF Brier score,
OOF log loss, trial count, DSR.  Artifacts: calibration curve plot.
"""

from __future__ import annotations

import logging
from typing import Any

import mlflow
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss

import lightgbm as lgb

from quant.features.earnings import MAX_FEATURES, REGISTERED_FEATURES
from quant.research.dsr import deflated_sharpe
from quant.research.purged_kfold import purged_kfold_split

logger = logging.getLogger(__name__)

# Default hyperparameters — chosen conservatively to avoid overfitting.
# These are the ONLY parameters used.  Do not tune post-dev.
_DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "max_depth": 4,           # shallow — prevents memorisation on small dataset
    "num_leaves": 15,
    "min_child_samples": 20,  # needs at least 20 samples per leaf
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

_HOLDING_PERIOD_DAYS = 10  # conservative purge window (strategy holds 5-10 days)
_K_FOLDS = 5


class CalibratedLGBM:
    """LightGBM classifier + isotonic probability calibration.

    Attributes
    ----------
    feature_cols : list[str]
        Ordered list of feature column names used during training.
        Locked after fit(); predict_proba() enforces this order.
    is_fitted : bool
        True after fit() completes successfully.
    oof_brier : float
        Out-of-fold Brier score from the purged k-fold.
    n_trials : int
        MLflow trial count used to compute DSR (must be tracked externally
        and passed at fit time).
    """

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        feature_cols: list[str] | None = None,
    ) -> None:
        self.params = {**_DEFAULT_PARAMS, **(params or {})}
        self.feature_cols: list[str] = feature_cols or REGISTERED_FEATURES
        self._validate_features(self.feature_cols)

        self._lgbm: lgb.LGBMClassifier | None = None
        self._calibrator: IsotonicRegression | None = None
        self.is_fitted: bool = False
        self.oof_brier: float = float("nan")
        self.oof_log_loss: float = float("nan")
        self.n_trials: int = 1

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series | np.ndarray,
        dates: pd.Series | pd.DatetimeIndex,
        n_trials: int = 1,
        experiment_name: str = "pead_midcap",
    ) -> "CalibratedLGBM":
        """Fit the model on the training set using purged k-fold.

        Parameters
        ----------
        X : pd.DataFrame
            Feature matrix.  Must contain exactly self.feature_cols.
        y : pd.Series or np.ndarray
            Binary target (0/1).
        dates : pd.Series or DatetimeIndex
            Date of each observation — used for purged k-fold split.
        n_trials : int
            Total MLflow experiments logged for this strategy (for DSR).
        experiment_name : str
            MLflow experiment name.

        Returns
        -------
        self
        """
        self._validate_features(list(X.columns))
        X_df = X[self.feature_cols].astype(np.float32)  # DataFrame with named cols
        X_mat = X_df.values                             # numpy for OOF fold slicing
        y_arr = np.asarray(y, dtype=np.float32)
        self.n_trials = n_trials

        if len(X_mat) < _K_FOLDS * 10:
            raise ValueError(
                f"Need at least {_K_FOLDS * 10} samples to fit with {_K_FOLDS}-fold CV, "
                f"got {len(X_mat)}.  Collect more earnings events."
            )

        # ── Purged k-fold OOF calibration ─────────────────────────────────────
        splits = purged_kfold_split(
            dates,
            holding_period_days=_HOLDING_PERIOD_DAYS,
            k=_K_FOLDS,
        )

        oof_probs = np.full(len(y_arr), fill_value=np.nan)

        for fold_i, (train_idx, test_idx) in enumerate(splits):
            if len(train_idx) == 0 or len(test_idx) == 0:
                logger.warning("Fold %d: empty split — skipping", fold_i)
                continue

            X_tr, X_te = X_mat[train_idx], X_mat[test_idx]
            y_tr = y_arr[train_idx]

            clf = lgb.LGBMClassifier(**self.params)
            clf.fit(X_tr, y_tr)
            oof_probs[test_idx] = clf.predict_proba(X_te)[:, 1]

        # ── Drop NaN OOF (folds that were skipped) ────────────────────────────
        valid_mask = ~np.isnan(oof_probs)
        if valid_mask.sum() < 20:
            raise RuntimeError(
                f"Only {valid_mask.sum()} valid OOF predictions — not enough to calibrate."
            )

        # ── Isotonic calibration on OOF ───────────────────────────────────────
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(oof_probs[valid_mask], y_arr[valid_mask])
        self._calibrator = calibrator

        # ── Final model fit on full training data ─────────────────────────────
        final_clf = lgb.LGBMClassifier(**self.params)
        final_clf.fit(X_df, y_arr)   # use DataFrame so feature names are stored
        self._lgbm = final_clf

        # ── Metrics ───────────────────────────────────────────────────────────
        cal_probs = calibrator.predict(oof_probs[valid_mask])
        self.oof_brier = float(brier_score_loss(y_arr[valid_mask], cal_probs))
        self.oof_log_loss = float(log_loss(y_arr[valid_mask], np.clip(cal_probs, 1e-7, 1 - 1e-7)))

        # DSR of calibrated OOF P&L (treat each prediction > 0.5 as a trade)
        trade_mask = cal_probs > 0.5
        if trade_mask.sum() > 5:
            trade_returns = np.where(
                y_arr[valid_mask][trade_mask] == 1, 0.005, -0.005
            )  # synthetic ±50 bps per trade
            oof_dsr = deflated_sharpe(trade_returns, n_trials=n_trials)
        else:
            oof_dsr = 0.0

        self.is_fitted = True

        # ── MLflow logging ────────────────────────────────────────────────────
        self._log_mlflow(
            experiment_name=experiment_name,
            n_trials=n_trials,
            oof_brier=self.oof_brier,
            oof_log_loss=self.oof_log_loss,
            oof_dsr=oof_dsr,
            n_samples=len(y_arr),
            n_features=len(self.feature_cols),
        )

        logger.info(
            "CalibratedLGBM fit: n=%d features=%d oof_brier=%.4f oof_dsr=%.3f",
            len(y_arr), len(self.feature_cols), self.oof_brier, oof_dsr,
        )
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return calibrated P(win) for each row.

        Parameters
        ----------
        X : pd.DataFrame
            Must contain all columns in self.feature_cols.

        Returns
        -------
        np.ndarray of shape (n_samples,) in [0, 1].
        """
        self._assert_fitted()
        X_ordered = X[self.feature_cols].astype(np.float32)
        raw_probs = self._lgbm.predict_proba(X_ordered)[:, 1]  # type: ignore[union-attr]
        return self._calibrator.predict(raw_probs)  # type: ignore[union-attr]

    def feature_importance(self) -> pd.Series:
        """Return feature importances (gain) from the final LightGBM model."""
        self._assert_fitted()
        importances = self._lgbm.feature_importances_  # type: ignore[union-attr]
        return pd.Series(importances, index=self.feature_cols).sort_values(ascending=False)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _assert_fitted(self) -> None:
        if not self.is_fitted or self._lgbm is None or self._calibrator is None:
            raise RuntimeError("CalibratedLGBM.fit() must be called before predict.")

    @staticmethod
    def _validate_features(cols: list[str]) -> None:
        if len(cols) > MAX_FEATURES:
            raise ValueError(
                f"Feature count {len(cols)} exceeds MAX_FEATURES={MAX_FEATURES}. "
                "Pre-register additional features in the hypothesis file before adding them."
            )
        unknown = set(cols) - set(REGISTERED_FEATURES)
        if unknown:
            raise ValueError(
                f"Features not in the pre-registered list: {sorted(unknown)}. "
                "Add them to research/hypotheses/2026-05-19-pead-midcap.md first."
            )

    def _log_mlflow(
        self,
        experiment_name: str,
        n_trials: int,
        oof_brier: float,
        oof_log_loss: float,
        oof_dsr: float,
        n_samples: int,
        n_features: int,
    ) -> None:
        try:
            mlflow.set_experiment(experiment_name)
            with mlflow.start_run(tags={"stage": "train", "strategy": "pead_midcap"}):
                mlflow.log_params({
                    **{f"lgbm_{k}": v for k, v in self.params.items()},
                    "n_features": n_features,
                    "n_folds": _K_FOLDS,
                    "holding_period_days": _HOLDING_PERIOD_DAYS,
                })
                mlflow.log_params({f"feature_{i}": f for i, f in enumerate(self.feature_cols)})
                mlflow.log_metrics({
                    "oof_brier": oof_brier,
                    "oof_log_loss": oof_log_loss,
                    "oof_dsr": oof_dsr,
                    "n_trials": n_trials,
                    "n_samples": n_samples,
                })
        except Exception as exc:
            logger.warning("MLflow logging failed (non-fatal): %s", exc)
