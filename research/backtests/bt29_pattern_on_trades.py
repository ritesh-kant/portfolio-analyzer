"""BT29 - does the v3 candlestick detector improve the momentum system's own trades?

Pre-registered in research/hypotheses/2026-09-12-pattern-confirmation-on-past-trades.md
(read it first; the decision rule was fixed before this ran).

BT28 asked whether a pattern predicts anything on its own: it does not. BT29 asks
the CONDITIONAL question the user actually trades on - entries come from the
momentum criteria (gainer, RVOL, setup, trigger), so the only useful role left
for a candlestick is as a filter INSIDE that pool.

Population: the union of every trade this repo has simulated with the momentum
engine (research/backtests/bt17_trades*.csv), de-duplicated on
(date, symbol, setup, entry_time, entry).

Exposure: for each trade, the v3 detector is re-run on that session's 5m bars
with closed_through set to the entry timestamp and to the three 5m bars before
it, so a formation only counts if it had fully closed before the fill. No
look-ahead anywhere.

Outputs
  bt29_trades_scored.csv   one row per trade + the v3 patterns found at entry
  bt29_summary.csv         confirmed vs unconfirmed, overall / per window / per pattern
  bt29_trade_report.html   the visual report (written by --html)

Example:
  apps/signal-engine/.venv/bin/python research/backtests/bt29_pattern_on_trades.py \
      --jobs 8 --html research/backtests/bt29_trade_report.html
"""

from __future__ import annotations

import argparse
import glob
import math
import multiprocessing as mp
import os
import sys
from pathlib import Path
from statistics import NormalDist

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import (  # noqa: E402
    PATTERN_RULES_VERSION,
    STRENGTH_WEAK_BELOW,
    completed_pattern_matches,
)
from src.momentum_trader.engine import resample_5m  # noqa: E402
from src.momentum_trader.pullback import pullback_ordinal  # noqa: E402

CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
TRADE_GLOB = str(ROOT / "research" / "backtests" / "bt17_trades*.csv")
KEY = ["date", "symbol", "setup", "entry_time", "entry"]
LOOKBACK_BARS = 4          # entry bar and the three closed 5m bars before it
REAL_COST_PCT = 0.21       # measured real MIS round trip, in percent
STRESS_COST_PCT = 0.80     # the conservative stress this repo uses elsewhere
WINDOWS = {2022: "2022-23", 2023: "2022-23", 2024: "2024", 2025: "2025", 2026: "2026"}

# The population the operator would actually have traded, and the only one the
# headline numbers should be read from. Two exclusions, both evidence-based:
#
#   * the multi-entry arm (`_me_multi`) was KILLED as a strategy - it takes the
#     2nd..5th re-entry on the same stock the same day - yet it is the biggest
#     single trade file, so pooling every variant silently made a dead arm half
#     the sample;
#   * runs written before 2026-09-06 carry the breakeven-lock defect that leaked
#     into `fixed_2r`. Replaying those exact-breakeven scratches shows 40% would
#     have reached the 2R target, so their gross is understated.
#
# Membership is verified at load time rather than trusted: a file qualifies only
# if it is one trade per symbol-day AND has effectively no breakeven scratches.
MAX_SCRATCH_SHARE = 0.005
EXCLUDED_ARMS = ("_me_multi",)


def is_clean_run(frame: pd.DataFrame, name: str) -> bool:
    """One trade per symbol-day, no breakeven-lock scratches, not a killed arm."""
    if any(tag in name for tag in EXCLUDED_ARMS):
        return False
    if frame.empty:
        return False
    if frame.groupby(["date", "symbol"]).size().max() > 1:
        return False
    scratch = ((frame["exit_reason"] == "trail_stop")
               & (frame["gross_pct"].abs() < 0.005)).mean()
    return bool(scratch <= MAX_SCRATCH_SHARE)


def load_trades() -> tuple[pd.DataFrame, list[str]]:
    """Every simulated momentum trade, de-duplicated, flagged clean / not."""
    frames, clean_files = [], []
    for path in sorted(glob.glob(TRADE_GLOB)):
        name = os.path.basename(path)
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            # a run that produced no trades still writes a header-less file;
            # skip it rather than aborting the whole scan
            continue
        frame["src"] = name
        frame["clean_run"] = is_clean_run(frame, name)
        if frame["clean_run"].iloc[0] if len(frame) else False:
            clean_files.append(name)
        frames.append(frame)
    if not frames:
        raise SystemExit("no bt17_trades*.csv found")
    trades = pd.concat(frames, ignore_index=True)
    # A trade that appears in any clean run keeps that provenance, so the
    # de-duplication cannot demote it just because a dirty file sorts first.
    trades = trades.sort_values("clean_run", ascending=False)
    trades = trades.drop_duplicates(subset=KEY).reset_index(drop=True)
    trades["window"] = trades["year"].map(WINDOWS).fillna("other")
    trades["logged_tags"] = trades["tags"].fillna("")
    return trades, clean_files


