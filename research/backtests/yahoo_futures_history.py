"""Record Yahoo futures bars (NQ=F by default) into a local cache that only grows.

Why: the AR-model strategy (research/specs/ar-model-chart-strategy-plan.md) is tested on NQ futures, and there
is no CME market data on the IBKR account. Yahoo serves NQ=F around the clock (the Asia session included) but
keeps only a sliding window of intraday history, measured 2026-11-12:

    1m   4 weeks back, 7 days per request (a window starting further back returns HTTP 422)
    5m   ~70 days        15m  ~70 days        60m  ~2.4 years        1d  years

Every run fetches everything Yahoo still has and MERGES it into the cache, so bars already saved are kept after
Yahoo drops them. Run it at least once every 4 weeks or 1-minute bars are lost for good. Weekly is safe.

    pnpm bt:nq                                   # NQ=F, all intervals
    pnpm bt:nq --intervals 1m,5m                 # just these
    pnpm bt:nq --symbols NQ=F,MNQ=F

Cache: research/backtests/.cache_yahoo_futures/<SYMBOL>/<interval>/<YYYY-MM>.csv (UTC month of the bar), with
columns time,open,high,low,close,volume (time = bar start, unix seconds), plus <SYMBOL>/manifest.json. A re-fetched
bar replaces the saved one (Yahoo's newest copy wins), so a candle that was still forming at the last run gets
completed by the next.

⚠ NQ=F is Yahoo's continuous front month, NOT back-adjusted: prices jump at each quarterly roll (around a week
before the March/June/September/December expiry). Anything that measures across a roll (a range, an ATR, a fib)
must handle that jump.

Stdlib only; read-only HTTP to Yahoo's public chart endpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache_yahoo_futures"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
ET = timezone(timedelta(hours=-5))  # labels only; gaps are judged in UTC below

DAY = 86_400
# interval → how to ask for everything Yahoo still has
PLANS: dict[str, dict] = {
    "1m": {"windows_days": 7, "back_days": 29},   # 7-day windows back to ~29 days
    "5m": {"range": "60d"},
    "15m": {"range": "60d"},
    "60m": {"range": "730d"},
    "1d": {"range": "10y"},
}
FIELDS = ("time", "open", "high", "low", "close", "volume")


def fetch(symbol: str, interval: str, span: str) -> tuple[list[dict], dict]:
    """One chart request → (bars, meta). Raises on failure after trying both hosts."""
    last = "no response"
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{urllib.request.quote(symbol)}?interval={interval}&{span}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as res:
                chart = json.load(res)["chart"]
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:160].decode(errors='replace')}"
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = str(e)
            continue
        result = (chart.get("result") or [None])[0]
        if not result:
            last = str((chart.get("error") or {}).get("description", "empty result"))
            continue
        q = result["indicators"]["quote"][0]
        bars = []
        for i, t in enumerate(result.get("timestamp") or []):
            o, h, lo, c = (q[k][i] for k in ("open", "high", "low", "close"))
            # The last element can be Yahoo's live trailing tick (stamped to the second, e.g. 05:24:05): not a bar.
            # (Hourly bars at :30 on early-close days are real; they are still on a whole minute.)
            if None in (o, h, lo, c) or int(t) % 60:
                continue
            bars.append({"time": int(t), "open": o, "high": h, "low": lo, "close": c, "volume": int(q["volume"][i] or 0)})
        return bars, result.get("meta", {})
    raise RuntimeError(f"{symbol} {interval} {span}: {last}")


def fetch_all(symbol: str, interval: str) -> tuple[list[dict], dict, list[str]]:
    plan = PLANS[interval]
    if "range" in plan:
        bars, meta = fetch(symbol, interval, f"range={plan['range']}")
        return bars, meta, []
    now = int(time.time())
    floor = now - plan["back_days"] * DAY
    out: dict[int, dict] = {}
    meta: dict = {}
    errors: list[str] = []
    end = now
    while end > floor:
        start = max(floor, end - plan["windows_days"] * DAY)
        try:
            chunk, meta = fetch(symbol, interval, f"period1={start}&period2={end}")
            for b in chunk:
                out[b["time"]] = b
        except RuntimeError as e:
            errors.append(str(e))
        end = start
        time.sleep(0.4)
    return sorted(out.values(), key=lambda b: b["time"]), meta, errors


def month_key(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m")


def read_month(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {
            int(r["time"]): {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["volume"]),
            }
            for r in csv.DictReader(f)
        }


def write_month(path: Path, rows: dict[int, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for t in sorted(rows):
            w.writerow(rows[t])
    os.replace(tmp, path)


def merge(sym_dir: Path, interval: str, bars: list[dict]) -> tuple[int, int]:
    """Merge into the monthly files. Returns (new bars added, bars that changed)."""
    by_month: dict[str, list[dict]] = {}
    for b in bars:
        by_month.setdefault(month_key(b["time"]), []).append(b)
    added = changed = 0
    for month, chunk in by_month.items():
        path = sym_dir / interval / f"{month}.csv"
        rows = {t: r for t, r in read_month(path).items() if t % 60 == 0}  # drops trailing ticks saved by older runs
        for b in chunk:
            old = rows.get(b["time"])
            if old is None:
                added += 1
            elif old != b:
                changed += 1
            rows[b["time"]] = b
        write_month(path, rows)
    return added, changed


def scan(sym_dir: Path, interval: str) -> dict:
    """Coverage of the whole cache for one interval: count, first/last, and unexpected gaps."""
    times: list[int] = []
    for path in sorted((sym_dir / interval).glob("*.csv")):
        times.extend(sorted(read_month(path)))
    if not times:
        return {"bars": 0}
    step = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": DAY}[interval]
    gaps = []
    if interval != "1d":
        for a, b in zip(times, times[1:]):
            if b - a <= max(2 * step, 75 * 60):  # CME's daily 17:00–18:00 ET halt is normal
                continue
            # a weekend (Fri close → Sun open) is normal too
            if datetime.fromtimestamp(a, ET).weekday() == 4 and b - a <= 3 * DAY:
                continue
            gaps.append([datetime.fromtimestamp(a, ET).strftime("%Y-%m-%d %H:%M"), round((b - a) / 3600, 1)])
    return {
        "bars": len(times),
        "first": datetime.fromtimestamp(times[0], timezone.utc).isoformat(),
        "last": datetime.fromtimestamp(times[-1], timezone.utc).isoformat(),
        "gaps_over_75min_et": gaps[-50:],  # holidays and early closes show up here; anything else is a hole
        "gap_count": len(gaps),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", default="NQ=F")
    ap.add_argument("--intervals", default=",".join(PLANS))
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()

    root = Path(args.cache)
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Yahoo futures bar cache: data, not code\n*\n")

    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    bad = [i for i in intervals if i not in PLANS]
    if bad:
        ap.error(f"unknown interval(s) {bad}; choose from {list(PLANS)}")

    failed = False
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        sym_dir = root / symbol.replace("=", "_")
        manifest_path = sym_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"symbol": symbol, "intervals": {}}
        for interval in intervals:
            try:
                bars, meta, errors = fetch_all(symbol, interval)
            except RuntimeError as e:
                print(f"{symbol:6} {interval:>4}  FAILED  {e}", file=sys.stderr)
                failed = True
                continue
            added, changed = merge(sym_dir, interval, bars)
            cov = scan(sym_dir, interval)
            if meta:
                manifest["meta"] = {
                    k: meta.get(k)
                    for k in ("symbol", "longName", "shortName", "currency", "fullExchangeName", "exchangeName",
                              "exchangeTimezoneName", "instrumentType")
                }
            manifest["intervals"][interval] = {
                **cov,
                "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_fetch": {"bars": len(bars), "added": added, "changed": changed, "errors": errors},
            }
            print(
                f"{symbol:6} {interval:>4}  fetched {len(bars):6}  +{added:<6} ~{changed:<5}"
                f"cache {cov.get('bars', 0):7}  {cov.get('first', '')[:10]} → {cov.get('last', '')[:10]}"
                f"  unexpected gaps {cov.get('gap_count', 0)}" + (f"  ({len(errors)} window errors)" if errors else "")
            )
            if errors:
                failed = True
        sym_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
