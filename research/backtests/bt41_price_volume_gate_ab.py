"""BT41 — anti-test for the four-bar price/volume quadrant gate.

The gate is NOT a subset filter. `state.candidate_seen` is only set after a
successful confirmation, so refusing one leaves the day open and a later
confirmation can produce a trade the OFF arm never had. Drawing random subsets
of the OFF trades would therefore compare against the wrong null — the error
that gave BT38 a false PASS on volume shelves.

This builds the matched null instead: refuse each otherwise-valid confirmation
at random with the same probability the real gate refused at, replayed through
the same engine so refusals create the same later-entry opportunities.

Reports the four criteria locked in
research/hypotheses/2026-09-19-price-volume-gate-ab.md before the run.

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt41_price_volume_gate_ab.py \
      --window w1 --seeds 200 --jobs 6
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(ROOT / "research" / "backtests"))

from bt17_momentum_pool import _trade_row, simulate_symbol  # noqa: E402
from src.momentum_trader import engine  # noqa: E402
from src.momentum_trader.upstox import Instrument  # noqa: E402

CACHE = ROOT / "research/backtests/.cache_upstox/1m"
HERE = Path(__file__).resolve().parent

WINDOWS = {
    "w1": ("2026-08-24", "2026-09-04", 2026),
    "w2": ("2024-01-01", "2024-12-31", 2024),
}

G1_ALPHA = 0.05
G2_LIFT_PP = 0.10
G4_MIN_N = 30


def counting_confirmation(original, counter: dict):
    """Wrap the confirmation point to count gate accept/refuse decisions.

    Only decisions the gate itself could change are counted: a confirmation
    that fails for some other reason is not a refusal attributable to the gate.
    """
    def confirm(bars, min_volume_ratio, guide=None, require_rising_price_volume=False):
        current, reason = original(bars, min_volume_ratio, guide, require_rising_price_volume)
        if not require_rising_price_volume:
            return current, reason
        baseline, _ = original(bars, min_volume_ratio, guide, False)
        if baseline is not None:
            counter["accepted" if current is not None else "refused"] += 1
        return current, reason
    return confirm


def random_refusal_confirmation(original, rate: float, seed: int, counter: dict):
    """Refuse otherwise-valid confirmations at `rate`, independently per decision.

    The engine is replayed around this, so a refusal leaves the day open in
    exactly the way a real gate refusal does.
    """
    rng = np.random.default_rng(seed)

    def confirm(bars, min_volume_ratio, guide=None, require_rising_price_volume=False):
        baseline, reason = original(bars, min_volume_ratio, guide, False)
        if baseline is None:
            return baseline, reason
        if rng.random() < rate:
            counter["refused"] += 1
            return None, "anti_random_refusal"
        counter["accepted"] += 1
        return baseline, "ok"
    return confirm


def _engine_cfg():
    """bt17 --attention --first-candidate-only, matching both A/B arms."""
    return engine.EngineConfig(
        stress_slip=engine.STRESS_SLIP,
        exit_mode="trend_resistance_state",
        fill_mode="future_trigger",
        use_attention_entries=True,
        require_resistance_breakout=True,
        allow_false_break_reentry=True,
        first_candidate_only=True,
    )


def _sessions(frame: pd.DataFrame, start: date, end: date) -> list[pd.Timestamp]:
    days = sorted({d for d in frame.index.normalize().unique()})
    return [d for d in days if start <= d.date() <= end]


def replay(task: tuple) -> dict:
    path, start_str, end_str, mode, rate, seed = task
    start, end = date.fromisoformat(start_str), date.fromisoformat(end_str)
    symbol = Path(path).stem.rsplit("_", 1)[0]
    frame = pd.read_parquet(path).sort_index()
    frame.index = frame.index.tz_convert("Asia/Kolkata")
    days = _sessions(frame, start, end)
    out = {"symbol": symbol, "rows": [], "accepted": 0, "refused": 0}
    if not days:
        return out
    inst = Instrument(symbol=symbol, isin="offline", key="offline",
                      name=symbol, tick_size=0.05)
    cfg = _engine_cfg()
    counter = {"accepted": 0, "refused": 0}
    original = engine._attention_confirmation
    if mode == "on":
        cfg.require_rising_price_volume = True
        wrapper = counting_confirmation(original, counter)
    elif mode == "off":
        wrapper = original
    else:
        wrapper = random_refusal_confirmation(original, rate, seed, counter)
    trades = []
    with patch.object(engine, "_attention_confirmation", wrapper):
        for day in days:
            daily, _ = simulate_symbol(
                inst, frame, day.date(), day.date(), cfg, lambda _s, _t: (0, ""),
                prefilter_chg_min=cfg.attention_day_chg_min,
            )
            trades.extend(daily)
    out["rows"] = [{"symbol": symbol, "date": str(t.entry_time.date()),
                    "gross_pct": _trade_row(t)["gross_pct"],
                    "net_pct": _trade_row(t)["net_pct"]} for t in trades]
    out["accepted"], out["refused"] = counter["accepted"], counter["refused"]
    return out


def run_mode(paths, start, end, mode, rate, seed, jobs) -> dict:
    tasks = [(str(p), start, end, mode, rate, seed) for p in paths]
    rows, accepted, refused = [], 0, 0
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for fut in as_completed([pool.submit(replay, t) for t in tasks]):
            r = fut.result()
            rows.extend(r["rows"])
            accepted += r["accepted"]
            refused += r["refused"]
    gross = np.array([r["gross_pct"] for r in rows], dtype=float)
    return {"rows": rows, "n": len(rows), "accepted": accepted, "refused": refused,
            "mean_gross": float(gross.mean()) if len(gross) else float("nan")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", choices=sorted(WINDOWS), required=True)
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--symbols-from", default="",
                    help="candidates CSV of the OFF arm; restricts the replay to "
                         "symbols that produced at least one candidate (a random "
                         "refusal can never create one where none existed)")
    a = ap.parse_args()
    start, end, year = WINDOWS[a.window]
    paths = sorted(CACHE.glob(f"*_{year}.parquet"))
    if a.symbols_from:
        keep = set(pd.read_csv(a.symbols_from)["symbol"].astype(str))
        paths = [p for p in paths if p.stem.rsplit("_", 1)[0] in keep]
    print(f"window {a.window} {start}..{end}  symbols={len(paths)}", flush=True)

    off = run_mode(paths, start, end, "off", 0.0, 0, a.jobs)
    on = run_mode(paths, start, end, "on", 0.0, 0, a.jobs)
    decisions = on["accepted"] + on["refused"]
    rate = on["refused"] / decisions if decisions else 0.0
    print(f"OFF n={off['n']} gross={off['mean_gross']:+.4f}", flush=True)
    print(f"ON  n={on['n']} gross={on['mean_gross']:+.4f}  "
          f"gate refused {on['refused']}/{decisions} = {rate * 100:.1f}%", flush=True)

    draws = []
    for seed in range(a.seeds):
        r = run_mode(paths, start, end, "anti", rate, 20260919 + seed, a.jobs)
        draws.append(r["mean_gross"])
        if (seed + 1) % 25 == 0:
            print(f"  anti seed {seed + 1}/{a.seeds}", flush=True)
    arr = np.array([d for d in draws if np.isfinite(d)], dtype=float)
    p_anti = float((arr >= on["mean_gross"]).mean()) if len(arr) else float("nan")
    lift = on["mean_gross"] - off["mean_gross"]

    report = {
        "window": a.window, "start": start, "end": end, "symbols": len(paths),
        "off": {"n": off["n"], "mean_gross": off["mean_gross"]},
        "on": {"n": on["n"], "mean_gross": on["mean_gross"],
               "refused": on["refused"], "decisions": decisions, "refusal_rate": rate},
        "anti": {"seeds": int(len(arr)), "mean": float(arr.mean()) if len(arr) else None,
                 "p5": float(np.percentile(arr, 5)) if len(arr) else None,
                 "p95": float(np.percentile(arr, 95)) if len(arr) else None,
                 "p_anti": p_anti},
        "lift_pp": lift,
        "G1_anti_pass": bool(p_anti < G1_ALPHA),
        "G2_lift_pass": bool(lift >= G2_LIFT_PP),
        "G4_n_pass": bool(on["n"] >= G4_MIN_N),
    }
    out = HERE / f"bt41_price_volume_gate_{a.window}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print("\n" + "-" * 70)
    print(f"lift            {lift:+.4f} pp        required >= +{G2_LIFT_PP}")
    print(f"anti p          {p_anti:.4f}          required <  {G1_ALPHA}")
    print(f"anti null mean  {report['anti']['mean']}  p95 {report['anti']['p95']}")
    print(f"n_on            {on['n']}             required >= {G4_MIN_N} (W2 only)")
    print("-" * 70)
    print("wrote", out.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
