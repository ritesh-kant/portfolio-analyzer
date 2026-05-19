"""Point-in-time join primitive. Month 2.

The asof_left_join(features, events) function below is the single most
important piece of infrastructure in this rebuild.

Semantics:
    For each row in `events`:
        find the most recent row in `features` where
            features.symbol == events.symbol
            AND features.data_available_at <= events.inference_date
        attach those feature columns to the event row.

Why this matters:
    The previous system silently consumed today's earnings calendar when
    backtesting 2021 trades — lookahead bias. Every quant retail system
    has this bug at least once. The PIT join makes it structurally
    impossible: data without an `as_of_timestamp` cannot pass the join.
"""

from __future__ import annotations


def asof_left_join(features, events, by: str = "symbol"):
    raise NotImplementedError("asof_left_join — Month 2 (use pandas.merge_asof under the hood)")
