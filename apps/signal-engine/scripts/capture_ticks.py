"""Record Upstox full-mode ticks to JSONL for the chart's "Big Trades" bubbles.

    cd apps/signal-engine
    .venv/bin/python scripts/capture_ticks.py --symbols RELIANCE,INFY --until 15:30
    .venv/bin/python scripts/capture_ticks.py --keys 'MCX_FO|573422'

⚠ The V3 feed is SNAPSHOT-based (~1 message/s per instrument): `ltq` is the last
trade only, so trades between snapshots are lost (measured 50-70% of lots seen on
MCX crude/natgas, 2026-11-05). One line is written per NEW trade (`ltt` changed):

    t=ltt ms, p=price, q=ltq, v=vtt (day volume), bp/bq/ap/aq=best bid/ask, r=receive ms

`v` lets the reader measure how much volume was missed between two lines.
Output: data/ticks/<YYYY-MM-DD IST>/<instrument key with | → _>.jsonl (git-ignored).
Token: UPSTOX_ANALYTICS_TOKEN (falls back to UPSTOX_ACCESS_TOKEN) from the repo .env.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "data" / "ticks"
NSE_MASTER = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def token() -> str:
    for name in ("UPSTOX_ANALYTICS_TOKEN", "UPSTOX_ACCESS_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    env = ROOT / ".env"
    found: dict[str, str] = {}
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            found[k.strip()] = v.strip().strip("\"'")
    return found.get("UPSTOX_ANALYTICS_TOKEN") or found.get("UPSTOX_ACCESS_TOKEN") or ""


def resolve(symbols: list[str]) -> list[str]:
    with urllib.request.urlopen(NSE_MASTER, timeout=30) as r:
        rows = json.loads(gzip.decompress(r.read()))
    by = {x["trading_symbol"]: x["instrument_key"] for x in rows
          if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}
    missing = [s for s in symbols if s not in by]
    if missing:
        raise SystemExit(f"unknown NSE symbols: {missing}")
    return [by[s] for s in symbols]


def parse_record(key: str, ff: dict, recv_ms: int) -> dict | None:
    """A tick record from one `marketFF` feed, or None when it has no price."""
    ltpc = ff.get("ltpc") or {}
    if "ltp" not in ltpc:
        return None
    q0 = (((ff.get("marketLevel") or {}).get("bidAskQuote")) or [{}])[0] or {}

    def num(x: object) -> float | None:
        return None if x is None else float(x)  # type: ignore[arg-type]

    return {
        "t": int(ltpc.get("ltt", 0)),
        "p": float(ltpc["ltp"]),
        "q": int(ltpc.get("ltq", 0)),
        "v": int(ff.get("vtt", 0)),
        "bp": num(q0.get("bidP")), "bq": num(q0.get("bidQ")),
        "ap": num(q0.get("askP")), "aq": num(q0.get("askQ")),
        "r": recv_ms,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma-separated NSE equity symbols")
    ap.add_argument("--keys", default="", help="comma-separated Upstox instrument keys")
    ap.add_argument("--until", default="", help="stop at HH:MM IST (default: run until Ctrl-C)")
    a = ap.parse_args()
    keys = [k for k in a.keys.split(",") if k]
    if a.symbols:
        keys += resolve([s for s in a.symbols.split(",") if s])
    if not keys:
        raise SystemExit("give --symbols or --keys")
    tok = token()
    if not tok:
        raise SystemExit("no UPSTOX_ANALYTICS_TOKEN / UPSTOX_ACCESS_TOKEN configured")

    import upstox_client  # heavy import; keep it lazy

    day = datetime.now(IST).strftime("%Y-%m-%d")
    folder = OUT / day
    folder.mkdir(parents=True, exist_ok=True)
    files = {k: open(folder / (k.replace("|", "_") + ".jsonl"), "a", buffering=1) for k in keys}
    last_ltt: dict[str, int] = {}
    counts = {k: 0 for k in keys}

    def on_message(m: dict) -> None:
        recv = int(time.time() * 1000)
        for key, feed in (m.get("feeds") or {}).items():
            ff = (feed.get("fullFeed") or {}).get("marketFF")
            if not ff or key not in files:
                continue
            rec = parse_record(key, ff, recv)
            if rec is None or rec["t"] == last_ltt.get(key):
                continue  # repeated snapshot of the same trade
            last_ltt[key] = rec["t"]
            files[key].write(json.dumps(rec, separators=(",", ":")) + "\n")
            counts[key] += 1

    cfg = upstox_client.Configuration()
    cfg.access_token = tok
    s = upstox_client.MarketDataStreamerV3(upstox_client.ApiClient(cfg), keys, "full")
    s.on("message", on_message)
    s.on("error", lambda e, *_: print("error:", str(e)[:200], flush=True))
    s.auto_reconnect(True, 5, 20)
    s.connect()
    print(f"recording {len(keys)} instrument(s) to {folder}", flush=True)
    try:
        while True:
            time.sleep(30)
            print(datetime.now(IST).strftime("%H:%M:%S"), counts, flush=True)
            if a.until and datetime.now(IST).strftime("%H:%M") >= a.until:
                break
    except KeyboardInterrupt:
        pass
    finally:
        s.disconnect()
        for f in files.values():
            f.close()


if __name__ == "__main__":
    main()
