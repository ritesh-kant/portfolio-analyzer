"""recalibrate — update order_agent.py Kelly table from backtest / paper trade results.

The production _WIN_PROB_TABLE in order_agent.py (lines 47-53) contains conservative
hand-picked win probabilities until empirical data accumulates.

This script replaces those numbers with measured win rates, binned by confidence bucket.

Requirements before running:
    • At least 30 closed trades per confidence bucket (see MIN_TRADES_PER_BUCKET).
      Below that threshold the bucket keeps its current conservative default.
    • The trade data can come from either:
        (a) backtest results CSVs  — run with --source backtest --trades path/to/trades.csv
        (b) live paper trading DB  — run with --source paper (reads MongoDB paper_orders)

Safety guardrails:
    • New win probabilities are capped at [0.45, 0.72] to prevent Kelly from
      over-sizing even when one bucket has a lucky run.
    • The script prints a before/after diff and requires --confirm to write.
    • A backup of order_agent.py is written before any modification.

Usage:
    # From backtest CSV results
    python -m backtest.recalibrate --source backtest --trades backtest/results/<run>/trades.csv

    # From live paper trading (requires MONGODB_URI set)
    python -m backtest.recalibrate --source paper

    # Preview only (no file write)
    python -m backtest.recalibrate --source backtest --trades path/to/trades.csv --dry-run
"""

from __future__ import annotations

import argparse
import ast
import csv
import logging
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Minimum number of closed trades required in a bucket before we replace the default.
# Lowered from 30 → 15: the 5-bucket × 30-trade requirement never fired on real backtest
# data (1,200 trades spread unevenly). WIN_PROB_MIN/MAX bounds protect against bad samples.
MIN_TRADES_PER_BUCKET = 15

# Win probability bounds — prevents over-sizing on lucky samples.
WIN_PROB_MIN = 0.45
WIN_PROB_MAX = 0.72

# Confidence buckets (same structure as _WIN_PROB_TABLE in order_agent.py)
# Each tuple: (min_confidence_inclusive, bucket_label)
CONFIDENCE_BUCKETS: list[tuple[float, str]] = [
    (80.0, "≥80"),
    (75.0, "75–79"),
    (70.0, "70–74"),
    (65.0, "65–69"),
    (60.0, "60–64"),
]

# Path to the production file we modify
_ORDER_AGENT_PATH = (
    Path(__file__).parent.parent
    / "src" / "pipeline" / "agents" / "order_agent.py"
)

# The exact line pattern to find and replace in order_agent.py
_WIN_PROB_TABLE_PATTERN = re.compile(
    r"(_WIN_PROB_TABLE: list\[tuple\[float, float\]\] = \[)(.*?)(\])",
    re.DOTALL,
)


# ── Bucket assignment ──────────────────────────────────────────────────────────

def _assign_bucket(confidence: float) -> str | None:
    """Return the bucket label for a given confidence value, or None if < 60."""
    for min_conf, label in CONFIDENCE_BUCKETS:
        if confidence >= min_conf:
            return label
    return None


# ── Load trade data ────────────────────────────────────────────────────────────

def load_trades_from_csv(trades_csv: Path) -> list[dict[str, Any]]:
    """Load closed trades from a backtest trades.csv file."""
    trades = []
    with open(trades_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "confidence":  float(row["confidence"]),
                "was_correct": row["was_correct"].lower() in ("true", "1", "yes"),
                "exit_reason": row.get("exit_reason", ""),
            })
    logger.info("recalibrate loaded %d trades from %s", len(trades), trades_csv)
    return trades


async def load_trades_from_paper_db() -> list[dict[str, Any]]:
    """Load closed paper trades from MongoDB (requires MONGODB_URI env var)."""
    try:
        from src.db.client import get_db
        from src.db.repositories.paper_orders import PaperOrdersRepository
    except ImportError as e:
        raise RuntimeError(
            "Cannot import DB modules. Make sure you run from the signal-engine root "
            "and MONGODB_URI is set."
        ) from e

    db = get_db()
    repo = PaperOrdersRepository(db)
    cursor = repo._col.find(
        {"status": "CLOSED", "was_correct": {"$exists": True}},
        projection={"confidence": 1, "was_correct": 1, "exit_reason": 1},
    )
    trades = []
    async for doc in cursor:
        trades.append({
            "confidence":  float(doc.get("confidence", 0)),
            "was_correct": bool(doc.get("was_correct", False)),
            "exit_reason": doc.get("exit_reason", ""),
        })
    logger.info("recalibrate loaded %d paper trades from MongoDB", len(trades))
    return trades


