"""Does any free Upstox V3 feed mode deliver every trade (tick-by-tick)? A side-by-side probe.

    cd apps/signal-engine
    .venv/bin/python scripts/upstox_feed_probe.py --futures NIFTY,BANKNIFTY --minutes 15
    .venv/bin/python scripts/upstox_feed_probe.py --report data/ticks/probe/2026-11-18/0920

Opens TWO websocket connections at once on the same instruments: `full` (what capture_ticks.py records: LTPC + 5-level
depth + day volume `vtt`) and `ltpc` (last trade only, the lightest mode). Every message is kept, not just new trades,
so the report can say how often each mode updates and how much of the traded volume it shows as separate trades:

    coverage = Σ ltq of distinct trades seen ÷ the rise in day volume (`vtt`, from the full stream) over the window

A mode that streamed every trade would show coverage ≈ 100 %. capture_ticks.py measured ~27–30 % for `full` on NSE
futures (2026-11-12). Upstox staff said in Feb 2026 that tick-by-tick is not exposed by the external API; this checks
whether `ltpc` is any better in practice. `full_d30` needs Upstox Plus (paid) and is not probed.

Uses 2 of the 2 websocket connections a normal account gets: stop the chart's tick recorder while it runs.
Output: data/ticks/probe/<YYYY-MM-DD>/<HHMM>/<mode>_<key>.jsonl (git-ignored, like data/ticks/), one line per message:
    r=receive ms, t=ltt ms, p=ltp, q=ltq, v=vtt (full only)
Token: same as capture_ticks.py (UPSTOX_ANALYTICS_TOKEN, else UPSTOX_ACCESS_TOKEN, from the repo .env); never printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from capture_ticks import IST, resolve_futures, token

OUT = Path(__file__).resolve().parents[1] / "data" / "ticks" / "probe"
MODES = ("full", "ltpc")


def ltpc_of(mode: str, feed: dict) -> tuple[dict, int | None]:
    """(ltpc dict, day volume or None) from one feed entry of either mode."""
    if mode == "ltpc":
        return feed.get("ltpc") or {}, None
    ff = (feed.get("fullFeed") or {}).get("marketFF") or {}
    vtt = ff.get("vtt")
    return ff.get("ltpc") or {}, int(vtt) if vtt is not None else None


def record(keys: list[str], minutes: float, folder: Path) -> None:
    import upstox_client  # heavy import; keep it lazy

    folder.mkdir(parents=True, exist_ok=True)
    files = {(m, k): open(folder / f"{m}_{k.replace('|', '_')}.jsonl", "a", buffering=1) for m in MODES for k in keys}
    counts = {m: 0 for m in MODES}

    def handler(mode: str):
        def on_message(msg: dict) -> None:
            recv = int(time.time() * 1000)
            for key, feed in (msg.get("feeds") or {}).items():
                if (mode, key) not in files:
                    continue
                lt, vtt = ltpc_of(mode, feed)
                if "ltp" not in lt:
                    continue
                rec = {"r": recv, "t": int(lt.get("ltt", 0)), "p": float(lt["ltp"]), "q": int(lt.get("ltq", 0))}
                if vtt is not None:
                    rec["v"] = vtt
                files[(mode, key)].write(json.dumps(rec, separators=(",", ":")) + "\n")
                counts[mode] += 1

        return on_message

    cfg = upstox_client.Configuration()
    cfg.access_token = token()
    if not cfg.access_token:
        raise SystemExit("no UPSTOX_ANALYTICS_TOKEN / UPSTOX_ACCESS_TOKEN configured")
    streams = []
    for mode in MODES:
        s = upstox_client.MarketDataStreamerV3(upstox_client.ApiClient(cfg), keys, mode)
        s.on("message", handler(mode))
        s.on("error", lambda e, *_m, mode=mode: print(f"{mode} error:", str(e)[:200], flush=True))
        s.connect()
        streams.append(s)
    print(f"probing {len(keys)} instrument(s) in modes {', '.join(MODES)} → {folder}", flush=True)
    end = time.time() + minutes * 60
    try:
        while time.time() < end:
            time.sleep(min(30, max(0.0, end - time.time())))
            print(datetime.now(IST).strftime("%H:%M:%S"), counts, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for s in streams:
            s.disconnect()
        for f in files.values():
            f.close()


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def report(folder: Path) -> None:
    keys = sorted({p.name.split("_", 1)[1][:-6] for p in folder.glob("*.jsonl")})
    if not keys:
        raise SystemExit(f"nothing recorded in {folder}")
    for key in keys:
        full = load(folder / f"full_{key}.jsonl")
        vols = [x["v"] for x in full if "v" in x]
        if len(vols) < 2:
            print(f"{key}: not enough full-mode messages to measure volume")
            continue
        t0, t1 = full[0]["r"], full[-1]["r"]
        traded = vols[-1] - vols[0]
        print(f"\n{key}: {(t1 - t0) / 60000:.1f} min, day volume rose by {traded:,} (full stream)")
        seen_by_mode: dict[str, set[tuple]] = {}
        for mode in MODES:
            # same window as the full stream, so both modes are measured over the same traded volume
            rows = [x for x in load(folder / f"{mode}_{key}.jsonl") if t0 <= x["r"] <= t1]
            trades = {(x["t"], x["p"], x["q"]) for x in rows}
            # the first message is a snapshot of a trade from before the window
            if rows:
                trades.discard((rows[0]["t"], rows[0]["p"], rows[0]["q"]))
            seen_by_mode[mode] = trades
            gaps = [b["r"] - a["r"] for a, b in zip(rows, rows[1:])]
            lag = [x["r"] - x["t"] for x in rows if x["t"]]
            cov = sum(q for _, _, q in trades) / traded if traded else 0
            print(
                f"  {mode:5}  {len(rows):6} messages ({len(rows) / max(1e-9, (t1 - t0) / 60000):.0f}/min, "
                f"median gap {statistics.median(gaps) if gaps else 0:.0f} ms)  {len(trades):6} distinct trades  "
                f"coverage {cov:6.1%}  receive − trade time median {statistics.median(lag) if lag else 0:.0f} ms"
            )
        both = seen_by_mode["full"] | seen_by_mode["ltpc"]
        only_l = seen_by_mode["ltpc"] - seen_by_mode["full"]
        print(
            f"  union  {len(both):6} distinct trades, coverage {sum(q for *_, q in both) / traded:6.1%}; "
            f"{len(only_l)} trades only ltpc saw"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--futures", default="NIFTY,BANKNIFTY", help="underlyings: front-month NSE future each")
    ap.add_argument("--keys", default="", help="comma-separated Upstox instrument keys instead")
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--report", default="", help="only print the report for an earlier probe folder")
    a = ap.parse_args()
    if a.report:
        report(Path(a.report))
        return
    keys = [k for k in a.keys.split(",") if k] or resolve_futures([s.strip().upper() for s in a.futures.split(",") if s.strip()])
    now = datetime.now(IST)
    folder = OUT / now.strftime("%Y-%m-%d") / now.strftime("%H%M")
    record(keys, a.minutes, folder)
    report(folder)


if __name__ == "__main__":
    main()