def scan_symbol_year(task: tuple[str, int, list[dict]]) -> list[dict]:
    """Re-detect v3 patterns at the entry moment of every trade on one symbol-year."""
    symbol, year, rows = task
    path = CACHE / f"{symbol}_{year}.parquet"
    if not path.exists():
        return [{**row, "scan": "no_cache"} for row in rows]
    try:
        bars = pd.read_parquet(path)
    except Exception:
        return [{**row, "scan": "unreadable_cache"} for row in rows]
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex):
        return [{**row, "scan": "empty_cache"} for row in rows]
    tz = bars.index.tz
    by_day = {str(d.date()): g for d, g in bars.groupby(bars.index.normalize())}
    out: list[dict] = []
    cache5: dict[str, pd.DataFrame] = {}
    for row in rows:
        day = by_day.get(row["date"])
        if day is None or day.empty:
            out.append({**row, "scan": "no_session"})
            continue
        tf5 = cache5.get(row["date"])
        if tf5 is None:
            tf5 = resample_5m(day)
            cache5[row["date"]] = tf5
        if tf5.empty:
            out.append({**row, "scan": "no_5m_bars"})
            continue
        entry_ts = pd.Timestamp(f"{row['date']} {row['entry_time']}", tz=tz)
        # Which pullback of the day's move this entry sits on. Computed from
        # bars that had closed before the fill, so it never looks ahead.
        closed = tf5[tf5.index + pd.Timedelta(minutes=5) <= entry_ts]
        try:
            ordinal = pullback_ordinal(closed) if len(closed) else None
        except Exception:
            ordinal = None
        found: dict[tuple[str, str, str], dict] = {}
        for k in range(LOOKBACK_BARS):
            through = entry_ts - pd.Timedelta(minutes=5 * k)
            for match in completed_pattern_matches(tf5, "5m", closed_through=through):
                key = (match.name, match.start, match.end)
                if key in found:
                    continue
                found[key] = {
                    "name": match.name,
                    "direction": match.direction,
                    "kind": match.kind,
                    "prior_trend": match.prior_trend,
                    "start": match.start,
                    "end": match.end,
                    "bars_ago": k,
                    "confirmation": match.confirmation,
                    "invalidation": match.invalidation,
                    "strength": match.strength,
                }
        hits = sorted(found.values(), key=lambda m: (m["bars_ago"], m["name"]))
        dirs = {m["direction"] for m in hits}
        out.append({
            **row,
            "scan": "ok",
            "v3_tags": "|".join(sorted({m["name"] for m in hits})),
            "v3_n": len(hits),
            "v3_bull": int("bullish" in dirs),
            "v3_bear": int("bearish" in dirs),
            "v3_neutral": int("neutral" in dirs),
            "v3_nearest": hits[0]["name"] if hits else "",
            "v3_nearest_bars_ago": hits[0]["bars_ago"] if hits else -1,
            # Size of the nearest formation, straight from the detector.
            "v3_strength": hits[0]["strength"] if hits else None,
            "v3_weak": (int(hits[0]["strength"] < STRENGTH_WEAK_BELOW)
                        if hits else None),
            "pullback_ord": ordinal,
            "v3_matches": hits,
        })
    return out


def group_tasks(trades: pd.DataFrame) -> list[tuple[str, int, list[dict]]]:
    cols = [*KEY, "year", "window", "src", "clean_run", "exit_reason", "gross_pct",
            "net_pct", "gross_inr", "costs_inr", "net_inr", "qty", "stop", "exit",
            "exit_time", "trigger_time", "day_chg_pct", "rvol", "logged_tags"]
    tasks: dict[tuple[str, int], list[dict]] = {}
    for row in trades[cols].to_dict("records"):
        tasks.setdefault((row["symbol"], int(row["year"])), []).append(row)
    return [(sym, yr, rows) for (sym, yr), rows in sorted(tasks.items())]


# ----------------------------------------------------------------- statistics


