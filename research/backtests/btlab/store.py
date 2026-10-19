"""Saved runs: nothing a user applies is ever lost.

One folder per run under `research/backtests/lab_runs/<id>/`:

  run.json    the exact configuration (base rule, years, patterns, sizing, every
              indicator with its parameters), the headline numbers, the comparison
              with the base trade, the git commit and the lab version. Tracked.
  trades.csv  the trades, in the dashboard's schema. Git-ignored: it is a pure
              function of run.json, and `service.ensure_trades` rebuilds it.

`ledger.jsonl` appends one line per DISTINCT configuration ever run. It is the
trial counter: the more variants tried on the same data, the more of them look
good by luck, so the UI shows the count next to every result.

A configuration that was already run is returned, not duplicated.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import LAB_VERSION, REPO
from .paths import RUNS_DIR
from . import plugins as P

LEDGER = RUNS_DIR / "ledger.jsonl"
NEWS_REVISION = 4
_lock = threading.RLock()


def canonical_config(cfg: dict) -> dict:
    """The part of a request that determines the trades, in a stable shape."""
    base = {k: v for k, v in cfg["base"].items()
            if not (k == "market" and v == "NSE")                 # NSE keys predate markets
            and not (k == "entry_mode" and v == "pattern")        # so do keys that predate the no-pattern option
            and not (k == "stop_levels" and v == "all")}          # and the stop-levels option
    revised = {x["id"]: NEWS_REVISION if x["id"] in P.NEWS_IDS else 2 for x in cfg["plugins"]
               if x["id"] in P.NEWS_IDS or
               (x["id"] in ("support_rule", "resistance_rule") and x["params"].get("kind") == "swing")}
    return {
        "years": sorted(int(y) for y in cfg["years"]),
        "base": dict(sorted(base.items())),
        "patterns": sorted(cfg["patterns"]),
        "risk_inr": float(cfg["risk_inr"]), "max_notional_inr": float(cfg["max_notional_inr"]),
        "max_trades": int(cfg["max_trades"]),
        **({"max_positions": int(cfg["max_positions"])} if cfg.get("max_positions") else {}),   # absent = unlimited, so old keys stand
        "plugins": [{"id": x["id"], "params": dict(sorted(x["params"].items()))}
                    for x in sorted(cfg["plugins"], key=lambda x: x["id"])],
        **({"indicator_revisions": revised} if revised else {}),
        **({"news_data_key": cfg["news_data_key"]} if cfg.get("news_data_key") else {}),
        "lab_version": LAB_VERSION,
    }


def rebuild_error(rec: dict) -> str | None:
    """Never replace a historical result with trades from different indicator semantics."""
    cfg = rec["config"]
    if any(x["id"] == "news" and x["params"].get("trigger") == "news_bar" and "bar_min_move_pct" not in x["params"]
           for x in cfg["plugins"]):
        return ("This saved news_bar run predates the price-up-since-filing floor and its original trades.csv "
                "is missing. Restore that CSV, or Apply the settings as a new experiment (set the floor to 0 "
                "to reproduce it). Its saved metrics have been preserved.")
    news_ids = [x["id"] for x in cfg["plugins"] if x["id"] in P.NEWS_IDS]
    if news_ids:
        rev = min(cfg.get("indicator_revisions", {}).get(i, 1) for i in news_ids)
        if rev != NEWS_REVISION:
            return ("This saved news run uses an older indicator revision and its original trades.csv "
                    "is missing. Restore that CSV to view its original charts, or Apply the settings "
                    "as a new experiment. Its saved metrics have been preserved.")
    return None


def config_key(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(canonical_config(cfg), sort_keys=True).encode()).hexdigest()[:12]


def window_key(cfg: dict) -> str:
    """Same data and base rule, any indicators: what a 'trial' is counted against."""
    c = canonical_config(cfg)
    blob = json.dumps({k: c[k] for k in ("years", "base", "patterns", "risk_inr",
                                         "max_notional_inr", "max_trades", "max_positions") if k in c}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def label_for(plugins: list[dict]) -> str:
    if not plugins:
        return "Base trade"
    known = [x for x in plugins if x["id"] in P.REGISTRY]       # runs saved with a since-removed indicator
    ent = [P.REGISTRY[x["id"]].name for x in known if P.REGISTRY[x["id"]].group == "entry"]
    ext = [P.REGISTRY[x["id"]].name for x in known if P.REGISTRY[x["id"]].group == "exit"]
    ext += [x["id"] + " (removed)" for x in plugins if x["id"] not in P.REGISTRY]
    parts = []
    if ent:
        parts.append("Entry: " + " + ".join(ent))
    if ext:
        parts.append("Exit: " + " + ".join(ext))
    return " · ".join(parts)


def git_info() -> dict:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "research/backtests/btlab",
                                     "apps/signal-engine/src/momentum_trader"], cwd=REPO,
                                    capture_output=True, text=True).stdout.strip())
        return {"sha": sha, "dirty": dirty}
    except Exception:  # noqa: BLE001
        return {"sha": "", "dirty": False}


def _dir(run_id: str) -> Path:
    if not run_id.replace("-", "").isalnum():
        raise ValueError("bad run id")
    return RUNS_DIR / run_id


def find_by_key(key: str) -> dict | None:
    for r in list_runs():
        if r.get("key") == key:
            return r
    return None


def _ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def trial_count(wkey: str) -> int:
    """Distinct configurations ever run on this window (deleting a run does not reset it)."""
    return len({e["key"] for e in _ledger() if e.get("window") == wkey and e.get("plugins")})


def save_run(cfg: dict, trades: pd.DataFrame, metrics: dict, vs_base: dict, base_id: str | None,
             name: str = "", notes: str = "") -> dict:
    with _lock:
        key = config_key(cfg)
        existing = find_by_key(key)
        if existing:
            return existing
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        run_id = f"{now:%Y%m%d-%H%M%S}-{key[:6]}"
        wkey = window_key(cfg)
        rec = {
            "id": run_id, "key": key, "window": wkey, "created_at": now.isoformat(timespec="seconds"),
            "name": name or (("US · " if cfg["base"].get("market") == "US" else "") + label_for(cfg["plugins"])), "notes": notes, "starred": False,
            "is_base": not cfg["plugins"], "base_id": base_id,
            "config": canonical_config(cfg), "git": git_info(),
            "metrics": metrics, "vs_base": vs_base,
            "trial_n": 0 if not cfg["plugins"] else
                       trial_count(wkey) + (0 if key in {e["key"] for e in _ledger()} else 1),
        }
        d = _dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        trades.to_csv(d / "trades.csv", index=False)
        (d / "run.json").write_text(json.dumps(rec, indent=1))
        if key not in {e["key"] for e in _ledger()}:
            with open(LEDGER, "a") as fh:
                fh.write(json.dumps({"id": run_id, "key": key, "window": wkey, "at": rec["created_at"],
                                     "plugins": [x["id"] for x in cfg["plugins"]]}) + "\n")
        return rec


_cache: dict[str, tuple[float, dict]] = {}


def get_run(run_id: str) -> dict | None:
    f = _dir(run_id) / "run.json"
    if not f.exists():
        return None
    m = f.stat().st_mtime
    hit = _cache.get(run_id)
    if hit and hit[0] == m:
        return hit[1]
    rec = json.loads(f.read_text())
    _cache[run_id] = (m, rec)
    return rec


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    out = [r for r in (get_run(p.name) for p in RUNS_DIR.iterdir() if p.is_dir()) if r]
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


def update_run(run_id: str, **fields: object) -> dict | None:
    with _lock:
        rec = get_run(run_id)
        if rec is None:
            return None
        rec = dict(rec)
        for k in ("name", "notes", "starred"):
            if k in fields:
                rec[k] = fields[k]
        (_dir(run_id) / "run.json").write_text(json.dumps(rec, indent=1))
        _cache.pop(run_id, None)
        return get_run(run_id)


def delete_run(run_id: str) -> bool:
    import shutil
    with _lock:
        d = _dir(run_id)
        if not d.exists():
            return False
        shutil.rmtree(d)
        _cache.pop(run_id, None)
        return True


def trades_path(run_id: str) -> Path:
    return _dir(run_id) / "trades.csv"
