# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "pyarrow", "scipy"]
# ///
"""BT52 — resistance checkpoint stop vs the BT50 capped target.

Scores the five locked criteria in
research/hypotheses/2026-09-27-resistance-checkpoint-stop.md at the REAL
itemised MIS cost model, then draws every matched trade whose outcome changed
in the BT32 chart format: the arm's trade (2R target, BUY/SELL, key support /
resistance), the checkpoint and the stop it lifts to, and the control's sell
price as a faint level. The card title carries the paired net change.

Usage (repo root):
  apps/signal-engine/.venv/bin/python research/backtests/bt52_checkpoint_stop_report.py
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

import bt32_strategy_report as bt32  # noqa: E402
from src.news_trader.trailing_sl import calc_costs  # noqa: E402

KEY = ["date", "symbol", "entry_time", "entry"]


def real_net(t: pd.DataFrame) -> pd.Series:
    cost = [calc_costs(r.entry, r.exit, int(r.qty), direction="long")["total"]
            for r in t.itertuples()]
    return t["gross_inr"] - pd.Series(cost, index=t.index)


def score(ctl: pd.DataFrame, arm: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    ctl, arm = ctl.copy(), arm.copy()
    ctl["net_real"], arm["net_real"] = real_net(ctl), real_net(arm)
    m = arm.merge(ctl[KEY + ["exit", "exit_reason", "exit_time", "target", "net_real"]]
                  .rename(columns={"exit": "ctl_exit", "exit_reason": "ctl_exit_reason",
                                   "exit_time": "ctl_exit_time", "target": "ctl_target",
                                   "net_real": "ctl_net_real"}),
                  on=KEY, how="inner", validate="one_to_one")
    m["delta"] = m["net_real"] - m["ctl_net_real"]
    changed = m[(m["exit_reason"] != m["ctl_exit_reason"])
                | ((m["exit"] - m["ctl_exit"]).abs() > 1e-9)].copy()
    total_d = float(arm["net_real"].sum() - ctl["net_real"].sum())
    need = 20.0 * len(ctl)
    t_p = float(stats.ttest_1samp(m["delta"], 0.0).pvalue) if m["delta"].std() > 0 else 1.0
    med = float(changed["delta"].median()) if len(changed) else 0.0
    top = changed["delta"].sort_values(ascending=False)
    drop5 = float(m["delta"].sum() - top[top > 0].head(5).sum())
    cap = float((arm["entry"] * arm["qty"]).sum() / (ctl["entry"] * ctl["qty"]).sum())
    res = {
        "control_n": len(ctl), "arm_n": len(arm), "matched": len(m), "changed": len(changed),
        "control_net": float(ctl["net_real"].sum()), "arm_net": float(arm["net_real"].sum()),
        "control_gross": float(ctl["gross_inr"].sum()), "arm_gross": float(arm["gross_inr"].sum()),
        "total_delta": total_d, "need": need, "paired_mean": float(m["delta"].mean()),
        "paired_p": t_p, "changed_median": med, "drop_top5": drop5, "capital_ratio": cap,
        "arm_win_real": float(100 * (arm["net_real"] > 0).mean()),
        "control_win_real": float(100 * (ctl["net_real"] > 0).mean()),
    }
    res["criteria"] = [
        ("1 total Δ ≥ +₹20/trade", total_d >= need, f"₹{total_d:+,.0f} vs ≥ ₹{need:+,.0f}"),
        ("2 paired p < 0.10", t_p < 0.10, f"p = {t_p:.3f} (mean ₹{res['paired_mean']:+.1f})"),
        ("3 changed median ≥ 0", med >= 0, f"₹{med:+.1f}"),
        ("4 drop top-5 Δ > 0", drop5 > 0, f"₹{drop5:+,.0f}"),
        ("5 capital ratio 0.95–1.05", 0.95 <= cap <= 1.05, f"{cap:.3f}"),
    ]
    ex = arm["exit_reason"].value_counts()
    res["arm_exits"] = ex.to_dict()
    res["control_exits"] = ctl["exit_reason"].value_counts().to_dict()
    cp = arm[arm["exit_reason"] == "checkpoint_stop"]
    res["checkpoint_hit"] = int(arm.get("checkpoint_hit", pd.Series(dtype=int)).sum())
    res["checkpoint_stop_exits"] = len(cp)
    res["checkpoint_gap_fills"] = int((cp["exit"] < cp["checkpoint"] * (1 - 0.0015) - 0.051).sum())
    res["late_2r_wins"] = int(((changed["exit_reason"] == "target")
                               & (changed["ctl_exit_reason"] == "target")).sum())
    return res, changed


def cards_for(changed: pd.DataFrame) -> list[dict]:
    out = []
    for _, row in changed.sort_values(["date", "entry_time"]).iterrows():
        card = bt32.build_day(row)
        if card is None:
            continue
        card["target_label"] = "Target 2R"
        card["setup"] = (f"Δ ₹{row['delta']:+,.0f} vs capped target · control sold "
                         f"{row['ctl_exit_time']} @ {row['ctl_exit']:.2f} "
                         f"({row['ctl_exit_reason']})")
        if pd.notna(row.get("checkpoint")):
            cp = float(row["checkpoint"])
            card["levels"].append({"price": round(cp, 2), "kind": "checkpoint",
                                   "touches": 0, "strength": 0.0, "structural": True,
                                   "side": "resistance", "emphasis": True,
                                   "color": "#fbbf24", "label": "CHECKPOINT (old sell)"})
            card["levels"].append({"price": round(np.floor(cp * 0.9985 / 0.05 + 1e-9) * 0.05, 2),
                                   "kind": "checkpoint stop", "touches": 0, "strength": 0.0,
                                   "structural": True, "side": "support", "emphasis": True,
                                   "color": "#fb923c", "label": "checkpoint stop"})
        card["levels"].append({"price": round(float(row["ctl_exit"]), 2),
                               "kind": "control sell", "touches": 0, "strength": 0.0,
                               "structural": False, "side": "resistance"})
        out.append(card)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", type=Path, nargs="+",
                    default=[HERE / "bt17_trades_ytd2026.csv"])
    ap.add_argument("--arm", type=Path, nargs="+",
                    default=[HERE / "bt17_trades_ytd2026_checkpoint.csv"])
    ap.add_argument("--output", type=Path, default=HERE / "bt52_checkpoint_stop_report.html")
    ap.add_argument("--window", default="2026-01-01 → 2026-09-25",
                    help="label for the report subtitle")
    a = ap.parse_args()
    ctl = pd.concat([pd.read_csv(p) for p in a.control], ignore_index=True)
    arm = pd.concat([pd.read_csv(p) for p in a.arm], ignore_index=True)
    res, changed = score(ctl, arm)
    if ctl["year"].nunique() > 1:
        print("per year (real-cost net):")
        for y in sorted(ctl["year"].unique()):
            r, _ = score(ctl[ctl["year"] == y], arm[arm["year"] == y])
            print(f"  {y}: n {r['control_n']:>4}  changed {r['changed']:>3}  "
                  f"control ₹{r['control_net']:>9,.0f}  arm ₹{r['arm_net']:>9,.0f}  "
                  f"Δ ₹{r['total_delta']:>+8,.0f}  paired ₹{r['paired_mean']:+.1f}/tr "
                  f"p={r['paired_p']:.2f}")

    print(f"control {res['control_n']} trades, arm {res['arm_n']}, matched {res['matched']}, "
          f"changed {res['changed']}")
    print(f"real net: control ₹{res['control_net']:,.0f}  arm ₹{res['arm_net']:,.0f}  "
          f"(gross ₹{res['control_gross']:,.0f} → ₹{res['arm_gross']:,.0f}); "
          f"win% @ real {res['control_win_real']:.1f} → {res['arm_win_real']:.1f}")
    for name, ok, detail in res["criteria"]:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    print(f"checkpoint reached {res['checkpoint_hit']}, checkpoint_stop exits "
          f"{res['checkpoint_stop_exits']} (gap fills below the stop {res['checkpoint_gap_fills']})")
    print("exits control:", res["control_exits"])
    print("exits arm:    ", res["arm_exits"])
    by = changed.groupby(["ctl_exit_reason", "exit_reason"])["delta"].agg(["size", "sum", "mean"])
    print(by.round(1).to_string())

    passed = sum(ok for _, ok, _ in res["criteria"])
    verdict = ("PASS (candidate only — spent window)" if passed == 5
               else f"KILL ({passed}/5)")
    print("VERDICT:", verdict)

    cards = cards_for(changed)
    crit = " · ".join(f"{'✓' if ok else '✗'} {n}: {d}" for n, ok, d in res["criteria"])
    sub = (f"<b>{verdict}.</b> Every trade whose outcome changed when the resistance-capped "
           "target was turned into a stop checkpoint and the target left at 2R. Each card is "
           "the NEW rule's trade; <b>checkpoint</b> = the price the old rule sold at, "
           "<b>checkpoint stop</b> = where the stop lifts once it is reached (from the next "
           "minute), faint <b>control sell</b> = where the old rule actually exited. "
           f"Real-cost net ₹{res['control_net']:,.0f} → ₹{res['arm_net']:,.0f}. {crit}. "
           f"{html.escape(a.window)}, spent window: can kill, cannot bless. "
           "<b>The chips below describe the NEW rule on these changed trades only</b>, "
           "not the change versus control.")
    data = {"summary": bt32.summarise(cards), "trades": cards, "default_tf": "1m",
            "weak_strength": bt32.STRENGTH_WEAK_BELOW, "rules_version": bt32.PATTERN_RULES_VERSION}
    a.output.write_text(bt32.build_html("BT52 — checkpoint stop, changed trades", sub, data))
    print(f"wrote {a.output} ({len(cards)} charts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