# ── Compute new win probabilities ──────────────────────────────────────────────

def compute_new_win_probs(
    trades: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Bin trades by confidence bucket and compute empirical win rates.

    Returns:
        {bucket_label: {
            "win_rate":    float,
            "trade_count": int,
            "wins":        int,
            "sufficient":  bool,   # True if trade_count >= MIN_TRADES_PER_BUCKET
        }}
    """
    bucket_trades: dict[str, list[bool]] = defaultdict(list)

    # Forced closures are not organic signal outcomes — exclude from calibration.
    # FOLD_END = backtest walk-forward boundary; max_age = paper trading timeout.
    _FORCED_EXITS = {"FOLD_END", "max_age"}

    for t in trades:
        if t.get("exit_reason") in _FORCED_EXITS:
            continue
        bucket = _assign_bucket(t["confidence"])
        if bucket is not None:
            bucket_trades[bucket].append(t["was_correct"])

    results: dict[str, dict[str, Any]] = {}
    for _, label in CONFIDENCE_BUCKETS:
        outcomes = bucket_trades.get(label, [])
        count = len(outcomes)
        wins  = sum(outcomes)
        raw_rate = wins / count if count > 0 else 0.0
        # Clamp to [WIN_PROB_MIN, WIN_PROB_MAX]
        clamped = max(WIN_PROB_MIN, min(WIN_PROB_MAX, raw_rate))
        results[label] = {
            "win_rate":    round(clamped, 4),
            "raw_rate":    round(raw_rate, 4),
            "trade_count": count,
            "wins":        wins,
            "sufficient":  count >= MIN_TRADES_PER_BUCKET,
        }

    return results


# ── Read / write order_agent.py ────────────────────────────────────────────────

def _read_current_table(order_agent_path: Path) -> list[tuple[float, float]]:
    """Parse the current _WIN_PROB_TABLE from order_agent.py using ast.literal_eval."""
    source = order_agent_path.read_text()
    m = _WIN_PROB_TABLE_PATTERN.search(source)
    if not m:
        raise ValueError(f"Could not find _WIN_PROB_TABLE in {order_agent_path}")
    table_body = m.group(2)
    # Extract individual tuples via regex
    tuples = re.findall(r"\((\d+\.?\d*),\s*(\d+\.?\d*)\)", table_body)
    return [(float(a), float(b)) for a, b in tuples]


def _format_new_table(
    current_table: list[tuple[float, float]],
    new_probs: dict[str, dict[str, Any]],
) -> str:
    """Format the replacement _WIN_PROB_TABLE string.

    Buckets with insufficient data keep their current value.
    """
    # Map min_conf → bucket label
    conf_to_label = {min_conf: label for min_conf, label in CONFIDENCE_BUCKETS}

    lines = []
    for min_conf, current_prob in current_table:
        label = conf_to_label.get(min_conf)
        bucket_data = new_probs.get(label) if label else None
        if bucket_data and bucket_data["sufficient"]:
            new_prob = bucket_data["win_rate"]
            comment = f"  # {bucket_data['wins']}/{bucket_data['trade_count']} trades"
        else:
            new_prob = current_prob
            count = bucket_data["trade_count"] if bucket_data else 0
            comment = f"  # insufficient data ({count}/{MIN_TRADES_PER_BUCKET} trades) — keeping default"
        lines.append(f"    ({min_conf:.1f}, {new_prob:.2f}),{comment}")

    return "\n".join(lines)


def _apply_update(order_agent_path: Path, new_table_body: str) -> None:
    """Backup order_agent.py and replace the _WIN_PROB_TABLE in-place."""
    source = order_agent_path.read_text()

    # Write backup
    backup_path = order_agent_path.with_suffix(".py.bak")
    shutil.copy2(order_agent_path, backup_path)
    logger.info("recalibrate backup written to %s", backup_path)

    def _replacer(m: re.Match) -> str:
        return m.group(1) + "\n" + new_table_body + "\n" + m.group(3)

    new_source = _WIN_PROB_TABLE_PATTERN.sub(_replacer, source)
    order_agent_path.write_text(new_source)
    logger.info("recalibrate updated %s", order_agent_path)


# ── Main entrypoint ────────────────────────────────────────────────────────────

def print_diff(
    current_table: list[tuple[float, float]],
    new_probs: dict[str, dict[str, Any]],
) -> None:
    """Print a before/after comparison of win probabilities."""
    conf_to_label = {min_conf: label for min_conf, label in CONFIDENCE_BUCKETS}
    print("\n  KELLY TABLE RECALIBRATION")
    print("  " + "-" * 70)
    print(f"  {'Bucket':<12} {'Current':<12} {'New':<12} {'Trades':<10} {'Raw%':<10} {'Status'}")
    print("  " + "-" * 70)
    for min_conf, current_prob in current_table:
        label = conf_to_label.get(min_conf, "?")
        bd = new_probs.get(label, {})
        new_prob = bd.get("win_rate", current_prob) if bd.get("sufficient") else current_prob
        count = bd.get("trade_count", 0)
        raw = bd.get("raw_rate", 0.0)
        status = "✅ updated" if bd.get("sufficient") else f"⏳ need {MIN_TRADES_PER_BUCKET - count} more"
        changed = " ←" if abs(new_prob - current_prob) > 0.001 else ""
        print(f"  {label:<12} {current_prob:<12.2f} {new_prob:<12.2f} {count:<10} {raw * 100:<10.1f}% {status}{changed}")
    print()


async def recalibrate(
    source: str = "backtest",
    trades_csv: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> None:
    """Main recalibration function.

    Args:
        source:     "backtest" (read from CSV) or "paper" (read from MongoDB).
        trades_csv: Path to trades.csv (required when source="backtest").
        dry_run:    If True, print diff but don't write to order_agent.py.
        confirm:    If True, write without asking (for scripted use).
    """
    if source == "backtest":
        if not trades_csv:
            raise ValueError("--trades is required when --source=backtest")
        trades = load_trades_from_csv(Path(trades_csv))
    else:
        trades = await load_trades_from_paper_db()

    if not trades:
        print("No trades found. Nothing to recalibrate.")
        return

    current_table = _read_current_table(_ORDER_AGENT_PATH)
    new_probs     = compute_new_win_probs(trades)

    print_diff(current_table, new_probs)

    any_sufficient = any(v["sufficient"] for v in new_probs.values())
    if not any_sufficient:
        print(f"  No bucket has ≥{MIN_TRADES_PER_BUCKET} trades yet. "
              f"Keep paper trading and re-run recalibrate when you have enough data.")
        return

    if dry_run:
        print("  Dry-run mode — no changes written.")
        return

    if not confirm:
        answer = input("  Write updated Kelly table to order_agent.py? [y/N]: ").strip().lower()
        if answer != "y":
            print("  Aborted.")
            return

    new_table_body = _format_new_table(current_table, new_probs)
    _apply_update(_ORDER_AGENT_PATH, new_table_body)
    print(f"  ✅ order_agent.py updated. Backup at {_ORDER_AGENT_PATH.with_suffix('.py.bak')}")


def main() -> None:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Recalibrate Kelly win-probability table")
    parser.add_argument("--source", choices=["backtest", "paper"], default="backtest")
    parser.add_argument("--trades", help="Path to trades.csv (required for --source=backtest)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no file write")
    parser.add_argument("--confirm", action="store_true", help="Write without interactive prompt")
    args = parser.parse_args()

    asyncio.run(recalibrate(
        source=args.source,
        trades_csv=args.trades,
        dry_run=args.dry_run,
        confirm=args.confirm,
    ))


if __name__ == "__main__":
    main()
