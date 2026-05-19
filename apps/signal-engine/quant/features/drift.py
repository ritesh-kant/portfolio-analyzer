"""Feature drift monitor — Population Stability Index (PSI).  Month 2.

PSI measures how much a feature's distribution has shifted between a
reference window (training) and a live window (production/paper trading).

PSI thresholds (plan §9.1)
--------------------------
    PSI < 0.10   — no significant shift (ok)
    0.10–0.25    — moderate shift (warn; watch the feature)
    0.25–0.50    — material shift (alert; trigger Bayesian P(strategy dead) update)
    > 0.50       — severe shift (freeze new entries on affected strategies)

Why PSI
-------
The 2022 "zero-trade" failure in the previous system was caused by feature
drift — the strategy's volatility-threshold features drifted out of their
training-time distribution, the gate was never triggered, and the system
silently sat in cash for an entire quarter with no alarm.  PSI is the
single mechanism that would have caught this in production.

Formula
-------
Bin edges are defined once on the reference (training) distribution.
The live distribution is then measured against those same edges.

    PSI = Σ_b  (live_pct_b − ref_pct_b) × ln(live_pct_b / ref_pct_b)

Bins with zero counts receive a small epsilon to avoid log(0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── PSI threshold constants (plan §9.1) ───────────────────────────────────────
PSI_OK = 0.10
PSI_WARN = 0.10
PSI_ALERT = 0.25
PSI_SEVERE = 0.50


def compute_psi(
    reference: list[float] | np.ndarray,
    live: list[float] | np.ndarray,
    bins: int = 10,
) -> float:
    """Population Stability Index between reference and live distributions.

    Parameters
    ----------
    reference : array-like of float
        Training/reference feature values (1-D).  Must have >= 2 samples.
    live : array-like of float
        Live/production feature values (1-D).  Must have >= 2 samples.
    bins : int
        Number of buckets.  Edges are percentile-based on ``reference``
        so each bucket contains roughly equal reference density.
        Default 10 (standard; use 20 for high-frequency features).

    Returns
    -------
    float
        PSI value >= 0.  Higher = more drift.

    Notes
    -----
    * Constant-value features (zero variance) return 0.0 safely.
    * ``live`` values outside the reference range land in the edge buckets;
      they are not dropped, so large distribution shifts are captured.
    """
    ref = np.asarray(reference, dtype=float)
    liv = np.asarray(live, dtype=float)

    ref = ref[np.isfinite(ref)]
    liv = liv[np.isfinite(liv)]

    if len(ref) < 2 or len(liv) < 2:
        return 0.0

    # Define percentile-based bin edges on the reference distribution
    percentiles = np.linspace(0, 100, bins + 1)
    edges = np.unique(np.percentile(ref, percentiles))

    if len(edges) < 2:
        # constant-value feature — no shift possible
        return 0.0

    # Extend edges to capture any live values outside the reference range
    edges[0] = min(edges[0], liv.min()) - 1e-10
    edges[-1] = max(edges[-1], liv.max()) + 1e-10

    def _pcts(arr: np.ndarray) -> np.ndarray:
        counts, _ = np.histogram(arr, bins=edges)
        counts = counts.astype(float)
        # Replace zeros with epsilon to avoid log(0)
        counts = np.where(counts == 0, 1e-6, counts)
        return counts / counts.sum()

    ref_pcts = _pcts(ref)
    liv_pcts = _pcts(liv)

    psi = float(np.sum((liv_pcts - ref_pcts) * np.log(liv_pcts / ref_pcts)))
    return max(0.0, psi)  # PSI is non-negative; clamp floating-point noise


def psi_status(psi: float) -> str:
    """Return drift-severity label for a PSI value.

    Returns
    -------
    str : one of ``"ok"`` | ``"warn"`` | ``"alert"`` | ``"severe"``
    """
    if psi >= PSI_SEVERE:
        return "severe"
    if psi >= PSI_ALERT:
        return "alert"
    if psi >= PSI_WARN:
        return "warn"
    return "ok"


def monitor_features(
    reference_df: pd.DataFrame,
    live_df: pd.DataFrame,
    feature_cols: list[str] | None = None,
    bins: int = 10,
) -> pd.DataFrame:
    """Compute PSI for every feature column.

    Parameters
    ----------
    reference_df : pd.DataFrame
        Training/reference feature DataFrame.
    live_df : pd.DataFrame
        Live/production feature DataFrame (same schema).
    feature_cols : list[str] or None
        Columns to monitor.  Defaults to all numeric columns present in
        both DataFrames.
    bins : int
        PSI bin count (default 10).

    Returns
    -------
    pd.DataFrame with columns: ``feature``, ``psi``, ``status``.
        Sorted by ``psi`` descending (worst drift first).
    """
    if feature_cols is None:
        numeric_ref = set(reference_df.select_dtypes(include=[np.number]).columns)
        numeric_liv = set(live_df.select_dtypes(include=[np.number]).columns)
        feature_cols = sorted(numeric_ref & numeric_liv)

    rows = []
    for col in feature_cols:
        ref_vals = reference_df[col].dropna().to_numpy(dtype=float)
        liv_vals = live_df[col].dropna().to_numpy(dtype=float)
        psi = compute_psi(ref_vals, liv_vals, bins=bins)
        rows.append({"feature": col, "psi": round(psi, 6), "status": psi_status(psi)})

    return (
        pd.DataFrame(rows)
        .sort_values("psi", ascending=False)
        .reset_index(drop=True)
    )
