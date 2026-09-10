# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas"]
# ///
"""Evaluate the registered strict max-move checklist once on the 2025 window."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
MIN_TRADES = 30
GROSS_FLOOR = 0.35
LIFT_FLOOR = 0.20
ANTI_ALPHA = 0.05
DSR_FLOOR = 0.95
SHUFFLES = 5000
SEED = 20260907
STRESS_PER_SIDE = 0.004


def _load(name: str) -> pd.DataFrame:
    frame = pd.read_csv(HERE / name)
    if frame.empty:
        return frame
    frame["key"] = (frame["date"].astype(str) + "|" + frame["symbol"].astype(str) + "|"
                    + frame["setup"].astype(str) + "|" + frame["trigger_time"].astype(str))
    frame["real_net_inr"] = (frame["gross_inr"] - (
        frame["costs_inr"] - STRESS_PER_SIDE * (frame["entry"] + frame["exit"]) * frame["qty"]
    ))
    return frame


def _normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _single_plan_dsr(gross_pct: pd.Series) -> float:
    """Deflated Sharpe probability for one pre-specified trial against zero."""
    x = gross_pct.to_numpy(dtype=float) / 100.0
    n = len(x)
    if n < 3 or float(np.std(x, ddof=1)) == 0.0:
        return float("nan")
    sr = float(np.mean(x) / np.std(x, ddof=1) * np.sqrt(252.0))
    skew = float(pd.Series(x).skew())
    kurt = float(pd.Series(x).kurt() + 3.0)
    denom = math.sqrt(max(1.0 - skew * sr + (kurt - 1.0) * sr * sr / 4.0, 1e-12))
    return _normal_cdf(sr * math.sqrt(n - 1.0) / denom)


def _summary(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {"n": 0.0, "gross": float("nan"), "real_net": 0.0, "gross_inr": 0.0,
                "win": float("nan"), "dsr": float("nan")}
    return {
        "n": float(len(frame)),
        "gross": float(frame["gross_pct"].mean()),
        "real_net": float(frame["real_net_inr"].sum()),
        "gross_inr": float(frame["gross_inr"].sum()),
        "win": float((frame["real_net_inr"] > 0).mean() * 100.0),
        "dsr": _single_plan_dsr(frame["gross_pct"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", default="bt17_trades_mm25_control.csv")
    ap.add_argument("--strict", default="bt17_trades_mm25_strict.csv")
    a = ap.parse_args()
    control, strict = _load(a.control), _load(a.strict)
    cs, ss = _summary(control), _summary(strict)
    subset = set(strict["key"]).issubset(set(control["key"]))
    lift = ss["gross"] - cs["gross"] if len(strict) and len(control) else float("nan")
    anti_p = float("nan")
    if 0 < len(strict) < len(control) and subset:
        rng = np.random.default_rng(SEED)
        base = control["gross_pct"].to_numpy()
        observed = lift
        draws = np.array([rng.choice(base, size=len(strict), replace=False).mean() - base.mean()
                          for _ in range(SHUFFLES)])
        anti_p = float((draws >= observed).mean())

    print("=" * 76)
    print("BT25 - strict max-move checklist | 2025 validation")
    print("=" * 76)
    print("\n                     control        strict")
    print(f"trades               {int(cs['n']):7d}        {int(ss['n']):7d}")
    print(f"gross / trade        {cs['gross']:+7.3f}%       {ss['gross']:+7.3f}%")
    print(f"gross total          Rs {cs['gross_inr']:+8.0f}    Rs {ss['gross_inr']:+8.0f}")
    print(f"real net total       Rs {cs['real_net']:+8.0f}    Rs {ss['real_net']:+8.0f}")
    print(f"real-cost win rate   {cs['win']:7.1f}%       {ss['win']:7.1f}%")
    print(f"single-plan DSR      {cs['dsr']:7.3f}       {ss['dsr']:7.3f}")
    print(f"\nstrict is exact control subset: {subset}")
    print(f"gross lift: {lift:+.3f} pp")
    print(f"anti p(random subset >= strict lift): {anti_p:.4f}")

    checks = {
        "enough trades": len(strict) >= MIN_TRADES,
        "gross return": bool(ss["gross"] >= GROSS_FLOOR),
        "real total profit": bool(ss["real_net"] > 0.0),
        "lift over control": bool(lift >= LIFT_FLOOR),
        "anti-test": bool(anti_p < ANTI_ALPHA),
        "DSR": bool(ss["dsr"] >= DSR_FLOOR),
    }
    print("\nlocked checks:")
    for name, passed in checks.items():
        print(f"  {name:20s} {'PASS' if passed else 'FAIL'}")
    print("\nVERDICT: " + ("PASS" if all(checks.values()) else "KILL"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