def welch(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """Difference of means (a - b) and its Welch t-statistic."""
    a, b = a.dropna(), b.dropna()
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan")
    diff = a.mean() - b.mean()
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return diff, (diff / se if se > 0 else float("nan"))


def net_at(gross_pct: pd.Series, cost_pct: float) -> pd.Series:
    return gross_pct - cost_pct


def contrast(frame: pd.DataFrame, mask: pd.Series, label: str, scope: str) -> dict:
    a, b = frame[mask], frame[~mask]
    diff, t = welch(a["gross_pct"], b["gross_pct"])
    return {
        "scope": scope,
        "split": label,
        "n_yes": len(a),
        "n_no": len(b),
        "gross_yes_pct": a["gross_pct"].mean() if len(a) else float("nan"),
        "gross_no_pct": b["gross_pct"].mean() if len(b) else float("nan"),
        "spread_pp": diff,
        "t_stat": t,
        "win_yes": float((a["gross_pct"] > 0).mean()) if len(a) else float("nan"),
        "win_no": float((b["gross_pct"] > 0).mean()) if len(b) else float("nan"),
        "net_real_yes_pct": (a["gross_pct"] - REAL_COST_PCT).mean() if len(a) else float("nan"),
        "net_real_no_pct": (b["gross_pct"] - REAL_COST_PCT).mean() if len(b) else float("nan"),
    }


def inr_at(frame: pd.DataFrame, cost_pct: float) -> float:
    """Rupee P&L of a subset if every trade paid `cost_pct` per round trip."""
    turnover = frame["qty"] * frame["entry"]
    return float((frame["gross_inr"] - turnover * cost_pct / 100).sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=max(1, (mp.cpu_count() or 2) - 2))
    ap.add_argument("--limit", type=int, default=0, help="first N trades (smoke test)")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "research" / "backtests" / "bt29_trades_scored.csv")
    ap.add_argument("--summary-out", type=Path,
                    default=ROOT / "research" / "backtests" / "bt29_summary.csv")
    args = ap.parse_args()

    trades, clean_files = load_trades()
    if args.limit:
        trades = trades.head(args.limit)
    print(f"BT29 {PATTERN_RULES_VERSION}: {len(trades):,} unique past trades, "
          f"{trades.symbol.nunique()} symbols, years {sorted(trades.year.unique())}")
    print(f"clean runs (1 trade/symbol-day, no breakeven-lock scratches, not the "
          f"killed multi-entry arm): {len(clean_files)}")
    for name in clean_files:
        print(f"    {name}")
    print(f"trades from a clean run: {int(trades.clean_run.sum()):,} of {len(trades):,}")

    tasks = group_tasks(trades)
    results: list[dict] = []
    if args.jobs == 1:
        stream = map(scan_symbol_year, tasks)
    else:
        pool = mp.Pool(args.jobs)
        stream = pool.imap_unordered(scan_symbol_year, tasks, chunksize=2)
    for i, got in enumerate(stream, 1):
        results.extend(got)
        if i % 50 == 0 or i == len(tasks):
            print(f"  [{i}/{len(tasks)}] {len(results):,} trades scored", flush=True)
    if args.jobs != 1:
        pool.close()
        pool.join()

    scored = pd.DataFrame(results)
    if "v3_matches" in scored:
        # per-match detail is rebuilt by bt29_trade_report.py; keep the CSV flat
        scored = scored.drop(columns=["v3_matches"])
    scored.to_csv(args.out, index=False)
    print(f"\nwrote {args.out} ({len(scored):,} rows)")
    print("scan status:", scored["scan"].value_counts().to_dict())

    ok = scored[scored["scan"] == "ok"].copy()
    for col in ("v3_bull", "v3_bear", "v3_neutral", "v3_n"):
        ok[col] = ok[col].fillna(0).astype(int)
    ok["confirmed"] = ok["v3_bull"] == 1
    ok["contradicted"] = (ok["v3_bear"] == 1) & (ok["v3_bull"] == 0)
    ok["logged_any"] = ok["logged_tags"].astype(str).str.len() > 0

    ok["any_v3"] = ok["v3_n"] > 0
    ok["bucket"] = "none"
    ok.loc[ok["v3_neutral"] == 1, "bucket"] = "indecision only"
    ok.loc[ok["v3_bear"] == 1, "bucket"] = "bearish"
    ok.loc[ok["v3_bull"] == 1, "bucket"] = "bullish"
    ok.loc[(ok["v3_bull"] == 1) & (ok["v3_bear"] == 1), "bucket"] = "conflicting"

    print("\nWhat the v3 detector saw at the moment each trade was filled")
    print("(the four 5m bars closed before the fill; long trades only):")
    bucket = ok.groupby("bucket").apply(
        lambda g: pd.Series({
            "n": len(g),
            "share_%": 100 * len(g) / len(ok),
            "gross_%": g["gross_pct"].mean(),
            "net_real_%": g["gross_pct"].mean() - REAL_COST_PCT,
            "win_%": 100 * float((g["gross_pct"] > 0).mean()),
            "net_real_inr": inr_at(g, REAL_COST_PCT),
            "inr_per_trade": inr_at(g, REAL_COST_PCT) / max(len(g), 1),
        }), include_groups=False
    ).round(3)
    print(bucket.to_string())

    print("\nby how long before the fill the formation closed:")
    lag = ok[ok["any_v3"]].groupby("v3_nearest_bars_ago").apply(
        lambda g: pd.Series({"n": len(g), "gross_%": g["gross_pct"].mean(),
                             "win_%": 100 * float((g["gross_pct"] > 0).mean())}),
        include_groups=False
    ).round(3)
    print(lag.to_string())

    rows: list[dict] = []
    rows.append(contrast(ok, ok["confirmed"], "v3 bullish at entry", "all"))
    rows.append(contrast(ok, ok["any_v3"], "any v3 pattern at entry", "all"))
    rows.append(contrast(ok, ok["contradicted"], "v3 bearish only (anti)", "all"))
    rows.append(contrast(ok, ok["logged_any"], "as-logged tags (pre-v3)", "all"))
    for window, grp in ok.groupby("window"):
        rows.append(contrast(grp, grp["confirmed"], "v3 bullish at entry", f"window {window}"))
    for setup, grp in ok.groupby("setup"):
        if len(grp) >= 100:
            rows.append(contrast(grp, grp["confirmed"], "v3 bullish at entry", f"setup {setup}"))

    names = sorted({n for tags in ok["v3_tags"].fillna("") for n in str(tags).split("|") if n})
    for name in names:
        has = ok["v3_tags"].fillna("").str.split("|").apply(lambda xs: name in xs)
        if has.sum() >= 30:
            rows.append(contrast(ok, has, f"pattern {name}", "per-pattern"))

    summary = pd.DataFrame(rows)
    summary.to_csv(args.summary_out, index=False)
    print(f"wrote {args.summary_out}")

    # ---- the clean population, which is what the report and the write-up use
    clean = ok[ok["clean_run"]].copy()
    clean_rows: list[dict] = []
    clean_rows.append(contrast(clean, clean["confirmed"], "v3 bullish at entry", "all"))
    clean_rows.append(contrast(clean, clean["any_v3"], "any v3 pattern at entry", "all"))
    clean_rows.append(
        contrast(clean, clean["contradicted"], "v3 bearish only (anti)", "all"))
    strong = clean[clean["v3_strength"].notna()]
    clean_rows.append(contrast(strong, strong["v3_strength"] >= 1.0,
                               "formation >= 1x recent range", "all"))
    with_ord = clean[clean["pullback_ord"].notna()]
    clean_rows.append(contrast(with_ord, with_ord["pullback_ord"].isin([1, 2]),
                               "pullback ordinal 1-2 vs 3+", "all"))
    for year, grp in clean.groupby("year"):
        clean_rows.append(
            contrast(grp, grp["confirmed"], "v3 bullish at entry", f"window {year}"))
    clean_summary = pd.DataFrame(clean_rows)
    clean_out = ROOT / "research" / "backtests" / "bt29_clean_pool.csv"
    clean_summary_out = ROOT / "research" / "backtests" / "bt29_summary_clean.csv"
    clean.to_csv(clean_out, index=False)
    clean_summary.to_csv(clean_summary_out, index=False)
    print(f"\nclean population: {len(clean):,} trades, {clean.symbol.nunique()} symbols, "
          f"mean gross {clean.gross_pct.mean():+.3f}%, "
          f"{inr_at(clean, REAL_COST_PCT) / max(len(clean), 1):+.0f} INR/trade at real costs")
    print(f"wrote {clean_out} and {clean_summary_out}")
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(clean_summary.round(4).to_string(index=False))
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(summary.round(4).to_string(index=False))

    cells = max(int((summary.scope == "per-pattern").sum()), 1)
    thr = NormalDist().inv_cdf(1 - 0.05 / (2 * cells))
    print(f"\nper-pattern cells: {cells}; Bonferroni(0.05) |t| threshold = {thr:.2f}")

    conf, unconf = ok[ok["confirmed"]], ok[~ok["confirmed"]]
    print("\nrupee P&L of the pool, by cost model (same trades, cost swapped):")
    for label, cost in (("real MIS 0.21%", REAL_COST_PCT), ("stress 0.80%", STRESS_COST_PCT)):
        print(f"  {label:<16} all {inr_at(ok, cost):>12,.0f}   "
              f"confirmed-only {inr_at(conf, cost):>12,.0f}   "
              f"unconfirmed-only {inr_at(unconf, cost):>12,.0f}")
    got = ok[ok["v3_strength"].notna()]
    if len(got):
        weak = float((got["v3_weak"] == 1).mean())
        print(f"\nformation size at the fill: median {got.v3_strength.median():.2f}x the "
              f"ten-candle average range; {100 * weak:.1f}% below the "
              f"{STRENGTH_WEAK_BELOW:g}x display threshold")

    print("\nexit-reason mix:")
    mix = pd.crosstab(ok["confirmed"], ok["exit_reason"], normalize="index")
    print((100 * mix).round(1).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
