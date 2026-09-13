"""BT31 - does a heavy-volume breakout bar pick better trades?

Pre-registered in research/hypotheses/2026-09-13-volume-surge-entry.md. Read it
first; C1-C4 were fixed before any surge number existed.

The day-level RVOL screen (>= 3.0) is already in the engine and is already known
to be flat-to-inverted. This asks the other question - the one visible on a
chart - whether the BREAKOUT BAR's own volume separates winners from losers
inside the pool we already trade.

Population: BT29's clean pool (one trade per symbol-day, post-breakeven-lock-fix
files only), re-using `is_clean_run` / `load_trades` unchanged so the two studies
speak about the same trades.

Exposure: for each trade, re-open the 1-minute cache for that session and take
`volume_ratio` of the trigger 5m bar and of the entry minute, both computed only
from bars closed before the fill.

Outputs
  bt31_surge_scored.csv   one row per trade + its surge measurements
  bt31_summary.csv        tercile / threshold / window tables

Example:
  uv run research/backtests/bt31_volume_surge.py --jobs 8
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(ROOT / "research" / "backtests"))

from bt29_pattern_on_trades import CACHE, WINDOWS, load_trades  # noqa: E402
from src.momentum_trader.engine import resample_5m  # noqa: E402
from src.momentum_trader.indicators import volume_ratio  # noqa: E402

REAL_COST_PCT = 0.21
STRESS_COST_PCT = 0.80
ANTI_DRAWS = 1000
ANTI_SEED = 20260913
LIFT_BAR_PP = 0.30        # C2, locked
OUT_SCORED = ROOT / "research" / "backtests" / "bt31_surge_scored.csv"
OUT_SUMMARY = ROOT / "research" / "backtests" / "bt31_summary.csv"


def scan_symbol_year(task: tuple[str, int, list[dict]]) -> list[dict]:
    """Measure breakout-bar volume for every trade on one symbol-year."""
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
    cache5: dict[str, pd.DataFrame] = {}
    out: list[dict] = []
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

        trig_ts = pd.Timestamp(f"{row['date']} {row['trigger_time']}", tz=tz)
        entry_ts = pd.Timestamp(f"{row['date']} {row['entry_time']}", tz=tz)

        # The trigger bar is the last 5m bar that had CLOSED at or before the
        # trigger moment - the bar whose high the entry broke. Slicing on
        # close time (index + 5m) keeps the future out.
        closed5 = tf5[tf5.index + pd.Timedelta(minutes=5) <= trig_ts]
        trig_vr = float("nan")
        trig_bar_pos = float("nan")
        if len(closed5) >= 4:
            vr5 = volume_ratio(closed5)
            if len(vr5) and not pd.isna(vr5.iloc[-1]):
                trig_vr = float(vr5.iloc[-1])
            bar = closed5.iloc[-1]
            rng = float(bar["high"]) - float(bar["low"])
            if rng > 0:
                trig_bar_pos = (float(bar["close"]) - float(bar["low"])) / rng

        # A 20-bar lookback cannot exist before ~10:10, which blanks the whole
        # opening hour - 31% of the pool, and the part of the day momentum
        # actually lives in. So measure the same bar a second way, against the
        # session so far, which is defined from the fourth bar onward.
        trig_vr_sess = float("nan")
        if len(closed5) >= 4:
            prior = closed5["volume"].iloc[:-1]
            avg = float(prior.mean())
            if avg > 0:
                trig_vr_sess = float(closed5["volume"].iloc[-1]) / avg

        # One-minute surge, measured on the last minute that CLOSED BEFORE the
        # fill. The entry minute itself is NOT usable: with next_open fills the
        # entry is the open of that minute, so its volume accrues afterwards -
        # knowing it would be look-ahead. `entry_vr_1m` keeps that contaminated
        # version on purpose, to show what the leak is worth.
        pre = day[day.index < entry_ts]
        pre_vr = float("nan")
        if len(pre) >= 4:
            v = volume_ratio(pre)
            if len(v) and not pd.isna(v.iloc[-1]):
                pre_vr = float(v.iloc[-1])
        mins = day[day.index <= entry_ts]
        entry_vr = float("nan")
        if len(mins) >= 4:
            vr1 = volume_ratio(mins)
            if len(vr1) and not pd.isna(vr1.iloc[-1]):
                entry_vr = float(vr1.iloc[-1])

        out.append({**row, "scan": "ok", "trig_vr_5m": trig_vr,
                    "trig_vr_sess": trig_vr_sess, "pre_entry_vr_1m": pre_vr,
                    "entry_vr_1m": entry_vr, "trig_close_pos": trig_bar_pos})
    return out


def anti_strategy(pool: pd.Series, n_pick: int, observed: float) -> tuple[float, float]:
    """How often does a RANDOM subset of the same size do at least as well?"""
    rng = np.random.default_rng(ANTI_SEED)
    values = pool.to_numpy()
    draws = np.empty(ANTI_DRAWS)
    for i in range(ANTI_DRAWS):
        pick = rng.choice(values, size=n_pick, replace=False)
        draws[i] = pick.mean()
    p = float((draws >= observed).mean())
    return p, float(draws.mean())


def bucket_table(frame: pd.DataFrame, col: str, label: str, rows: list[dict]) -> None:
    frame = frame.dropna(subset=[col])
    if len(frame) < 100:
        return
    frame = frame.copy()
    frame["dec"] = pd.qcut(frame[col], 10, labels=False, duplicates="drop")
    print(f"\n{label} deciles")
    print(f"{'dec':>3} {'range':>16} {'n':>6} {'gross%':>9} {'win%':>7} {'net@0.21':>9}")
    for d, g in frame.groupby("dec"):
        print(f"{d:>3} {g[col].min():>7.2f}-{g[col].max():<8.2f} {len(g):>6} "
              f"{g.gross_pct.mean():>+9.4f} {g.win.mean():>6.1%} "
              f"{g.gross_pct.mean() - REAL_COST_PCT:>+9.4f}")
        rows.append({"table": f"{label}_decile", "bucket": int(d),
                     "lo": g[col].min(), "hi": g[col].max(), "n": len(g),
                     "gross_pct": g.gross_pct.mean(), "win_rate": g.win.mean()})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=max(1, mp.cpu_count() - 1))
    ap.add_argument("--rescan", action="store_true", help="ignore the cached scored CSV")
    a = ap.parse_args()

    if OUT_SCORED.exists() and not a.rescan:
        scored = pd.read_csv(OUT_SCORED)
        print(f"reusing {OUT_SCORED.name} ({len(scored)} rows); --rescan to redo")
    else:
        trades, clean_files = load_trades()
        pool = trades[trades["clean_run"]].copy()
        print(f"clean pool n={len(pool)} from {len(clean_files)} files")
        pool["year"] = pool["year"].astype(int)
        tasks: dict[tuple[str, int], list[dict]] = {}
        for row in pool.to_dict("records"):
            tasks.setdefault((row["symbol"], row["year"]), []).append(row)
        items = [(s, y, rows) for (s, y), rows in tasks.items()]
        print(f"scanning {len(items)} symbol-years on {a.jobs} jobs ...")
        if a.jobs > 1:
            with mp.Pool(a.jobs) as p:
                chunks = p.map(scan_symbol_year, items)
        else:
            chunks = [scan_symbol_year(i) for i in items]
        scored = pd.DataFrame([r for c in chunks for r in c])
        scored.to_csv(OUT_SCORED, index=False)
        print(f"wrote {OUT_SCORED.name}")

    print("\nscan status:", scored["scan"].value_counts().to_dict())
    ok = scored[scored["scan"] == "ok"].copy()
    ok["win"] = ok["gross_pct"] > 0
    blind = ok["trig_vr_5m"].isna()
    print(f"\nCOVERAGE: the 20-bar lookback is undefined for {blind.sum()} of "
          f"{len(ok)} trades ({blind.mean():.1%}) - every 09:xx entry plus the "
          f"10:00-10:10 ones.")
    print(f"  blind set   : gross={ok[blind].gross_pct.mean():+.4f}%  "
          f"win={ok[blind].win.mean():.1%}")
    print(f"  measured set: gross={ok[~blind].gross_pct.mean():+.4f}%  "
          f"win={ok[~blind].win.mean():.1%}")
    d = scored[scored["scan"] == "ok"].dropna(subset=["trig_vr_5m", "gross_pct"]).copy()
    d["win"] = d["gross_pct"] > 0
    d["window"] = d["year"].map(WINDOWS).fillna("other")
    print(f"\nmeasured n={len(d)}  gross mean={d.gross_pct.mean():+.4f}%  win={d.win.mean():.1%}")
    print(f"trigger-bar volume ratio: min={d.trig_vr_5m.min():.2f} "
          f"med={d.trig_vr_5m.median():.2f} p90={d.trig_vr_5m.quantile(.9):.2f} "
          f"max={d.trig_vr_5m.max():.2f}")

    rows: list[dict] = []
    bucket_table(d, "trig_vr_5m", "trigger 5m bar volume ratio", rows)
    bucket_table(d, "pre_entry_vr_1m", "1m bar before entry (clean)", rows)
    bucket_table(d, "entry_vr_1m", "entry 1m bar (CONTAMINATED - look-ahead)", rows)

    # ---- C1 dose-response -------------------------------------------------
    rho, p_rho = stats.spearmanr(d["trig_vr_5m"], d["gross_pct"])
    c1 = bool(rho > 0 and p_rho < 0.05)

    # ---- C2 size of the lift, on the PRE-REGISTERED top tercile -----------
    cut = d["trig_vr_5m"].quantile(2 / 3)
    hi, lo = d[d.trig_vr_5m >= cut], d[d.trig_vr_5m < cut]
    spread = hi.gross_pct.mean() - lo.gross_pct.mean()
    c2 = bool(spread >= LIFT_BAR_PP)

    # ---- C3 anti-strategy --------------------------------------------------
    p_anti, anti_mean = anti_strategy(d["gross_pct"], len(hi), hi.gross_pct.mean())
    c3 = bool(p_anti < 0.05)

    # ---- C4 cross-window sign ---------------------------------------------
    win_rows = []
    for w, g in d.groupby("window"):
        gh, gl = g[g.trig_vr_5m >= cut], g[g.trig_vr_5m < cut]
        if len(gh) < 20 or len(gl) < 20:
            continue
        sp = gh.gross_pct.mean() - gl.gross_pct.mean()
        win_rows.append((w, len(gh), sp))
    positive = sum(1 for _, _, sp in win_rows if sp > 0)
    c4 = bool(positive >= 3)

    print(f"\n{'=' * 72}\nPRE-REGISTERED CRITERIA (locked 2026-09-13)\n{'=' * 72}")
    print(f"C1 dose-response   rho={rho:+.4f} p={p_rho:.3f}            "
          f"{'PASS' if c1 else 'FAIL'}  (need rho>0, p<0.05)")
    print(f"C2 lift            top tercile (vr>={cut:.2f}) n={len(hi)} "
          f"gross={hi.gross_pct.mean():+.4f}% vs rest {lo.gross_pct.mean():+.4f}%")
    print(f"                   spread={spread:+.4f} pp                 "
          f"{'PASS' if c2 else 'FAIL'}  (need >= +{LIFT_BAR_PP:.2f} pp)")
    print(f"C3 anti-strategy   p={p_anti:.3f} (random same-size mean {anti_mean:+.4f}%)  "
          f"{'PASS' if c3 else 'FAIL'}  (need p<0.05)")
    print(f"C4 windows         {positive}/{len(win_rows)} positive           "
          f"{'PASS' if c4 else 'FAIL'}  (need >= 3)")
    for w, n, sp in win_rows:
        print(f"     {w:>10}: n_hi={n:>5}  spread={sp:+.4f} pp")

    verdict = "SHIP" if (c1 and c2 and c3 and c4) else "KILL"
    print(f"\nVERDICT: {verdict}  ({sum([c1, c2, c3, c4])} of 4 criteria met)")

    print(f"\n{'-' * 72}\nObservation only - threshold sweep (cannot declare a win)")
    for thr in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        h, l = d[d.trig_vr_5m >= thr], d[d.trig_vr_5m < thr]
        if len(h) < 50 or len(l) < 50:
            continue
        tt = stats.ttest_ind(h.gross_pct, l.gross_pct, equal_var=False)
        print(f"  vr>={thr:>4.1f}: kept n={len(h):>5} ({len(h) / len(d):>5.1%})  "
              f"kept={h.gross_pct.mean():+.4f}%  cut={l.gross_pct.mean():+.4f}%  "
              f"spread={h.gross_pct.mean() - l.gross_pct.mean():+.4f}pp  "
              f"t={tt.statistic:+.2f} p={tt.pvalue:.3f}  "
              f"win {h.win.mean():.1%}v{l.win.mean():.1%}")

    # The session-anchored measure covers the open, so the kill does not rest
    # on a sample that excludes the best hour of the day.
    s = scored[scored["scan"] == "ok"].dropna(subset=["trig_vr_sess", "gross_pct"]).copy()
    s["win"] = s["gross_pct"] > 0
    s["window"] = s["year"].map(WINDOWS).fillna("other")
    print(f"\n{'-' * 72}\nCoverage check - same bar vs the session so far "
          f"(n={len(s)}, {len(s) / len(ok):.0%} of the pool)")
    bucket_table(s, "trig_vr_sess", "trigger bar vs session mean", rows)
    rho_s, p_s = stats.spearmanr(s["trig_vr_sess"], s["gross_pct"])
    cut_s = s["trig_vr_sess"].quantile(2 / 3)
    hs, ls = s[s.trig_vr_sess >= cut_s], s[s.trig_vr_sess < cut_s]
    sp_s = hs.gross_pct.mean() - ls.gross_pct.mean()
    pa_s, _ = anti_strategy(s["gross_pct"], len(hs), hs.gross_pct.mean())
    print(f"  rho={rho_s:+.4f} p={p_s:.3f}   top tercile (>= {cut_s:.2f}) "
          f"spread={sp_s:+.4f} pp   anti p={pa_s:.3f}")
    for w, g in s.groupby("window"):
        gh, gl = g[g.trig_vr_sess >= cut_s], g[g.trig_vr_sess < cut_s]
        if len(gh) >= 20 and len(gl) >= 20:
            print(f"     {w:>10}: n_hi={len(gh):>5}  "
                  f"spread={gh.gross_pct.mean() - gl.gross_pct.mean():+.4f} pp")

    print("\nRupee view at real 0.21% costs (kept set = top tercile):")
    for name, g in (("all", d), ("top tercile", hi), ("rest", lo)):
        net = g.gross_pct - REAL_COST_PCT
        print(f"  {name:>12}: n={len(g):>5}  net/trade={net.mean():+.4f}%  "
              f"stress@0.80={(g.gross_pct - STRESS_COST_PCT).mean():+.4f}%")

    rows.append({"table": "verdict", "bucket": verdict, "n": len(d), "lo": rho,
                 "hi": spread, "gross_pct": p_anti, "win_rate": positive})
    pd.DataFrame(rows).to_csv(OUT_SUMMARY, index=False)
    print(f"\nwrote {OUT_SUMMARY.name}")


if __name__ == "__main__":
    main()
