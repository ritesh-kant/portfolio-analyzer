# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT24 — entry location: round numbers and support/resistance
(hypothesis: research/hypotheses/2026-09-06-entry-location.md §4).

ONE engine run on the FRESH 2026 window (2026-01-01 → 2026-09-04, never read by
anything before this test). Every arm is a derived exact subset of it, so the
anti-test is valid.

  A    base pool, exactly as it runs today
  V    veto stack : macd_hist > 0  AND  not near resistance  AND  not near a
                    0.50-rupee mark
  V+   V  AND  near support

Two LOCKED gates (G1 on V, G2 on V+); alpha 0.05 each (0.10 Bonferroni over 2).
Viability bar +0.35% gross, carried over frozen from BT23.

Usage:
  uv run research/backtests/bt24_entry_location.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

# --- LOCKED (hypothesis §4) ---
GROSS_FLOOR = 0.35       # percent gross/trade
LIFT_FLOOR = 0.20        # percentage points
ANTI_ALPHA = 0.05        # 0.10 / 2 gated tests
MIN_N = 150
SHUFFLES = 5000
SEED = 20260906
REAL_COST_PCT = 0.206
NEAR_PCT = 0.35          # levels.NEAR_PCT — frozen in the codebase

NEEDED = ("dist_to_round_pct", "resist_head_pct", "support_drop_pct", "macd_hist")


