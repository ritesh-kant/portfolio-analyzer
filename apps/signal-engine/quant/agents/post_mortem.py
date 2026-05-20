"""Post-mortem agent — structured analysis of every closed trade. Month 4.

Model: Deepseek-V3 (plan §6).
Triggered after every closed trade (paper or live) via the execution adapter.
Output is written to data/paper/post_mortems.jsonl (one JSON line per trade).

Month 5 extension: embed the narrative and write to sqlite-vec store for
retrieval-augmented decisioning at trade entry time.

Output schema (locked):
    {
        "trade_id": str,
        "symbol": str,
        "strategy_id": str,
        "entry_date": str,
        "exit_date": str,
        "entry_price": float,
        "exit_price": float,
        "net_return": float,
        "holding_days": int,
        "narrative": str,            # 3-5 sentence description
        "proximate_exit_cause": str, # TARGET|STOP|MAX_AGE|MANUAL|other
        "lesson": str,               # one sentence for retrieval matching
        "created_at": str,           # UTC ISO timestamp
    }
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a disciplined quantitative trading post-mortem analyst.
Write a concise, factual structured analysis of a closed trade.

Rules:
1. Narrative: 3-5 sentences — what happened, why, what the outcome was.
2. proximate_exit_cause: TARGET (hit profit target), STOP (hit stop loss),
   MAX_AGE (held max holding period and exited), MANUAL, or other.
   For PEAD strategy 5-day holds: use MAX_AGE unless told otherwise.
3. lesson: ONE sentence a future agent can retrieve. Focus on what was
   unusual that could improve future decision-making. Be specific.
4. Be honest about failures. Do not rationalise losing trades."""

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "proximate_exit_cause": {
            "type": "string",
            "enum": ["TARGET", "STOP", "MAX_AGE", "MANUAL", "other"],
        },
        "lesson": {"type": "string"},
    },
    "required": ["narrative", "proximate_exit_cause", "lesson"],
}


def run(
    symbol: str,
    strategy_id: str,
    entry_date: str,
    exit_date: str,
    entry_price: float,
    exit_price: float,
    net_return: float,
    holding_days: int,
    entry_features: dict | None = None,
    additional_context: str = "",
) -> dict:
    """Generate and persist a post-mortem for one closed trade.

    Parameters
    ----------
    symbol : str
    strategy_id : str
    entry_date, exit_date : str  ISO date strings
    entry_price, exit_price : float
    net_return : float  Net return after costs (0.012 = +1.2%)
    holding_days : int
    entry_features : dict | None  Feature values at entry (from build_earnings_features)
    additional_context : str  Free-text context (regime, filing summary, etc.)

    Returns
    -------
    dict — the full post-mortem record (also written to JSONL).
    """
    return_pct = net_return * 100

    user_prompt = (
        f"Trade: LONG {symbol} via {strategy_id}\n"
        f"Entry: {entry_date} @ ₹{entry_price:.2f}\n"
        f"Exit:  {exit_date} @ ₹{exit_price:.2f}\n"
        f"Net return: {return_pct:+.2f}%  Holding: {holding_days} days\n"
    )

    if entry_features:
        notable = {
            k: v for k, v in entry_features.items()
            if v is not None and k not in ("symbol", "announcement_date", "quarter_sin", "quarter_cos")
        }
        if notable:
            lines = "\n".join(
                f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}"
                for k, v in notable.items()
            )
            user_prompt += f"\nEntry features:\n{lines}\n"

    if additional_context:
        user_prompt += f"\nContext:\n{additional_context}\n"

    user_prompt += "\nWrite the post-mortem analysis."

    llm_output = _call_llm(user_prompt)

    record = {
        "trade_id": str(uuid.uuid4()),
        "symbol": symbol,
        "strategy_id": strategy_id,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "net_return": net_return,
        "holding_days": holding_days,
        "narrative": llm_output.get("narrative", ""),
        "proximate_exit_cause": llm_output.get("proximate_exit_cause", "other"),
        "lesson": llm_output.get("lesson", ""),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    _write_jsonl(record)
    logger.info(
        "post_mortem written trade_id=%s symbol=%s return=%.2f%%",
        record["trade_id"], symbol, return_pct,
    )
    return record


def write_post_mortem(closed_trade: dict, entry_features: dict) -> dict:
    """Legacy entry point — delegates to run()."""
    return run(
        symbol=closed_trade.get("symbol", "UNKNOWN"),
        strategy_id=closed_trade.get("strategy_id", "unknown"),
        entry_date=closed_trade.get("entry_date", ""),
        exit_date=closed_trade.get("exit_date", ""),
        entry_price=float(closed_trade.get("entry_price", 0)),
        exit_price=float(closed_trade.get("exit_price", 0)),
        net_return=float(closed_trade.get("net_return", 0)),
        holding_days=int(closed_trade.get("holding_days", 0)),
        entry_features=entry_features,
    )


def load_all(limit: int | None = None) -> list[dict]:
    """Load all post-mortems from the JSONL store."""
    path = _jsonl_path()
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if limit is not None:
        return records[-limit:]
    return records


def weekly_summary() -> dict:
    """Aggregate post-mortems from the last 7 days."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    records = [r for r in load_all() if r.get("exit_date", "") >= cutoff]

    if not records:
        return {"n_trades": 0, "win_rate": 0.0, "avg_net_return": 0.0, "failure_modes": []}

    returns = [r["net_return"] for r in records]
    wins = sum(1 for r in returns if r > 0)
    causes = Counter(r.get("proximate_exit_cause", "other") for r in records)

    return {
        "n_trades": len(records),
        "win_rate": wins / len(records),
        "avg_net_return": sum(returns) / len(returns),
        "failure_modes": [{"cause": c, "count": n} for c, n in causes.most_common(5)],
    }


# ── Private ────────────────────────────────────────────────────────────────────

def _jsonl_path() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data")
    return Path(base) / "paper" / "post_mortems.jsonl"


def _write_jsonl(record: dict) -> None:
    path = _jsonl_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _call_llm(user_prompt: str) -> dict:
    try:
        from quant.agents import llm_router
        return llm_router.call(
            role="parser",
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            response_schema=_OUTPUT_SCHEMA,
            temperature=0.15,
        )
    except Exception as exc:
        logger.warning("Post-mortem LLM call failed (fallback used): %s", exc)
        return {
            "narrative": "LLM unavailable — trade details recorded without analysis.",
            "proximate_exit_cause": "MAX_AGE",
            "lesson": "LLM unavailable at post-mortem time; manual review needed.",
        }
