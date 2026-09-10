# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT23 — volatility-scaled entry floor + indicator confirmation
(hypothesis: research/hypotheses/2026-09-06-volatility-scaled-entry.md §4).

Two engine runs on 2022-2023, 225 symbols, identical in every way except the
day-change floor:

  A  floor 4.0%  (current rule)          bt17_trades_vs_a.csv
  B  floor 2.0%                          bt17_trades_vs_b.csv

and three subsets DERIVED from B, so each is an exact subset and the anti-test
is valid:

  C   Claim 1 (operator): high-vol names enter from 2%, low-vol still need 4%
  C'  Claim 2 (inverse)  : require day_chg_pct >= 1.0 x atr_pct   [EXPLORATORY]
  D   Claim 3            : require macd_hist > 0

Three LOCKED gates, all must pass or the hypothesis is killed. The viability bar
is +0.35% gross/trade, set from the MEASURED real MIS round trip of 0.206% and
frozen in the hypothesis before this ran.

Usage:
  uv run research/backtests/bt23_vol_scaled_entry.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

# --- LOCKED (hypothesis section 4) ---
GROSS_FLOOR = 0.35       # percent gross/trade — viability bar
LIFT_FLOOR = 0.20        # percentage points
ANTI_ALPHA = 0.033       # 0.10 / 3 gated tests (Bonferroni)
MIN_N = 300
SHUFFLES = 5000
SEED = 20260906
REAL_COST_PCT = 0.206    # measured intraday MIS round trip
SIGMA_K = 1.0            # C': day_chg must be >= SIGMA_K x the stock's own ATR%


def _load(name: str) -> pd.DataFrame:
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"bt23: missing {p.name} — run that arm of bt17 first.")
    df = pd.read_csv(p)
    for col in ("atr_pct", "macd_hist"):
        if col not in df.columns:
            raise SystemExit(f"bt23: {p.name} has no {col} column — it predates the "
                             f"volatility-scaled-entry patch. Re-run bt17.")
    return df


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    net = g["net_pct"]
    sharpe = float(net.mean() / net.std() * np.sqrt(252)) if n > 1 and net.std() > 0 else np.nan
    gross = g["gross_pct"]
    return pd.Series({
        "n": n,
        "win%": (g["net_inr"] > 0).mean() * 100,
        "gross%/tr": gross.mean(),
        "net@real%": gross.mean() - REAL_COST_PCT,
        "net%/tr": net.mean(),
        "sharpe": sharpe,
        "target%": (g["exit_reason"] == "target").mean() * 100,
        "stop%": (g["exit_reason"] == "stop").mean() * 100,
    })


def _ci95(x: pd.Series) -> tuple[float, float]:
    n = len(x)
    if n < 2:
        return (np.nan, np.nan)
    half = 1.96 * float(x.std(ddof=1)) / np.sqrt(n)
    m = float(x.mean())
    return (m - half, m + half)


def _v(ok: bool) -> str:
    return "PASS" if ok else "FAIL -> KILL"


def _deciles(df: pd.DataFrame, col: str, label: str) -> None:
    d = df.dropna(subset=[col])
    if len(d) < 50:
        print(f"\n{label}: too few rows ({len(d)}) to decile.")
        return
    q = pd.qcut(d[col], 10, duplicates="drop")
    out = d.groupby(q, observed=True).apply(
        lambda g: pd.Series({"n": len(g), "gross%/tr": g["gross_pct"].mean(),
                             "win%": (g["net_inr"] > 0).mean() * 100}),
        include_groups=False)
    print(f"\n{label}:")
    print(out.to_string())


