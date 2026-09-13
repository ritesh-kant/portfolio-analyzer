"""BT29 visual report - the momentum system's own past trades, with the v3
candlestick formations marked in the chart itself.

Reads ``bt29_trades_scored.csv`` (written by bt29_pattern_on_trades.py), picks a
stratified sample of real past trades, rebuilds each session's 5-minute chart
from the cached 1-minute bars, re-runs the v3 detector over the session with a
prefix scan, and writes ONE self-contained HTML file (no CDN, opens from
file://) containing:

  * one dark candlestick chart per trade, with BUY / STOP / EXIT levels, the
    holding period shaded, and every formation shaded + bracket-labelled
  * a trade table: timestamp, symbol, setup, what v3 saw, exit reason, gross %,
    net rupees at the real 0.21% MIS round trip. Clicking a row jumps the chart
    to that minute.
  * the headline result of the conditional test, so the picture and the number
    are on the same page.

Example:
  apps/signal-engine/.venv/bin/python research/backtests/bt29_trade_report.py \
      --max-charts 240 --output research/backtests/bt29_trade_report.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import (  # noqa: E402
    PATTERN_RULES_VERSION,
    STRENGTH_WEAK_BELOW,
    completed_pattern_matches,
)
from src.momentum_trader.engine import resample_5m  # noqa: E402

CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
ASSETS = Path(__file__).resolve().parent / "bt29_assets"
REAL_COST_PCT = 0.21
MIN_HISTORY = 11


def scan_session(tf5: pd.DataFrame) -> list[dict]:
    """Every formation in the session, found by prefix scan (no look-ahead)."""
    seen: dict[tuple[str, str, str], dict] = {}
    for end in range(MIN_HISTORY, len(tf5) + 1):
        for match in completed_pattern_matches(tf5.iloc[:end], "5m"):
            key = (match.name, match.start, match.end)
            if key in seen:
                continue
            seen[key] = {
                "name": match.name, "direction": match.direction, "kind": match.kind,
                "prior_trend": match.prior_trend, "start": match.start, "end": match.end,
                "confirmation": round(match.confirmation, 2),
                "invalidation": round(match.invalidation, 2),
                # Descriptive size, straight from the detector (candles.py).
                "strength": match.strength,
                "weak": bool(match.strength < STRENGTH_WEAK_BELOW),
            }
    return list(seen.values())


def bar_fraction(tf5: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Where a 1-minute timestamp sits on the 5-minute x axis, as bar index."""
    if tf5.empty:
        return None
    floor = ts.floor("5min")
    pos = tf5.index.get_indexer([floor])[0]
    if pos < 0:
        if ts < tf5.index[0]:
            return 0.0
        if ts > tf5.index[-1]:
            return float(len(tf5))
        pos = int(tf5.index.searchsorted(floor)) - 1
        if pos < 0:
            return 0.0
    return float(pos) + (ts - floor).total_seconds() / 300.0


def pick(scored: pd.DataFrame, limit: int, seed: int) -> pd.DataFrame:
    """Stratified, deterministic: oversample the trades the test is about.

    `sample(random_state=seed)` draws by row POSITION, and a `--jobs N` scan
    writes `bt29_trades_scored.csv` in worker-completion order, so without the
    sort below the same seed on the same data picks a different ~18% of the
    charts every run — which makes two reports impossible to compare.
    """
    scored = scored.sort_values(["date", "symbol", "entry_time"]).reset_index(drop=True)
    quota = [("bullish", int(limit * 0.50)), ("bearish", int(limit * 0.22)),
             ("indecision only", int(limit * 0.16)), ("none", int(limit * 0.12))]
    parts = []
    for bucket, want in quota:
        grp = scored[scored["bucket"] == bucket]
        if grp.empty:
            continue
        parts.append(grp.sample(n=min(want, len(grp)), random_state=seed))
    out = pd.concat(parts).drop_duplicates(subset=["date", "symbol", "entry_time"])
    return out.sort_values(["date", "symbol", "entry_time"]).reset_index(drop=True)


