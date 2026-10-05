"""Command line for the lab (the UI is at http://localhost:8898/lab).

  cd research/backtests
  ../../apps/signal-engine/.venv/bin/python -m btlab build --years 2024,2025
  ../../apps/signal-engine/.venv/bin/python -m btlab apply --years 2026 --with above_vwap,ema9_exit
  ../../apps/signal-engine/.venv/bin/python -m btlab runs

`apply` saves the run exactly as the UI does (same store, same dedupe, same trial counter).
Indicators take their default parameters here; use the UI to change them.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import base, plugins as P, service, store
from .paths import cached_years


def _years(s: str) -> list[int]:
    return cached_years() if s == "all" else [int(y) for y in s.split(",")]


def main() -> int:
    ap = argparse.ArgumentParser(prog="btlab", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the base candidate data for some years")
    b.add_argument("--years", default="all")
    a = sub.add_parser("apply", help="apply indicators to the base trade and save the run")
    a.add_argument("--years", default="all")
    a.add_argument("--with", dest="with_", default="", help="comma-separated indicator ids")
    a.add_argument("--name", default="")
    sub.add_parser("indicators", help="list the indicator ids")
    sub.add_parser("runs", help="list saved runs")
    args = ap.parse_args()

    if args.cmd == "indicators":
        for p in P.catalog():
            print(f'{p["group"]:<6} {p["id"]:<24} {p["name"]}')
        return 0
    if args.cmd == "runs":
        for r in store.list_runs():
            m = r["metrics"]
            print(f'{r["id"]}  {r["name"][:70]:<70} n={m.get("n", 0):>5} '
                  f'₹/trade={m.get("mean_inr", 0):>8} median={m.get("median_inr", 0):>8}')
        return 0
    if args.cmd == "build":
        rule = base.BaseRule()
        for y in _years(args.years):
            meta = base.build_year(y, rule, progress=lambda n, t, s: print(f"\r{y}: {n}/{t}", end="", flush=True))
            print(f'\r{y}: {meta["n_candidates"]} candidates on {meta["n_days"]} symbol-days '
                  f'in {meta["elapsed_s"]} s')
        return 0
    plugins = [{"id": i, "params": {}} for i in args.with_.split(",") if i]
    try:
        rec = service.apply({"years": _years(args.years), "plugins": plugins}, name=args.name)
    except service.NeedBuild as nb:
        print(f"base data missing for {nb.years}: run `build --years {','.join(map(str, nb.years))}` first")
        return 1
    m = rec["metrics"]
    print(json.dumps({"id": rec["id"], "name": rec["name"], "cached": rec.get("cached"),
                      **{k: m.get(k) for k in ("n", "win_pct", "mean_inr", "median_inr", "net_real_inr")},
                      "vs_base": rec.get("vs_base")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
