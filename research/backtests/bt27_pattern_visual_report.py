"""BT27 - visual candlestick-pattern audit report.

Runs the working-tree contextual detector (``candles-v3-20260912``) over cached
1-minute bars, resampled to 5m exactly like the live scanner, and writes ONE
self-contained HTML file (no CDN, opens from file://) containing:

  * a dark candlestick chart per session, with every detected formation shaded
    and bracket-labelled in the chart itself
  * per-chart zoom/pan, plus click-to-zoom from the detection list
  * a sortable, filterable detection table: time, symbol, pattern, direction,
    prior trend, and what price actually did afterwards (+15m / +30m / EOD)
  * a per-pattern outcome summary measured against the unconditional return of
    the same bars (the control), so a pattern that fires often but predicts
    nothing is visible as such

The purpose is ERROR-FINDING: look at the marked candles and judge whether the
label is right. Forward returns are shown for context and are NOT a strategy
backtest - there is no stop, target, size, or cost model here. See
``bt26_candlestick_pattern_replay.py`` for the 2R replay and
``bt28_pattern_census.py`` for the statistical census.

Example:
  apps/signal-engine/.venv/bin/python research/backtests/bt27_pattern_visual_report.py \\
      --symbols ABSLAMC,TATAMOTORS --year 2026 --days 12
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader import candles as C  # noqa: E402
from src.momentum_trader.candles import (  # noqa: E402
    BODY_LOOKBACK,
    PATTERN_RULES_VERSION,
    completed_pattern_matches,
)
from src.momentum_trader.engine import resample_5m  # noqa: E402

# Geometry-only helpers, with the prior trend each named rule additionally
# requires. Used ONLY to find near misses: shapes the eye would call a pattern
# that the contextual detector deliberately did not label.
GEOMETRY: dict[str, tuple[int, object, str]] = {
    "hammer": (1, C.is_hammer, "down"),
    "hanging_man": (1, C.is_hanging_man, "up"),
    "inverted_hammer": (1, C.is_inverted_hammer, "down"),
    "shooting_star": (1, C.is_shooting_star, "up"),
    "dragonfly_doji": (1, C.is_dragonfly_doji, "down"),
    "gravestone_doji": (1, C.is_gravestone_doji, "up"),
    "bullish_engulfing": (2, C.is_bullish_engulfing, "down"),
    "bearish_engulfing": (2, C.is_bearish_engulfing, "up"),
    "morning_star": (3, C.is_morning_star, "down"),
    "evening_star": (3, C.is_evening_star, "up"),
    "three_white_soldiers": (3, C.is_three_white_soldiers, "down"),
    "three_black_crows": (3, C.is_three_black_crows, "up"),
    "rising_three": (5, C.is_rising_three, "up"),
    "falling_three": (5, C.is_falling_three, "down"),
}

ASSETS = Path(__file__).resolve().parent / "bt27_assets"
CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
SESSION_END = pd.Timestamp("15:14").time()
MIN_HISTORY = 11
HORIZONS = (1, 3, 6, 12)
ROUND_TRIP_COST_PCT = 0.21


def forward_returns(tf5: pd.DataFrame, entry_idx: int) -> dict[str, float]:
    """Entry at the open of the next bar; exits h bars later, and at the close."""
    out: dict[str, float] = {}
    if entry_idx >= len(tf5):
        return out
    entry = float(tf5["open"].iloc[entry_idx])
    if not entry > 0:
        return out
    out["entry"] = entry
    for h in HORIZONS:
        j = entry_idx + h - 1
        if j < len(tf5):
            out[f"r{h}"] = float(tf5["close"].iloc[j]) / entry - 1.0
    out["r_eod"] = float(tf5["close"].iloc[-1]) / entry - 1.0
    return out


def near_misses(tf5: pd.DataFrame, end: int, emitted: set[str]) -> list[dict]:
    """Shapes that pass the geometry helper but were NOT labelled, with why.

    This is the recall side of the audit: silence from the detector is only
    trustworthy if you can see what it rejected and on what ground.
    """
    out: list[dict] = []
    window = tf5.iloc[:end]
    for name, (size, fn, needs) in GEOMETRY.items():
        if name in emitted or len(window) < BODY_LOOKBACK + size:
            continue
        rows = [window.iloc[-(size - i)] for i in range(size)]
        try:
            if not fn(*rows):
                continue
        except Exception:
            continue
        prior = window.iloc[-(BODY_LOOKBACK + size):-size]
        if len(prior) < BODY_LOOKBACK:
            continue
        trend, _ = C._trend(prior)
        span = window.index[-(BODY_LOOKBACK + size):]
        gapped = bool(((span[1:] - span[:-1]) != pd.Timedelta(minutes=5)).any())
        if gapped:
            reason = "history broken (a 5m bucket was dropped for missing minutes)"
        elif trend != needs:
            reason = f"prior trend was {trend}, this rule needs {needs}"
        else:
            reason = ("failed a size/ratio or position rule (adaptive body/range "
                      "baseline, or body not at the prior candle's extreme)")
        out.append({
            "name": name, "direction": "bullish" if needs == "down" else "bearish",
            "kind": "near_miss", "prior_trend": trend, "needs_trend": needs,
            "reason": reason,
            "start": window.index[-size].isoformat(),
            "end": window.index[-1].isoformat(),
            **forward_returns(tf5, end),
        })
    return out


def scan_day(
    day_1m: pd.DataFrame, want_misses: bool = True
) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Prefix scan: a formation is only discoverable at its own closing bar."""
    day_1m = day_1m[day_1m.index.time <= SESSION_END]
    tf5 = resample_5m(day_1m)
    seen: dict[tuple[str, str, str], dict] = {}
    misses: dict[tuple[str, str, str], dict] = {}
    for end in range(MIN_HISTORY, len(tf5) + 1):
        emitted = set()
        for match in completed_pattern_matches(tf5.iloc[:end], "5m"):
            emitted.add(match.name)
            key = (match.name, match.start, match.end)
            if key in seen:
                continue
            row = asdict(match)
            row.pop("evidence", None)
            row.update(forward_returns(tf5, end))
            seen[key] = row
        if want_misses:
            for miss in near_misses(tf5, end, emitted):
                misses.setdefault((miss["name"], miss["start"], miss["end"]), miss)
    return tf5, list(seen.values()), list(misses.values())