def build_payload(rows: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    cache: dict[tuple[str, int], pd.DataFrame] = {}
    for _, r in rows.iterrows():
        key = (r["symbol"], int(r["year"]))
        bars = cache.get(key)
        if bars is None:
            path = CACHE / f"{r['symbol']}_{int(r['year'])}.parquet"
            if not path.exists():
                continue
            bars = pd.read_parquet(path)
            cache[key] = bars
        day = bars[bars.index.normalize() == pd.Timestamp(r["date"], tz=bars.index.tz)]
        if day.empty:
            continue
        tf5 = resample_5m(day)
        if tf5.empty:
            continue
        entry_ts = pd.Timestamp(f"{r['date']} {r['entry_time']}", tz=bars.index.tz)
        exit_ts = pd.Timestamp(f"{r['date']} {r['exit_time']}", tz=bars.index.tz)
        entry_x, exit_x = bar_fraction(tf5, entry_ts), bar_fraction(tf5, exit_ts)
        patterns = scan_session(tf5)
        entry_bar = int(entry_x) if entry_x is not None else 0
        index = {ts.isoformat(): i for i, ts in enumerate(tf5.index)}
        for p in patterns:
            p["bars_ago"] = entry_bar - index.get(p["end"], entry_bar)
            p["pre"] = bool(p["bars_ago"] >= 0)
        turnover = float(r["qty"]) * float(r["entry"])
        net_real = float(r["gross_inr"]) - turnover * REAL_COST_PCT / 100
        out.append({
            "symbol": r["symbol"], "date": r["date"], "tf": "5m",
            "bars": [
                {"t": ts.isoformat(), "label": ts.strftime("%H:%M"),
                 "o": round(float(b["open"]), 2), "h": round(float(b["high"]), 2),
                 "l": round(float(b["low"]), 2), "c": round(float(b["close"]), 2)}
                for ts, b in tf5.iterrows()
            ],
            "patterns": patterns,
            "trade": {
                "setup": r["setup"], "entry_ts": entry_ts.isoformat(),
                "exit_ts": exit_ts.isoformat(), "entry": round(float(r["entry"]), 2),
                "stop": round(float(r["stop"]), 2), "exit": round(float(r["exit"]), 2),
                "qty": int(r["qty"]), "exit_reason": r["exit_reason"],
                "gross_pct": round(float(r["gross_pct"]), 3),
                "net_real_pct": round(float(r["gross_pct"]) - REAL_COST_PCT, 3),
                "net_real_inr": round(net_real, 0),
                "v3_tags": r["v3_tags"] if isinstance(r["v3_tags"], str) else "",
                "bucket": r["bucket"],
                "pullback_ord": (int(r["pullback_ord"])
                                 if pd.notna(r.get("pullback_ord")) else None),
                # Size of the nearest formation, as the detector reported it.
                "strength": (round(float(r["v3_strength"]), 2)
                             if pd.notna(r.get("v3_strength")) else None),
                "entry_x": entry_x, "exit_x": exit_x,
            },
        })
    return out


def headline_table(summary: pd.DataFrame) -> str:
    keep = summary[summary["scope"].isin(["all"]) |
                   summary["scope"].str.startswith("window")]
    head = ("<table><thead><tr><th>Split</th><th>Scope</th><th class='num'>trades<br>with</th>"
            "<th class='num'>gross<br>with</th><th class='num'>gross<br>without</th>"
            "<th class='num'>spread</th><th class='num'>t</th></tr></thead><tbody>")
    body = ""
    for _, r in keep.iterrows():
        spread = r["spread_pp"]
        klass = "pos" if spread > 0 else "neg" if spread < 0 else ""
        body += (
            f"<tr><td>{html.escape(str(r['split']))}</td>"
            f"<td>{html.escape(str(r['scope']))}</td>"
            f"<td class='num'>{int(r['n_yes'])}</td>"
            f"<td class='num'>{r['gross_yes_pct']:+.3f}%</td>"
            f"<td class='num'>{r['gross_no_pct']:+.3f}%</td>"
            f"<td class='num {klass}'>{spread:+.3f} pp</td>"
            f"<td class='num'>{r['t_stat']:+.2f}</td></tr>"
        )
    return head + body + "</tbody></table>"


def build_html(title: str, subtitle: str, chips: str, verdict: str,
               table: str, data: dict) -> str:
    css = (ASSETS / "report.css").read_text(encoding="utf-8")
    js = (ASSETS / "report.js").read_text(encoding="utf-8")
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
        f"<header>\n<h1>{html.escape(title)}</h1>\n<p>{html.escape(subtitle)}</p>\n"
        f"<p>Detector <b>{html.escape(PATTERN_RULES_VERSION)}</b> · 5-minute bars built with the "
        "live <code>resample_5m</code> · formations found by a prefix scan, so nothing on a chart "
        "uses a candle that had not closed yet. Every card is a <b>real past trade of the momentum "
        "engine</b>, entered on its own criteria (gainer, RVOL, setup, trigger) — the candlestick "
        "is only asked whether it would have been a useful extra filter.</p>\n"
        f"<p>{verdict}</p>\n</header>\n"
        f'<div class="stats">\n{chips}\n</div>\n'
        '<div class="wrap">\n<div id="charts"></div>\n<div class="side">\n'
        '<div class="panel"><h3>Did a v3 pattern separate the winners?</h3>'
        '<p class="hint">Measured on all 11,813 past trades, not just the charted sample. '
        '&ldquo;Spread&rdquo; is gross return with the pattern minus gross return without it, '
        'in percentage points. The pre-registered bar to ship it as a live filter was '
        '+0.30 pp with t &ge; 2.0.</p>'
        f'<div class="scroll">{table}</div></div>\n'
        '<div class="panel"><h3>Charted trades</h3>'
        '<p class="hint" id="det-count"></p>'
        '<div class="controls">'
        '<label>trades <select id="f-bucket">'
        '<option value="all">all</option><option value="bullish">v3 bullish at entry</option>'
        '<option value="bearish">v3 bearish at entry</option>'
        '<option value="indecision only">indecision only</option>'
        '<option value="none">no pattern</option></select></label>'
        '<label>outcome <select id="f-out"><option value="all">all</option>'
        '<option value="win">winners</option><option value="loss">losers</option>'
        "</select></label>"
        '<label>pullback <select id="f-ord"><option value="all">any ordinal</option>'
        '<option value="12">1st or 2nd only</option>'
        '<option value="3">3rd or later</option></select></label>'
        '<label>formation size <select id="f-size">'
        '<option value="all">any size</option>'
        '<option value="big">&ge; 1&times; recent range</option>'
        '<option value="small">&lt; 1&times; recent range</option></select></label>'
        '<label>marks <select id="f-dir">'
        '<option value="directional">bullish + bearish</option>'
        '<option value="all">everything (incl. indecision)</option>'
        '<option value="bullish">bullish only</option>'
        '<option value="bearish">bearish only</option></select></label>'
        '<label>pattern <select id="f-pat"><option value="all">all</option></select></label>'
        '<label><input type="checkbox" id="f-pre"> only formations that closed '
        'before the fill</label>'
        '<label><input type="checkbox" id="f-nozoom"> don\'t zoom on click</label>'
        "</div>"
        '<div class="scroll"><table id="det"><thead><tr>'
        '<th data-k="t">Time ⇅</th><th data-k="symbol">Sym ⇅</th>'
        '<th data-k="setup">Setup ⇅</th><th class="num" data-k="ord">pb# ⇅</th>'
        '<th data-k="pat">v3 saw ⇅</th><th class="num" data-k="size">size ⇅</th>'
        '<th data-k="reason">Exit ⇅</th><th class="num" data-k="gross">gross ⇅</th>'
        '<th class="num" data-k="net">net ₹ ⇅</th>'
        "</tr></thead><tbody></tbody></table></div></div>\n</div>\n</div>\n"
        '<div class="tip" id="tip"></div>\n'
        "<footer>Generated by <code>research/backtests/bt29_trade_report.py</code>. "
        "Pre-registration: "
        "<code>research/hypotheses/2026-09-12-pattern-confirmation-on-past-trades.md</code>. "
        "Pattern definitions and their sources: "
        "<code>research/specs/candlestick-recognition-v2.md</code>.</footer>\n"
        "<script>\nvar DATA = " + json.dumps(data, separators=(",", ":")) +
        ";\n</script>\n<script>\n" + js + "\n</script>\n</body>\n</html>\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path,
                    default=ROOT / "research" / "backtests" / "bt29_clean_pool.csv")
    ap.add_argument("--summary", type=Path,
                    default=ROOT / "research" / "backtests" / "bt29_summary_clean.csv")
    ap.add_argument("--max-charts", type=int, default=240)
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--output", type=Path,
                    default=ROOT / "research" / "backtests" / "bt29_trade_report.html")
    args = ap.parse_args()

    scored = pd.read_csv(args.scored)
    if "scan" in scored.columns:
        scored = scored[scored["scan"] == "ok"]
    scored = scored.copy()
    for col in ("v3_bull", "v3_bear", "v3_neutral"):
        scored[col] = scored[col].fillna(0).astype(int)
    scored["bucket"] = "none"
    scored.loc[scored["v3_neutral"] == 1, "bucket"] = "indecision only"
    scored.loc[scored["v3_bear"] == 1, "bucket"] = "bearish"
    scored.loc[scored["v3_bull"] == 1, "bucket"] = "bullish"
    turnover = scored["qty"] * scored["entry"]
    scored["net_real_inr"] = scored["gross_inr"] - turnover * REAL_COST_PCT / 100

    summary = pd.read_csv(args.summary)
    sample = pick(scored, args.max_charts, args.seed)
    print(f"charting {len(sample)} of {len(scored):,} trades "
          f"({sample['bucket'].value_counts().to_dict()})")
    payload = build_payload(sample)
    print(f"built {len(payload)} charts")

    conf = scored[scored["bucket"] == "bullish"]
    spread = float(summary.loc[(summary.scope == "all") &
                               (summary.split == "v3 bullish at entry"), "spread_pp"].iloc[0])
    tstat = float(summary.loc[(summary.scope == "all") &
                              (summary.split == "v3 bullish at entry"), "t_stat"].iloc[0])
    verdict = (
        f"<b>Result: it does not help.</b> Across {len(scored):,} past trades, the "
        f"{len(conf):,} where a bullish v3 formation had closed in the four 5-minute bars "
        f"before the fill returned <b>{spread:+.3f} percentage points</b> in gross terms "
        f"versus the rest (t = {tstat:+.2f}). The pre-registered bar was +0.30 pp with "
        "t &ge; 2.0, so the pattern is <b>not</b> being added to the entry rules. Use this "
        "page to check that the labels themselves are right — that part holds up.<br>"
        "<b>Population:</b> one trade per symbol-day only, and only runs made after the "
        "breakeven-lock defect was fixed on 2026-09-06. The multi-entry arm (2nd, 3rd, 5th "
        "re-entry on the same stock) is excluded — it was killed as a strategy and its "
        "trades are not ones you would take. Each card shows the <b>pullback ordinal</b> and "
        "how big the nearest formation was relative to the recent average range. A "
        f"formation below <b>{STRENGTH_WEAK_BELOW:g}\u00d7</b> that range is drawn faint: "
        "the name is correct, the candle is too small to act on."
    )
    chips = (
        f'<span class="chip">past trades <b>{len(scored):,}</b></span>'
        f'<span class="chip">symbols <b>{scored.symbol.nunique()}</b></span>'
        f'<span class="chip">years <b>{scored.year.min()}–{scored.year.max()}</b></span>'
        f'<span class="chip bull">v3 bullish at entry <b>{len(conf):,}</b></span>'
        f'<span class="chip bear">v3 bearish at entry '
        f'<b>{(scored.bucket == "bearish").sum():,}</b></span>'
        f'<span class="chip">indecision only '
        f'<b>{(scored.bucket == "indecision only").sum():,}</b></span>'
        f'<span class="chip">no pattern <b>{(scored.bucket == "none").sum():,}</b></span>'
        f'<span class="chip neg">pool net at real costs '
        f'<b>−₹{abs(scored.net_real_inr.sum()):,.0f}</b></span>'
        f'<span class="chip">charts on this page <b>{len(payload)}</b></span>'
    )
    title = "Do candlestick patterns improve my momentum trades?"
    subtitle = (
        f"BT29 · {len(scored):,} past momentum-engine trades, {scored.year.min()}–"
        f"{scored.year.max()} · {len(payload)} charted · generated "
        f"{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%Y-%m-%d %H:%M IST')}"
    )
    args.output.write_text(
        build_html(title, subtitle, chips, verdict, headline_table(summary),
                   {"trades": payload}),
        encoding="utf-8")
    print(f"wrote {args.output} ({args.output.stat().st_size / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
