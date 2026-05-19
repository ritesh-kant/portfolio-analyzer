"""Hold-out lock — prevents accidental access to the 2024-07-01+ partition.

The most important single piece of infrastructure in this rebuild
(plan §3.1 + §14). The hold-out is sacred; every retail quant who has
failed has failed because they peeked.

Mechanism:
    1. The hold-out parquet partition has its read access gated by
       a file lock at .holdout-lock
    2. Reading requires an environment variable QUANT_HOLDOUT_UNLOCK=<strategy>
       AND a hash of a pre-registered hypothesis file with `final=true`
    3. Each unlock is logged to MLflow with the strategy name + git SHA
    4. After a strategy fails on hold-out, that strategy is dead — no
       second look at the same hold-out partition for that strategy

If a function in quant/ ever needs to read 2024-07-01+ data and isn't
called through holdout_lock.read_holdout(strategy_name), the load fails.
"""

from __future__ import annotations


def read_holdout(strategy_name: str, hypothesis_hash: str):
    raise NotImplementedError("holdout_lock.read_holdout — Month 2")


def assert_no_holdout_access(business_date: str) -> None:
    """Raise if business_date is in the hold-out partition. Default-deny."""
    raise NotImplementedError("assert_no_holdout_access — Month 2")
