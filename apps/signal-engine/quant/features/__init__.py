"""L2 feature store — point-in-time feature builders + drift monitor.

Every feature carries data_available_at = max(input as_of_timestamps).
Downstream models join with features.data_available_at <= inference_date.

Modules (per plan §11):
    builder.py — feature builders (momentum residuals, surprise features,
                 breadth, regime labels). Reuses backtest/indicators.py
                 vectorized math as building blocks.
    drift.py   — PSI (Population Stability Index) monitor. PSI > 0.25 = warn;
                 > 0.5 = freeze trading on the affected strategy.
"""
