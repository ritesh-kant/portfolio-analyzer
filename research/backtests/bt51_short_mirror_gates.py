# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "scipy"]
# ///
"""BT51 — gates for research/hypotheses/2026-09-26-momentum-short-mirror.md.

Reads the bt17 trade CSVs the hypothesis names and scores the five locked
criteria. Nothing here chooses a threshold; they are copied from §3.

Real-cost net is recomputed from each row's entry/exit/qty with the side's own
MIS cost model (a short pays STT on its entry sale). bt17's `net_inr` carries
the +40 bps/side research stress and is printed alongside as the stressed
figure, never used for a gate.

Usage (repo root):
  uv run research/backtests/bt51_short_mirror_gates.py            # 2024, the registered dev run
  uv run research/backtests/bt51_short_mirror_gates.py --suffix ly  # trailing 12 months

"Halves" split at the midpoint of the window's own dates, which for 2024 is
the calendar mid-year the hypothesis names.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine"))

from src.news_trader.trailing_sl import calc_costs  # noqa: E402

HERE = Path(__file__).resolve().parent

GROSS_BAR_PCT = 0.35     # C2
N_MIN = 300              # C4
DROP_TOP = 20            # C5


def with_real_net(tr: pd.DataFrame) -> pd.DataFrame:
    tr = tr.copy()
    side = tr["side"] if "side" in tr.columns else pd.Series("long", index=tr.index)
    tr["real_costs_inr"] = [
        calc_costs(e, x, int(q), direction=s)["total"]
        for e, x, q, s in zip(tr["entry"], tr["exit"], tr["qty"], side, strict=True)
    ]
    tr["real_net_inr"] = tr["gross_inr"] - tr["real_costs_inr"]
    return tr


def summary(tr: pd.DataFrame, label: str) -> dict[str, float]:
    g = tr["gross_pct"]
    n = len(tr)
    mean, sd = float(g.mean()), float(g.std(ddof=1)) if n > 1 else float("nan")
    half = stats.t.ppf(0.975, n - 1) * sd / np.sqrt(n) if n > 1 else float("nan")
    real = tr["real_net_inr"]
    top = real.sort_values(ascending=False).iloc[DROP_TOP:] if n > DROP_TOP else real.iloc[:0]
    dates = pd.to_datetime(tr["date"])
    mid = dates < dates.min() + (dates.max() - dates.min()) / 2
    out = {
        "n": n,
        "gross_pct_mean": mean,
        "gross_ci_lo": mean - half,
        "gross_ci_hi": mean + half,
        "gross_pct_median": float(g.median()),
        "real_net_inr_mean": float(real.mean()),
        "real_net_inr_median": float(real.median()),
        "real_net_inr_total": float(real.sum()),
        "real_net_drop_top20_mean": float(top.mean()) if len(top) else float("nan"),
        "stressed_net_inr_mean": float(tr["net_inr"].mean()),
        "gross_h1": float(g[mid].mean()) if mid.any() else float("nan"),
        "gross_h2": float(g[~mid].mean()) if (~mid).any() else float("nan"),
        "n_h1": int(mid.sum()),
        "n_h2": int((~mid).sum()),
        "win_rate_real": float((real > 0).mean() * 100),
    }
    print(f"\n── {label} ──")
    for k, v in out.items():
        print(f"  {k:26s} {v:,.4f}" if isinstance(v, float) else f"  {k:26s} {v}")
    return out


def gates(s: dict[str, float]) -> bool:
    checks = {
        "C1 mean real-cost net > ₹0": s["real_net_inr_mean"] > 0,
        f"C2 mean gross ≥ +{GROSS_BAR_PCT}%": s["gross_pct_mean"] >= GROSS_BAR_PCT,
        "C3 gross 95% CI lower bound > 0": s["gross_ci_lo"] > 0,
        f"C4 n ≥ {N_MIN}": s["n"] >= N_MIN,
        "C5 gross > 0 in both halves AND drop-top-20 real net > 0": (
            s["gross_h1"] > 0 and s["gross_h2"] > 0 and s["real_net_drop_top20_mean"] > 0
        ),
    }
    print("\n  LOCKED CRITERIA")
    for name, ok in checks.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    verdict = all(checks.values())
    print(f"\n  VERDICT: {'PASS → earns one 2025 run' if verdict else 'KILL'}")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="2024", help="bt17 --tag suffix of the four runs")
    sfx = ap.parse_args().suffix
    PRIMARY = HERE / f"bt17_trades_short_mirror_{sfx}.csv"
    if not PRIMARY.exists():
        print(f"missing {PRIMARY.name}")
        return 1
    primary = with_real_net(pd.read_csv(PRIMARY))
    s = summary(primary, f"PRIMARY P — short mirror, legacy pool, --live-fill, {sfx}")
    print("\n  by setup (gross %/trade, real net ₹/trade):")
    print(primary.groupby("setup").agg(n=("gross_pct", "size"), gross=("gross_pct", "mean"),
                                       real_net=("real_net_inr", "mean")).round(3).to_string())
    print("\n  by exit reason:")
    print(primary.groupby("exit_reason").agg(n=("gross_pct", "size"),
                                             gross=("gross_pct", "mean")).round(3).to_string())
    gates(s)
    for path, label in (
        (HERE / f"bt17_trades_long_ctx_{sfx}.csv", "CONTEXT — long pool, same config (not a gate)"),
        (HERE / f"bt17_trades_short_warrior_{sfx}.csv",
         "SECONDARY S — short, warrior_strict (descriptive)"),
        (HERE / f"bt17_trades_long_warrior_{sfx}.csv", "CONTEXT — long, warrior_strict"),
    ):
        if path.exists():
            summary(with_real_net(pd.read_csv(path)), label)
        else:
            print(f"\n── {label}: {path.name} not found yet ──")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
