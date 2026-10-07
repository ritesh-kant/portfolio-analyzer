"""Headline numbers for a set of trades, and how a run compares with the base trade.

Every number is NET AT REAL COSTS unless it says "stress". The mean is never
shown without the median beside it, and the mean without the top five winners
sits next to both: on a near-zero-drift strategy a handful of trades can carry a
whole result (2026-09-20: 5 trades were 44% of a "+₹20/trade" arm).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else float(x)


def summarize(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"n": 0}
    tr = tr.sort_values(["date", "entry_time", "symbol"]).reset_index(drop=True)
    net = tr["net_real_inr"].to_numpy(float)
    wins, losses = net[net > 0], net[net < 0]
    cum = np.cumsum(net)
    dd = float((cum - np.maximum.accumulate(np.maximum(cum, 0))).min())
    top5 = np.sort(net)[::-1][:5]
    deployed = float(tr["notional_inr"].sum())
    step = max(1, len(cum) // 300)
    idx = list(range(0, len(cum), step))
    if idx[-1] != len(cum) - 1:
        idx.append(len(cum) - 1)
    curve = [round(float(cum[i]), 1) for i in idx]
    by_year = [{"year": int(y), "n": int(len(g)), "net_real_inr": round(float(g["net_real_inr"].sum()), 0),
                "mean_inr": round(float(g["net_real_inr"].mean()), 1),
                "win_pct": round(100.0 * float((g["net_real_inr"] > 0).mean()), 1)}
               for y, g in tr.groupby("year")]
    return {
        "n": int(len(tr)), "symbols": int(tr["symbol"].nunique()), "days": int(tr["date"].nunique()),
        "win_pct": round(100.0 * float((net > 0).mean()), 1),
        "gross_pct_mean": round(float(tr["gross_pct"].mean()), 4),
        "net_real_pct_mean": round(float(tr["net_real_pct"].mean()), 4),
        "net_stress_pct_mean": round(float(tr["net_pct"].mean()), 4),
        "net_real_inr": round(float(net.sum()), 0),
        "net_stress_inr": round(float(tr["net_inr"].sum()), 0),
        "gross_inr": round(float(tr["gross_inr"].sum()), 0),
        "costs_inr": round(float(tr["costs_inr"].sum()), 0),
        "mean_inr": round(float(net.mean()), 2), "median_inr": round(float(np.median(net)), 2),
        "mean_ex_top5_inr": round(float(np.sort(net)[:-5].mean()), 2) if len(net) > 5 else None,
        "top5_share_pct": round(100.0 * float(top5.sum()) / float(net.sum()), 0)
                          if net.sum() > 0 and len(net) > 5 else None,
        "profit_factor": _f(round(float(wins.sum() / -losses.sum()), 2)) if losses.sum() < 0 else None,
        "avg_win_inr": round(float(wins.mean()), 1) if len(wins) else 0.0,
        "avg_loss_inr": round(float(losses.mean()), 1) if len(losses) else 0.0,
        "max_drawdown_inr": round(dd, 0),
        "deployed_inr": round(deployed, 0),
        "return_on_deployed_pct": round(100.0 * float(net.sum()) / deployed, 3) if deployed else None,
        "mean_r": round(float(tr["r_multiple"].mean()), 3),
        "target_hit_pct": round(100.0 * float((tr["exit_reason"] == "target").mean()), 1),
        "stop_pct": round(100.0 * float(tr["exit_reason"].str.contains("stop").mean()), 1),
        "exit_mix": {k: int(v) for k, v in tr["exit_reason"].value_counts().items()},
        "stop_source_mix": {k: int(v) for k, v in tr["stop_source"].value_counts().items()},
        "by_year": by_year, "curve": curve,
    }


def _norm_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def _welch(a: np.ndarray, b: np.ndarray) -> tuple[float, float] | None:
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    if va + vb <= 0:
        return None
    z = (a.mean() - b.mean()) / math.sqrt(va + vb)
    return float(z), _norm_p(z)


def compare(base: pd.DataFrame, run: pd.DataFrame) -> dict:
    """What the indicators did relative to the base trade, with the honest caveats.

    * `kept_vs_removed`: base trades the run still has vs base trades it dropped
      (did the filter keep the better trades?). Welch z, normal approximation.
    * `paired`: for trades both runs took, the per-trade change in net ₹ (did the
      exits help the same trades?). Paired z over those trades.
    * The base trades are keyed by (date, symbol, entry_time).
    """
    out: dict = {}
    if base.empty or run.empty:
        return out
    key = lambda d: d["date"].astype(str) + "|" + d["symbol"] + "|" + d["entry_time"]  # noqa: E731
    b = base.assign(_k=key(base)).set_index("_k")
    r = run.assign(_k=key(run)).set_index("_k")
    common = b.index.intersection(r.index)
    removed = b.index.difference(r.index)
    added = r.index.difference(b.index)
    out["overlap"] = {"base_n": int(len(b)), "run_n": int(len(r)), "same": int(len(common)),
                      "dropped": int(len(removed)), "new": int(len(added))}
    if len(removed):
        rem = b.loc[removed, "net_real_inr"].to_numpy(float)
        kept = b.loc[common, "net_real_inr"].to_numpy(float)
        out["removed"] = {"n": int(len(rem)), "mean_inr": round(float(rem.mean()), 2),
                          "median_inr": round(float(np.median(rem)), 2),
                          "net_real_inr": round(float(rem.sum()), 0)}
        w = _welch(kept, rem)
        if w:
            out["kept_vs_removed"] = {"z": round(w[0], 2), "p": round(w[1], 3)}
    if len(common) >= 5:
        d = (r.loc[common, "net_real_inr"] - b.loc[common, "net_real_inr"]).to_numpy(float)
        sd = d.std(ddof=1)
        if sd > 0 and np.any(d != 0):
            z = d.mean() / (sd / math.sqrt(len(d)))
            out["paired"] = {"n": int(len(d)), "mean_change_inr": round(float(d.mean()), 2),
                             "median_change_inr": round(float(np.median(d)), 2),
                             "changed": int((d != 0).sum()), "z": round(float(z), 2),
                             "p": round(_norm_p(float(z)), 3)}
    return out
