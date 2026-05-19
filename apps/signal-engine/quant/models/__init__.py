"""L3 signal models — one calibrated probabilistic model per strategy.

Models output (P(target), P(stop), E[hold_days]) rather than a single
confidence score. Calibration (isotonic) is mandatory — uncalibrated
gradient-boosted probabilities are unsuitable for Kelly sizing.

Modules (per plan §11):
    calibrated_lgbm.py — base class wrapping LightGBM + isotonic + MLflow logging
"""