def _load(name: str) -> pd.DataFrame:
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"bt24: missing {p.name} — run bt17 on 2026 first.")
    df = pd.read_csv(p)
    missing = [c for c in NEEDED if c not in df.columns]
    if missing:
        raise SystemExit(f"bt24: {p.name} lacks {missing} — it predates the "
                         f"entry-location patch. Re-run bt17.")
    return df


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    net = g["net_pct"]
    sharpe = float(net.mean() / net.std() * np.sqrt(252)) if n > 1 and net.std() > 0 else np.nan
    return pd.Series({
        "n": n,
        "win%": (g["net_inr"] > 0).mean() * 100,
        "gross%/tr": g["gross_pct"].mean(),
        "net@real%": g["gross_pct"].mean() - REAL_COST_PCT,
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
    return (float(x.mean()) - half, float(x.mean()) + half)


def _v(ok: bool) -> str:
    return "PASS" if ok else "FAIL -> KILL"


def _anti(base: pd.DataFrame, k: int, lift: float, rng: np.random.Generator) -> float:
    """p(a random k-subset of `base` lifts gross by >= `lift`)."""
    if not (0 < k < len(base)):
        return float("nan")
    g = base["gross_pct"].to_numpy()
    m = g.mean()
    ge = 0
    for _ in range(SHUFFLES):
        ge += int(g[rng.choice(len(g), size=k, replace=False)].mean() - m >= lift)
    return ge / SHUFFLES


def _gate(name: str, desc: str, arm: pd.DataFrame, base: pd.DataFrame,
          rng: np.random.Generator) -> bool:
    gA = base["gross_pct"].mean()
    g = arm["gross_pct"].mean()
    lift = g - gA
    print("\n" + "=" * 78)
    print(f"{name} — {desc}")
    print("=" * 78)
    if len(arm) < MIN_N:
        print(f"  n = {len(arm)} < {MIN_N} required by §4.")
        print("  >>> NOT TESTABLE on this window. Per the locked rules this may NOT be")
        print("      rescued by loosening a proximity band, dropping a leg, or widening")
        print("      the window — that would be selecting the rule by trade count.")
        return False
    p = _anti(base, len(arm), lift, rng)
    lo, hi = _ci95(arm["gross_pct"])
    ok_l, ok_f, ok_a = bool(g >= GROSS_FLOOR), bool(lift >= LIFT_FLOOR), bool(p < ANTI_ALPHA)
    print(f"  gross%/trade            : {g:+.4f}%  (>= {GROSS_FLOOR:+.2f})  {_v(ok_l)}")
    print(f"  95% CI on that mean     : [{lo:+.4f}, {hi:+.4f}]")
    print(f"  lift vs A               : {lift:+.4f} pp (>= {LIFT_FLOOR:+.2f})  {_v(ok_f)}")
    print(f"  anti p(random subset)   : {p:.4f}   (< {ANTI_ALPHA})      {_v(ok_a)}")
    print(f"  n                       : {len(arm)}         (>= {MIN_N})     PASS")
    return ok_l and ok_f and ok_a


def _deciles(df: pd.DataFrame, col: str, label: str) -> None:
    d = df.dropna(subset=[col])
    if len(d) < 50:
        print(f"\n{label}: only {len(d)} rows with a value — not decile-able.")
        return
    q = pd.qcut(d[col], 10, duplicates="drop")
    out = d.groupby(q, observed=True).apply(
        lambda g: pd.Series({"n": len(g), "gross%/tr": g["gross_pct"].mean(),
                             "win%": (g["net_inr"] > 0).mean() * 100}),
        include_groups=False)
    print(f"\n{label}:")
    print(out.to_string())


def run(a_name: str) -> int:
    A = _load(a_name)
    pd.set_option("display.width", 175)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
    rng = np.random.default_rng(SEED)

    dates = pd.to_datetime(A["date"])
    print("=" * 78)
    print(f"BT24 — entry location | FRESH window {dates.min().date()}..{dates.max().date()}"
          f" | {len(pd.unique(dates.dt.to_period('M')))} months")
    print(f"   viability bar {GROSS_FLOOR:+.2f}% gross (real MIS cost {REAL_COST_PCT:.3f}%)")
    print("   *** first read of 2026 — after this run it is a used window ***")
    print("=" * 78)

    med_round = float(A["dist_to_round_pct"].median())
    # A missing level on a side means nothing was found there, which is NOT
    # "near" — treated as far, and the count is reported so the choice is visible.
    near_round = A["dist_to_round_pct"] < med_round
    near_resist = A["resist_head_pct"].notna() & (A["resist_head_pct"] <= NEAR_PCT)
    near_support = A["support_drop_pct"].notna() & (A["support_drop_pct"] <= NEAR_PCT)
    macd_ok = A["macd_hist"] > 0

    V = A[macd_ok & ~near_resist & ~near_round]
    Vp = A[macd_ok & ~near_resist & ~near_round & near_support]

    print(f"\nmedian dist_to_round_pct = {med_round:.4f}%  (the near/far split)")
    print(f"rows with no resistance found above: {A['resist_head_pct'].isna().sum()}"
          f"   no support below: {A['support_drop_pct'].isna().sum()}"
          f"   no macd (warm-up): {A['macd_hist'].isna().sum()}")
    print(f"near resistance {int(near_resist.sum())} · near a round mark "
          f"{int(near_round.sum())} · near support {int(near_support.sum())} "
          f"· macd>0 {int(macd_ok.sum())}   of {len(A)}")

    arms = {"A base": A, "V veto": V, "V+ veto&support": Vp}
    print("\narms:")
    print(pd.DataFrame({k: _summ(v) for k, v in arms.items()}).T.to_string())
    for nm, sub in (("V", V), ("V+", Vp)):
        assert set(sub.index).issubset(set(A.index)), f"{nm} is not a subset of A"
    print("\nV and V+ are exact subsets of A (anti-test precondition holds).")

    G1 = _gate("G1", "VETO STACK (4.1 + 4.2.1 + indicator)", V, A, rng)
    G2 = _gate("G2", "SUPPORT INCLUSION (4.2.3)", Vp, A, rng)

    print("\n" + "=" * 78)
    print(f"BT24 VERDICT   G1={_v(G1)}   G2={_v(G2)}")
    print("=" * 78)
    print("  >>> " + (
        "A GATE PASSED -> do NOT ship: re-measure with the gate INSIDE the engine "
        "(to capture substitution), then confirm on a window this test did not touch"
        if (G1 or G2) else
        "KILL — per §5 no entry-location gate is added. The recorded fields stay "
        "recorded-only and no threshold may be moved to rescue this."))

    print("\n" + "-" * 78)
    print("EXPLORATORY — single rules and gradients, NOT gates (§3)")
    print("-" * 78)
    gA = A["gross_pct"].mean()
    singles = {
        "round veto only": A[~near_round],
        "resistance veto only": A[~near_resist],
        "near support only": A[near_support],
        "macd>0 only": A[macd_ok],
        "near a round mark (the vetoed half)": A[near_round],
        "near resistance (the vetoed set)": A[near_resist],
    }
    rows = {}
    for k, v in singles.items():
        if len(v):
            rows[k] = pd.Series({"n": len(v), "gross%/tr": v["gross_pct"].mean(),
                                 "vs A (pp)": v["gross_pct"].mean() - gA,
                                 "win%": (v["net_inr"] > 0).mean() * 100})
    print(pd.DataFrame(rows).T.to_string())

    _deciles(A, "dist_to_round_pct", "gross by distance-to-round-mark decile (4.1)")
    _deciles(A, "round_head_pct", "gross by head-to-next-mark decile (directional reading)")
    _deciles(A, "resist_head_pct", "gross by clear-air-above decile (4.2.1)")
    _deciles(A, "support_drop_pct", "gross by drop-to-support decile (4.2.3)")

    print("\nby setup (base):")
    print(A.groupby("setup").apply(_summ, include_groups=False)
          .sort_values("n", ascending=False).to_string())
    print("\nby month (base) — is 2026 a different regime?")
    print(A.assign(m=dates.dt.to_period("M").astype(str))
          .groupby("m").apply(_summ, include_groups=False).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="bt17_trades_loc26.csv")
    return run(ap.parse_args().a)


if __name__ == "__main__":
    sys.exit(main())