def run(a_name: str, b_name: str) -> int:
    A, B = _load(a_name), _load(b_name)
    pd.set_option("display.width", 175)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
    rng = np.random.default_rng(SEED)

    months = len(pd.to_datetime(B["date"]).dt.to_period("M").unique())
    print("=" * 78)
    print(f"BT23 — volatility-scaled entry | dev 2022-2023 | {months} months | 225 symbols")
    print(f"   viability bar {GROSS_FLOOR:+.2f}% gross (real MIS cost {REAL_COST_PCT:.3f}%)")
    print("=" * 78)

    # ---- derived subsets (exact subsets of B) ----
    Bv = B.dropna(subset=["atr_pct"])
    med_atr = float(Bv["atr_pct"].median())
    hi = B["atr_pct"] >= med_atr
    C = B[hi | (B["day_chg_pct"] >= 4.0)]
    Cp = B[B["day_chg_pct"] >= SIGMA_K * B["atr_pct"]]
    D = B[B["macd_hist"] > 0]
    band = B[B["day_chg_pct"] < 4.0]

    arms = {"A base(4%)": A, "B wide(2%)": B, "C rule(op)": C,
            "D macd>0": D, "C' sigma[expl]": Cp, "2-4% band": band}
    print(f"\nmedian atr_pct in B = {med_atr:.3f}%  (the high/low-vol split point)")
    print("\narms:")
    print(pd.DataFrame({k: _summ(v) for k, v in arms.items()}).T.to_string())

    for nm, sub in (("C", C), ("C'", Cp), ("D", D)):
        assert set(sub.index).issubset(set(B.index)), f"{nm} is not a subset of B"
    print("\nC, C' and D are exact subsets of B (anti-test precondition holds).")
    print(f"A is NOT compared as a subset — separate engine run "
          f"(A n={len(A)}, B n={len(B)}).")

    gA, gB = A["gross_pct"].mean(), B["gross_pct"].mean()

    # ================= G1 — BAND =================
    lo, hi_ci = _ci95(band["gross_pct"])
    g1_level = bool(band["gross_pct"].mean() >= GROSS_FLOOR)
    g1_ci = bool(lo > 0)
    g1_n = len(band) >= MIN_N
    print("\n" + "=" * 78)
    print("G1 — BAND: does the 2-4% band pay for itself?")
    print("=" * 78)
    print(f"  gross%/trade                : {band['gross_pct'].mean():+.4f}% "
          f"(>= {GROSS_FLOOR:+.2f})   {_v(g1_level)}")
    print(f"  95% CI on that mean         : [{lo:+.4f}, {hi_ci:+.4f}]  "
          f"(lower > 0)      {_v(g1_ci)}")
    print(f"  n                           : {len(band)}          "
          f"(>= {MIN_N})        {_v(g1_n)}")
    G1 = g1_level and g1_ci and g1_n

    # ================= G2 — RULE (Claim 1) =================
    gC = C["gross_pct"].mean()
    liftC = gC - gA
    anti_c = np.nan
    if len(C) and len(B) > len(C):
        k = int(hi.sum())
        dc = B["day_chg_pct"].to_numpy()
        gr = B["gross_pct"].to_numpy()
        n = len(B)
        ge = 0
        for _ in range(SHUFFLES):
            lab = np.zeros(n, dtype=bool)
            lab[rng.choice(n, size=k, replace=False)] = True
            sel = lab | (dc >= 4.0)
            if not sel.any():
                continue
            ge += int(gr[sel].mean() - gA >= liftC)
        anti_c = ge / SHUFFLES
    g2_level, g2_lift = bool(gC >= GROSS_FLOOR), bool(liftC >= LIFT_FLOOR)
    g2_anti, g2_n = bool(anti_c < ANTI_ALPHA), len(C) >= MIN_N
    print("\n" + "=" * 78)
    print("G2 — RULE (Claim 1): high-vol names enter from 2%, low-vol need 4%")
    print("=" * 78)
    print(f"  gross%/trade(C)             : {gC:+.4f}%  (>= {GROSS_FLOOR:+.2f})   "
          f"{_v(g2_level)}")
    print(f"  lift vs A                   : {liftC:+.4f} pp (>= {LIFT_FLOOR:+.2f})   "
          f"{_v(g2_lift)}")
    print(f"  anti p(shuffled vol labels) : {anti_c:.4f}   (< {ANTI_ALPHA})     "
          f"{_v(g2_anti)}")
    print(f"  n(C)                        : {len(C)}          (>= {MIN_N})       "
          f"{_v(g2_n)}")
    G2 = g2_level and g2_lift and g2_anti and g2_n

    # ================= G3 — INDICATOR (Claim 3) =================
    gD = D["gross_pct"].mean()
    liftD = gD - gB
    anti_d = np.nan
    if 0 < len(D) < len(B):
        gr = B["gross_pct"].to_numpy()
        k = len(D)
        ge = 0
        for _ in range(SHUFFLES):
            pick = rng.choice(len(gr), size=k, replace=False)
            ge += int(gr[pick].mean() - gB >= liftD)
        anti_d = ge / SHUFFLES
    g3_level, g3_lift = bool(gD >= GROSS_FLOOR), bool(liftD >= LIFT_FLOOR)
    g3_anti, g3_n = bool(anti_d < ANTI_ALPHA), len(D) >= MIN_N
    print("\n" + "=" * 78)
    print("G3 — INDICATOR (Claim 3): no MACD signal, no trade")
    print("=" * 78)
    print(f"  gross%/trade(D)             : {gD:+.4f}%  (>= {GROSS_FLOOR:+.2f})   "
          f"{_v(g3_level)}")
    print(f"  lift vs B                   : {liftD:+.4f} pp (>= {LIFT_FLOOR:+.2f})   "
          f"{_v(g3_lift)}")
    print(f"  anti p(random subset of B)  : {anti_d:.4f}   (< {ANTI_ALPHA})     "
          f"{_v(g3_anti)}")
    print(f"  n(D)                        : {len(D)}          (>= {MIN_N})       "
          f"{_v(g3_n)}")
    G3 = g3_level and g3_lift and g3_anti and g3_n

    # ================= verdict =================
    print("\n" + "=" * 78)
    print(f"BT23 VERDICT   G1={_v(G1)}   G2={_v(G2)}   G3={_v(G3)}")
    print("=" * 78)
    print("  >>> " + (
        "ALL GATES PASS -> ask the operator before reading the 2025 hold-out"
        if (G1 and G2 and G3) else
        "KILL — per section 5 the floor stays at 4.0% and no indicator entry gate "
        "is added. No threshold may be moved to rescue this."))

    # ================= exploratory, NOT gated =================
    print("\n" + "-" * 78)
    print("EXPLORATORY — reported for mechanism only, CANNOT ship (hypothesis section 4)")
    print("-" * 78)
    gCp = Cp["gross_pct"].mean()
    print(f"  C' (Claim 2, the inverse: day_chg >= {SIGMA_K} x atr_pct)")
    print(f"     n={len(Cp)}  gross={gCp:+.4f}%  vs A {gCp - gA:+.4f} pp  vs B {gCp - gB:+.4f} pp")
    print("     A pass here is NOT a rescue — it is the opposite of the tested claim and")
    print("     would need a fresh window, which means the sealed 2025 hold-out.")

    print("\n  the literal 'momentum or volatility?' sort:")
    _deciles(B, "atr_pct", "  gross by atr_pct decile (VOLATILITY)")
    _deciles(B, "day_chg_pct", "  gross by day_chg_pct decile (MOMENTUM)")
    _deciles(B, "macd_hist", "  gross by macd_hist decile (INDICATOR strength)")

    print("\n  arm B by whole-percent day-change bucket:")
    bkt = B.assign(b=pd.cut(B["day_chg_pct"], [2, 3, 4, 5, 6, 7, 8]))
    print(bkt.groupby("b", observed=True).apply(_summ, include_groups=False).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="bt17_trades_vs_a.csv")
    ap.add_argument("--b", default="bt17_trades_vs_b.csv")
    x = ap.parse_args()
    return run(x.a, x.b)


if __name__ == "__main__":
    sys.exit(main())
