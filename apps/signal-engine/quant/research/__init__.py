"""Research platform — validation harness + experiment registry interface.

The most critical layer for not repeating the previous system's failure.
Every backtest run is logged with config hash, code SHA, data snapshot ID,
random seed, and metrics including Deflated Sharpe with trial-count.

Modules (per plan §11):
    purged_kfold.py  — Lopez de Prado's purged k-fold + embargo
    dsr.py           — Deflated Sharpe Ratio (multiple-testing-adjusted)
    holdout_lock.py  — file lock preventing accidental reads of the
                       2024-07-01+ hold-out partition
    (later: capacity.py, anti_strategy.py, cost_stress.py)
"""
