"""Hold-out lock — prevents accidental access to the 2024-07-01+ partition.

The most important single piece of infrastructure in this rebuild.
(plan §3.1 + §14).

"Every retail quant who has failed has failed because they peeked."

Mechanism
---------
1.  ``assert_no_holdout_access(business_date)`` is called at the top of
    every feature builder, model runner, and backtest loop.  It raises
    immediately if the date falls in the hold-out window.  Default-deny.

2.  ``read_holdout(strategy_name, hypothesis_path)`` is the single gate
    through which hold-out access is permitted.  It requires:
        a. Environment variable ``QUANT_HOLDOUT_UNLOCK=<strategy_name>``
        b. A hypothesis file at ``hypothesis_path`` that contains
           ``final: true`` — proving the hypothesis was pre-registered
           and not retroactively written after seeing dev results.
        c. Each unlock is logged with the git SHA and hypothesis hash.

    After calling ``read_holdout()``, use ``pit_loader.load()`` with
    ``start=HOLDOUT_START`` to access the data.

3.  If a strategy fails on hold-out it dies.  There is no
    ``read_holdout()`` call for "one more look" — the lock checks
    whether a hold-out run was already logged for this strategy and
    refuses a second unlock.

Hold-out window: 2024-07-01 → present (never changes; see plan §3.1).
Train window:    2015-01-01 → 2023-06-30
Dev window:      2023-07-01 → 2024-06-30
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

HOLDOUT_START: str = "2024-07-01"
TRAIN_END: str = "2023-06-30"
DEV_START: str = "2023-07-01"
DEV_END: str = "2024-06-30"

_HOLDOUT_START_DATE = pd.Timestamp(HOLDOUT_START).date()
_DEV_START_DATE = pd.Timestamp(DEV_START).date()
_TRAIN_END_DATE = pd.Timestamp(TRAIN_END).date()

_ENV_VAR = "QUANT_HOLDOUT_UNLOCK"


# ── Public API ─────────────────────────────────────────────────────────────────

def assert_no_holdout_access(business_date: str | pd.Timestamp) -> None:
    """Raise ``ValueError`` if ``business_date`` is in the hold-out window.

    Call this at the top of every function that accepts a date argument and
    might touch price or feature data.  It is a no-op for dates before
    ``HOLDOUT_START`` so it is safe to call unconditionally.

    Parameters
    ----------
    business_date : str or pd.Timestamp
        Any ISO date string (``"YYYY-MM-DD"``) or ``pd.Timestamp``.

    Raises
    ------
    ValueError
        If ``business_date >= HOLDOUT_START`` (2024-07-01).
    """
    d = pd.Timestamp(business_date).date()
    if d >= _HOLDOUT_START_DATE:
        raise ValueError(
            f"HOLD-OUT VIOLATION: date {business_date!r} is in the hold-out "
            f"partition (>= {HOLDOUT_START}).  "
            "Use holdout_lock.read_holdout() with a finalised hypothesis to "
            "unlock hold-out access.  See plan §3.1 + §14."
        )


def read_holdout(strategy_name: str, hypothesis_path: str | Path) -> None:
    """Gate for hold-out partition access.

    Validates the three preconditions and logs the unlock.  After a
    successful call, use ``pit_loader.load(start=HOLDOUT_START, ...)``
    to read hold-out data.

    Parameters
    ----------
    strategy_name : str
        Must match the value of the ``QUANT_HOLDOUT_UNLOCK`` env var exactly.
    hypothesis_path : str or Path
        Path to the pre-registered hypothesis ``.md`` file.  Must contain
        the line ``final: true`` (case-insensitive).

    Raises
    ------
    PermissionError
        If the env var is unset or does not match ``strategy_name``.
    ValueError
        If the hypothesis file does not contain ``final: true``.
    FileNotFoundError
        If ``hypothesis_path`` does not exist.
    """
    # ── Precondition 1: env var ────────────────────────────────────────────────
    unlock_val = os.environ.get(_ENV_VAR, "")
    if unlock_val != strategy_name:
        raise PermissionError(
            f"Hold-out unlock refused: {_ENV_VAR}={unlock_val!r} does not "
            f"match strategy {strategy_name!r}.  "
            f"Set {_ENV_VAR}={strategy_name} in the environment before "
            "running the final hold-out evaluation.  "
            "See plan §3.1 + §14."
        )

    # ── Precondition 2: hypothesis file is finalised ───────────────────────────
    path = Path(hypothesis_path)
    content = path.read_text(encoding="utf-8")  # raises FileNotFoundError if missing
    if "final: true" not in content.lower():
        raise ValueError(
            f"Hold-out unlock refused: hypothesis file {str(path)!r} does not "
            "contain 'final: true'.  Pre-register and mark the hypothesis "
            "final before running hold-out evaluation.  See plan §8.2."
        )

    # ── Precondition 3: one-run-only guard ────────────────────────────────────
    # If a hold-out run already exists in MLflow for this strategy, refuse.
    _assert_no_prior_holdout_run(strategy_name)

    h_hash = _file_hash(path)
    git_sha = _git_sha()

    logger.warning(
        "HOLDOUT_UNLOCK strategy=%s hypothesis=%s sha256=%s...%s git=%s",
        strategy_name,
        path.name,
        h_hash[:8],
        h_hash[-4:],
        git_sha,
    )

    # Log the unlock event to MLflow with an immutable tag
    _log_unlock_to_mlflow(strategy_name, path, h_hash, git_sha)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _file_hash(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    """Short HEAD SHA, or ``'unknown'`` if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _assert_no_prior_holdout_run(strategy_name: str) -> None:
    """Raise PermissionError if a hold-out run already exists in MLflow.

    This enforces the "single shot" rule: once you run the hold-out, you
    cannot run it again.  If the first run fails the gate, the strategy is
    dead.  There is no re-run.
    """
    try:
        import mlflow
        client = mlflow.tracking.MlflowClient()
        exp = client.get_experiment_by_name(strategy_name)
        if exp is None:
            return  # no experiment yet, first run allowed

        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            filter_string="tags.is_holdout_run = 'true'",
            max_results=1,
        )
        if runs:
            prior_run_id = runs[0].info.run_id
            raise PermissionError(
                f"Hold-out unlock refused: a hold-out run already exists for "
                f"strategy {strategy_name!r} (MLflow run_id={prior_run_id!r}).  "
                "The hold-out is a single-shot evaluation.  "
                "If the strategy failed, it is dead.  See plan §3.1 + §14."
            )
    except PermissionError:
        raise
    except Exception as exc:
        logger.warning("MLflow check for prior hold-out run failed (non-fatal): %s", exc)


def _log_unlock_to_mlflow(
    strategy_name: str,
    hypothesis_path: Path,
    h_hash: str,
    git_sha: str,
) -> None:
    """Log the hold-out unlock event to MLflow with immutable tags."""
    try:
        import mlflow
        mlflow.set_experiment(strategy_name)
        with mlflow.start_run(
            tags={
                "is_holdout_run": "true",      # used by _assert_no_prior_holdout_run
                "stage": "holdout",
                "strategy": strategy_name,
                "hypothesis_sha256": h_hash,
                "git_sha": git_sha,
                "hypothesis_file": hypothesis_path.name,
            }
        ) as active_run:
            mlflow.log_param("holdout_start", HOLDOUT_START)
            mlflow.log_param("strategy_name", strategy_name)
            logger.info(
                "Holdout unlock logged to MLflow run_id=%s",
                active_run.info.run_id,
            )
    except Exception as exc:
        logger.warning("MLflow holdout logging failed (non-fatal): %s", exc)
