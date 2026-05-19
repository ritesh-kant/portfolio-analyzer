"""MLflow thin wrapper. Month 1 (setup) + Month 2 (full integration).

API (to be implemented):
    start_run(strategy: str, split: str, hypothesis_hash: str) -> RunContext
        - strategy: e.g. "pead_midcap"
        - split: "train" | "dev" | "holdout"
        - hypothesis_hash: SHA of the pre-registered hypothesis file
        Returns a context manager that writes config hash + git SHA + seed.

    log_metrics(run_id: str, metrics: dict) -> None
    log_artifact(run_id: str, path: str) -> None

    is_final_run_for(strategy: str) -> bool
        - True iff a run with split="holdout" and is_final=true already exists
        for this strategy. Used by holdout_lock to enforce single-shot rule.

    trial_count(strategy: str, split: str = "dev") -> int
        - Returns the number of runs ever logged for (strategy, split).
        Consumed by DSR to deflate Sharpe by multiple-testing bias.
"""

from __future__ import annotations


def start_run(strategy: str, split: str, hypothesis_hash: str):
    raise NotImplementedError("start_run — Month 1 setup")