def day_payload(symbol: str, date_str: str, tf5: pd.DataFrame,
                patterns: list[dict], misses: list[dict]) -> dict:
    bars = [
        {
            "t": ts.isoformat(),
            "label": ts.strftime("%H:%M"),
            "o": round(float(r["open"]), 2),
            "h": round(float(r["high"]), 2),
            "l": round(float(r["low"]), 2),
            "c": round(float(r["close"]), 2),
        }
        for ts, r in tf5.iterrows()
    ]
    return {"symbol": symbol, "date": date_str, "tf": "5m", "bars": bars,
            "patterns": patterns, "misses": misses}


def control_moments(tf5: pd.DataFrame) -> list[dict[str, float]]:
    """Unconditional outcome of every bar open the detector could have used."""
    return [forward_returns(tf5, i) for i in range(MIN_HISTORY, len(tf5))]


def summarize(days: list[dict], control: list[dict[str, float]]) -> list[dict]:
    """Directional mean forward return per pattern, minus the same-bar control."""
    base = {
        key: (sum(c[key] for c in control if key in c) /
              max(sum(1 for c in control if key in c), 1))
        for key in ("r3", "r6", "r_eod")
    }
    buckets: dict[str, list[dict]] = {}
    for day in days:
        for p in day["patterns"]:
            buckets.setdefault(p["name"], []).append(p)
    out = []
    for name, items in sorted(buckets.items()):
        direction = items[0].get("direction", "neutral")
        sign = {"bullish": 1.0, "bearish": -1.0}.get(direction, 1.0)
        row: dict[str, object] = {"pattern": name, "direction": direction,
                                  "n": len(items)}
        for key in ("r3", "r6", "r_eod"):
            vals = [sign * p[key] for p in items if key in p]
            row[key] = (sum(vals) / len(vals)) if vals else None
            row[key + "_base"] = sign * base[key]
            row[key + "_edge"] = (row[key] - sign * base[key]) if vals else None
            row[key + "_win"] = (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
        out.append(row)
    out.sort(key=lambda r: (r["direction"] == "neutral", -(r["n"] or 0)))
    return out


def summary_table(summary: list[dict]) -> str:
    head = (
        "<table><thead><tr><th>Pattern</th><th>Dir</th><th class='num'>n</th>"
        "<th class='num'>+15m</th><th class='num'>+30m</th><th class='num'>EOD</th>"
        "<th class='num'>vs control<br>(+30m)</th><th class='num'>win</th>"
        "</tr></thead><tbody>"
    )

    def cell(v: float | None) -> str:
        if v is None:
            return "<td class='num'>–</td>"
        klass = "pos" if v > 0 else "neg" if v < 0 else ""
        return f"<td class='num {klass}'>{v * 100:+.2f}%</td>"

    body = ""
    for row in summary:
        win = row["r6_win"]
        label = html.escape(row["pattern"].replace("_", " "))
        win_cell = (
            f"<td class='num'>{win * 100:.0f}%</td>" if win is not None
            else "<td class='num'>&ndash;</td>"
        )
        body += (
            f"<tr class='{row['direction']}'><td>{label}</td>"
            f"<td class='dir-{row['direction']}'>{row['direction'][:4]}</td>"
            f"<td class='num'>{row['n']}</td>"
            + cell(row["r3"]) + cell(row["r6"]) + cell(row["r_eod"])
            + cell(row["r6_edge"]) + win_cell + "</tr>"
        )
    return head + body + "</tbody></table>"


def build_html(title: str, subtitle: str, chips: str, summary: list[dict],
               data: dict) -> str:
    css = (ASSETS / "report.css").read_text(encoding="utf-8")
    js = (ASSETS / "report.js").read_text(encoding="utf-8")
    note = (
        "Forward returns are measured from the OPEN of the next 5-minute bar "
        "after the formation closes (the first price you could actually get) "
        "and are signed by the pattern's own direction: a bearish pattern "
        "scores positive when price falls. &lsquo;vs control&rsquo; subtracts "
        f"the average of every other eligible bar on the same sessions. One "
        f"real round trip costs about {ROUND_TRIP_COST_PCT:.2f}%, so an edge "
        "smaller than that is not tradable even when it is real."
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
        f"<header>\n<h1>{html.escape(title)}</h1>\n<p>{html.escape(subtitle)}</p>\n"
        f"<p>Detector <b>{html.escape(PATTERN_RULES_VERSION)}</b> · 5-minute bars built with "
        "the live <code>resample_5m</code> (clock-aligned buckets; a bucket missing "
        "any of its five source minutes is dropped) · formations are found by a prefix "
        "scan, so nothing uses a candle that had not closed yet.</p>\n"
        "<p>This page is an <b>audit tool</b>: open a chart, look at the shaded candles, "
        "and decide whether the label is what you would have called by eye. Tick "
        "<b>show near misses</b> to also see shapes that matched the geometry but were "
        "rejected on context - hover one to read exactly why. It is not a strategy "
        "backtest.</p>\n</header>\n"
        f'<div class="stats">\n{chips}\n</div>\n'
        '<div class="wrap">\n<div id="charts"></div>\n<div class="side">\n'
        '<div class="panel"><h3>What happened after each pattern</h3>'
        f'<p class="hint">{note}</p><div class="scroll">{summary_table(summary)}</div></div>\n'
        '<div class="panel"><h3>Detections</h3>'
        '<p class="hint" id="det-count"></p>'
        '<div class="controls">'
        '<label>show <select id="f-dir">'
        '<option value="directional">bullish + bearish</option>'
        '<option value="all">everything (incl. indecision)</option>'
        '<option value="bullish">bullish only</option>'
        '<option value="bearish">bearish only</option>'
        '<option value="neutral">indecision only</option></select></label>'
        '<label>pattern <select id="f-pat"><option value="all">all</option></select></label>'
        '<label><input type="checkbox" id="f-miss"> show near misses</label>'
        '<label><input type="checkbox" id="f-nozoom"> don\'t zoom on click</label>'
        "</div>"
        '<div class="scroll"><table id="det"><thead><tr>'
        '<th data-k="t">Time ⇅</th><th data-k="symbol">Sym ⇅</th>'
        '<th data-k="name">Pattern ⇅</th><th data-k="direction">Dir ⇅</th>'
        '<th data-k="prior">Trend ⇅</th><th class="num" data-k="r3">+15m ⇅</th>'
        '<th class="num" data-k="r6">+30m ⇅</th><th class="num" data-k="reod">EOD ⇅</th>'
        "</tr></thead><tbody></tbody></table></div></div>\n</div>\n</div>\n"
        '<div class="tip" id="tip"></div>\n'
        "<footer>Generated by <code>research/backtests/bt27_pattern_visual_report.py</code>. "
        "Named-pattern definitions and their sources are in "
        "<code>research/specs/candlestick-recognition-v2.md</code>.</footer>\n"
        "<script>\nvar DATA = " + json.dumps(data, separators=(",", ":")) +
        ";\n</script>\n<script>\n" + js + "\n</script>\n</body>\n</html>\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ABSLAMC",
                    help="comma-separated NSE symbols present in the 1m cache")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--days", type=int, default=12,
                    help="most recent N sessions per symbol (0 = all)")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--no-misses", action="store_true",
                    help="skip the geometry-only near-miss (recall) pass")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    days: list[dict] = []
    control: list[dict[str, float]] = []
    counts: Counter = Counter()
    dir_counts: Counter = Counter()

    for symbol in symbols:
        path = CACHE / f"{symbol}_{args.year}.parquet"
        if not path.exists():
            raise SystemExit(f"cache not found: {path}")
        bars = pd.read_parquet(path)
        if bars.empty:
            raise SystemExit(f"{symbol}: cache has no bars")
        groups = [(str(d.date()), g) for d, g in bars.groupby(bars.index.normalize())]
        if args.days > 0:
            groups = groups[-args.days:]
        for date_str, day in groups:
            tf5, patterns, misses = scan_day(day, want_misses=not args.no_misses)
            days.append(day_payload(symbol, date_str, tf5, patterns, misses))
            control.extend(control_moments(tf5))
            for p in patterns:
                counts[p["name"]] += 1
                dir_counts[p.get("direction", "?")] += 1
            print(f".. {symbol} {date_str}: {len(patterns)} detections, "
                  f"{len(misses)} near misses", flush=True)

    days.sort(key=lambda d: (d["date"], d["symbol"]))
    total = sum(len(d["patterns"]) for d in days)
    miss_total = sum(len(d["misses"]) for d in days)
    miss_reasons: Counter = Counter(
        m["reason"] for d in days for m in d["misses"]
    )
    miss_names: Counter = Counter(m["name"] for d in days for m in d["misses"])
    summary = summarize(days, control)

    chips = (
        f'<span class="chip">sessions <b>{len(days)}</b></span>'
        f'<span class="chip">symbols <b>{len(symbols)}</b></span>'
        f'<span class="chip">detections <b>{total}</b></span>'
        + f'<span class="chip bull">bullish <b>{dir_counts.get("bullish", 0)}</b></span>'
        + f'<span class="chip bear">bearish <b>{dir_counts.get("bearish", 0)}</b></span>'
        + f'<span class="chip">indecision <b>{dir_counts.get("neutral", 0)}</b></span>'
        + f'<span class="chip">near misses <b>{miss_total}</b></span>'
        + "".join(
            f'<span class="chip">{html.escape(k.replace("_", " "))} <b>{v}</b></span>'
            for k, v in counts.most_common()
        )
    )
    title = f"Candlestick detector audit — {', '.join(symbols)} {args.year}"
    subtitle = (
        f"{len(days)} sessions ({days[0]['date']} → {days[-1]['date']}) · "
        f"{total} formations · generated "
        f"{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%Y-%m-%d %H:%M IST')}"
    )
    out = args.output or (
        ROOT / "research" / "backtests"
        / f"bt27_{'_'.join(symbols)[:40]}_{args.year}_visual.html"
    )
    out.write_text(build_html(title, subtitle, chips, summary, {"days": days}),
                   encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"\nwrote {out} ({size_kb:,.0f} KB, {total} detections, {len(days)} sessions)")
    for name, n in counts.most_common():
        print(f"  {name:<22} {n}")
    if miss_total:
        print(f"\nnear misses (geometry matched, detector stayed silent): {miss_total}")
        for name, n in miss_names.most_common():
            print(f"  {name:<22} {n}")
        print("  reasons:")
        for reason, n in miss_reasons.most_common():
            print(f"    {n:>5}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
