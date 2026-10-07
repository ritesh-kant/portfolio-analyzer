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

import pyarrow.parquet as pq

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


@lru_cache(maxsize=1)
def ibkr_us_1m_dir() -> Path:
    """IBKR 1-minute US bars (`<SYMBOL>_<conId>_<year>.parquet`), same worktree fallback."""
    env = os.environ.get("MT_CACHE_ROOT")
    candidates = [Path(env) / ".cache_ibkr_us" / "1m"] if env else []
    candidates.append(BACKTESTS / ".cache_ibkr_us" / "1m")
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO, capture_output=True,
            text=True, check=True).stdout.strip()
        candidates.append((REPO / common).resolve().parent / "research" / "backtests" / ".cache_ibkr_us" / "1m")
    except Exception:  # noqa: BLE001
        pass
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def bar_file(sym: str, year: int, market: str = "NSE") -> Path | None:
    if market == "US":
        found = sorted(ibkr_us_1m_dir().glob(f"{sym}_*_{year}.parquet"))
        return found[0] if found else None
    f = upstox_1m_dir() / f"{sym}_{year}.parquet"
    return f if f.exists() else None


@lru_cache(maxsize=None)
def _rows(path: str, mtime_ns: int) -> int:
    """Row count from the parquet footer (no data read); 0 for an unreadable file."""
    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:  # noqa: BLE001 - a corrupt cache file counts as empty
        return 0


def has_bars(p: Path) -> bool:
    """False for the empty placeholder files a fetch leaves when the source has no data.

    Upstox serves no 1-minute history before 2022-01-01, so the 2021 files written as
    warm-up for 2022 are zero-row parquet files. They must not make 2021 look 'built'.
    """
    return _rows(str(p), p.stat().st_mtime_ns) > 0


def cached_symbols(year: int, market: str = "NSE") -> list[str]:
    if market == "US":
        files = ibkr_us_1m_dir().glob(f"*_*_{year}.parquet")
        return sorted({p.name.split("_")[0] for p in files if has_bars(p)})
    d = upstox_1m_dir()
    return sorted(p.name[: -len(f"_{year}.parquet")] for p in d.glob(f"*_{year}.parquet") if has_bars(p))


def cached_years(market: str = "NSE") -> list[int]:
    """Years with at least one non-empty bar file."""
    d = ibkr_us_1m_dir() if market == "US" else upstox_1m_dir()
    years: set[int] = set()
    for p in sorted(d.glob("*_*.parquet")):
        y = p.stem.rsplit("_", 1)[1]
        if y.isdigit() and int(y) not in years and has_bars(p):
            years.add(int(y))
    return sorted(years)
