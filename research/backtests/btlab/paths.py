"""Where the bar cache, the lab's own cache and the saved runs live.

The Upstox bar cache (~1.5 GB) is git-ignored, so a git worktree has none of it.
`upstox_1m_dir()` therefore falls back to the main checkout's copy instead of
silently finding no data.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from . import REPO

BACKTESTS = REPO / "research" / "backtests"
LAB_CACHE = BACKTESTS / ".lab_cache"      # git-ignored: rebuildable candidate tables
RUNS_DIR = BACKTESTS / "lab_runs"         # run.json is tracked; trades.csv is not
STATIC = Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=1)
def upstox_1m_dir() -> Path:
    env = os.environ.get("MT_CACHE_ROOT")
    candidates = [Path(env) / ".cache_upstox" / "1m"] if env else []
    candidates.append(BACKTESTS / ".cache_upstox" / "1m")
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO, capture_output=True,
            text=True, check=True).stdout.strip()
        main_root = (REPO / common).resolve().parent
        candidates.append(main_root / "research" / "backtests" / ".cache_upstox" / "1m")
    except Exception:  # noqa: BLE001 - not a git checkout; the other candidates still apply
        pass
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0] if candidates else BACKTESTS / ".cache_upstox" / "1m"


def cached_symbols(year: int) -> list[str]:
    d = upstox_1m_dir()
    return sorted(p.name[: -len(f"_{year}.parquet")] for p in d.glob(f"*_{year}.parquet"))


def cached_years() -> list[int]:
    d = upstox_1m_dir()
    years = {int(p.stem.rsplit("_", 1)[1]) for p in d.glob("*_*.parquet")
             if p.stem.rsplit("_", 1)[1].isdigit()}
    return sorted(years)
