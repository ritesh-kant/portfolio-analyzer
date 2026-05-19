"""Point-in-time join primitive.  Month 2.

``asof_left_join(features, events)`` is the single piece of infrastructure
that makes lookahead bias structurally impossible:

    For each row in `events`:
        find the most recent row in `features` where
            features[by] == events[by]
            AND features[feature_time_col] <= events[event_time_col]
        attach those feature columns to the event row.

Why this matters
----------------
The previous system silently consumed today's earnings calendar when
backtesting 2021 trades — lookahead bias on every single prediction.
With the PIT join:
  * Any feature row lacking an ``as_of_timestamp`` cannot pass the join.
  * Any feature row timestamped after ``inference_date`` cannot pass the join.
  * Downstream code never even sees the future rows; they don't exist in
    the result set.

This eliminates an entire class of phantom-alpha bugs at the infrastructure
level rather than relying on developer vigilance.

See ~/.claude/plans/based-on-the-full-harmonic-gosling.md §4.3.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def asof_left_join(
    features: pd.DataFrame,
    events: pd.DataFrame,
    *,
    by: str = "symbol",
    feature_time_col: str = "data_available_at",
    event_time_col: str = "inference_date",
) -> pd.DataFrame:
    """Left-join `events` to the most recent PIT-valid `features` row.

    For every event row the function finds the latest feature row satisfying:
        ``features[by] == events[by]``
        AND ``features[feature_time_col] <= events[event_time_col]``

    Event rows with no qualifying feature row receive ``NaN`` for all
    feature columns — never a silently wrong value.

    Parameters
    ----------
    features : pd.DataFrame
        Must contain ``[by, feature_time_col, ...feature_cols...]``.
        ``feature_time_col`` must be datetime-castable.
    events : pd.DataFrame
        Must contain ``[by, event_time_col, ...event_cols...]``.
        ``event_time_col`` must be datetime-castable.
    by : str
        Join key column name (default ``"symbol"``).
    feature_time_col : str
        Timestamp column in ``features`` (default ``"data_available_at"``).
    event_time_col : str
        Inference timestamp column in ``events`` (default ``"inference_date"``).

    Returns
    -------
    pd.DataFrame
        All event columns plus feature columns (``NaN`` where no PIT match).
        Row order matches ``events`` order.  ``feature_time_col`` is dropped
        from the result if it was not originally in ``events``.
    """
    if features.empty or events.empty:
        feature_cols = [
            c for c in features.columns if c not in (by, feature_time_col)
        ]
        result = events.copy()
        for col in feature_cols:
            result[col] = np.nan
        return result

    features = features.copy()
    events = events.copy()

    features[feature_time_col] = pd.to_datetime(features[feature_time_col])
    events[event_time_col] = pd.to_datetime(events[event_time_col])

    feature_cols = [c for c in features.columns if c not in (by, feature_time_col)]
    ft_col_in_events = feature_time_col in events.columns

    # preserve original row order for the final output
    events["_row_order"] = np.arange(len(events))

    result_frames: list[pd.DataFrame] = []

    for sym, ev_group in events.groupby(by, sort=False):
        feat_group = features[features[by] == sym].sort_values(feature_time_col)

        if feat_group.empty:
            ev_copy = ev_group.copy()
            for col in feature_cols:
                ev_copy[col] = np.nan
            result_frames.append(ev_copy)
            continue

        merged = pd.merge_asof(
            ev_group.sort_values(event_time_col),
            feat_group[[feature_time_col] + feature_cols].sort_values(feature_time_col),
            left_on=event_time_col,
            right_on=feature_time_col,
            direction="backward",  # most recent feature row <= inference_date
        )
        result_frames.append(merged)

    if not result_frames:
        result = events.copy()
        for col in feature_cols:
            result[col] = np.nan
    else:
        result = pd.concat(result_frames, ignore_index=True)

    # restore original event row order
    result = result.sort_values("_row_order").drop(columns=["_row_order"])

    # drop the feature timestamp column unless it was already in events
    if not ft_col_in_events and feature_time_col in result.columns:
        result = result.drop(columns=[feature_time_col])

    return result.reset_index(drop=True)
