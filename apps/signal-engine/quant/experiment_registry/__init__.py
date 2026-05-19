"""Experiment registry — thin MLflow wrapper.

NOTE: Plan §11 lists this under `packages/experiment-registry/`. Placed
here under quant/ for the same reason as feature_store (TS-only packages/
convention; only one Python app needs this currently). Promote later if
another Python app shows up.

Wraps MLflow with project-specific conventions:
    - SQLite backend (free, sufficient for solo founder)
    - Every run tagged with: code SHA, config hash, data snapshot ID,
      random seed, train/dev/holdout split definitions
    - Trial-count tracked across all runs (needed by DSR — peeking still
      costs you even if the experiment "failed")
    - `is_final=true` immutable tag on hold-out runs; non-final runs to
      the hold-out partition raise

If a backtest run isn't logged here, it didn't happen (plan §8.1).
The previous system's full_run_v1..v7 directories were the antipattern
this exists to prevent.
"""
