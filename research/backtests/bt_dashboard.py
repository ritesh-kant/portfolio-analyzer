"""Backtest dashboard — every backtest run in one place, click one to see every
trade drawn on its own candle chart (the BT32 report format), group trades by
exit reason / target hit.

    pnpm bt:dashboard
    # then open http://localhost:8898/

What it serves
  /                          the list of backtest runs, newest first (BT number, when it
                             ran, data window, trades, gross / net per trade, win %,
                             target-hit %, exit mix); every column but "backtest" sorts
  /run/<run>                 that run's report: summary chips, a Group-by control,
                             filters, a sortable trade table, one chart per trade
  /run/<run>?group=exit_reason   same, opened already grouped
  /api/run/<run>/trade/<i>   one trade's bars + as-of-entry levels + formations
  /files/<name>.html         the static reports already built in this folder

A "run" is any `*.csv` in this folder with the bt17 trade columns (date, symbol,
entry/exit time + price, stop, qty, exit_reason, gross/net). The run list and its
numbers come straight from the CSVs, so a new `pnpm bt --tag foo` shows up on
refresh with no registration. NAMING a run (BT number, title, hypothesis) is done
in `dashboard_runs.json`, not in Python; `pnpm bt:dashboard --unlabelled` lists
runs nobody has named, and trade CSVs the dashboard cannot chart at all.

Charts are built ON CLICK, not up front. One trade costs ~0.9 s and ~33 KB of
bars/levels/formations, so a 1,800-trade run built eagerly is ~25 minutes and a
60 MB page. Here a report opens instantly (it ships only the CSV-derived row per
trade), each card fetches its chart when it nears the viewport, and the result
is cached on disk in `.dashboard_cache/` keyed by the CSV's mtime, so the second
look is instant. Charts need the 1-minute bar cache (`.cache_upstox/1m/`); a trade
whose symbol/year is not cached shows a "no chart" note but still counts in every
number.

Honesty notes (same as BT32): levels are recomputed as of the entry bar only;
costs are shown twice (bt17's +40 bps/side stress and the realistic itemised
MIS model); a report is DESCRIPTIVE of an already-measured window, not a new
test. "Target hit" means the exit WAS the target fill (exit_reason == "target").
"""

from __future__ import annotations

import argparse
import fnmatch
import html
import json
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bt32_strategy_report as bt32  # noqa: E402

CACHE_DIR = HERE / ".dashboard_cache"
INDEX_CACHE = CACHE_DIR / "index.json"
CACHE_VERSION = 3   # bump when summary_fields / the stats below change shape
REGISTRY = HERE / "dashboard_runs.json"

REQUIRED = {"date", "symbol", "entry_time", "exit_time", "entry", "stop", "exit",
            "exit_reason", "qty", "gross_pct", "net_pct", "gross_inr", "net_inr"}

# Which BT a HISTORIC run belongs to. Ordered; first match wins. Evidence for each line
# is the analysis script that reads that tag (default tag / prefix) or the package.json
# command that writes it. NEW runs are named in dashboard_runs.json instead, which wins
# over this list. A run no rule matches is listed as "BT17 · <tag>": bt17
# (`bt17_momentum_pool.py`) is the engine every tagged run replays.
# (pattern on the CSV stem, bt, title)
RULES: list[tuple[str, str, str]] = [
    (r"^bt17_trades_ytd2026_sig5m$", "Live rule", "Five-minute signal exits · 2026 YTD"),
    (r"^bt17_trades_ytd2026_checkpoint$", "BT52", "Checkpoint stop · 2026 YTD"),
    (r"^bt17_trades_ytd2026(_.+)?$", "Live rule", "warrior_strict live rule · 2026 YTD{v}"),
    (r"^bt17_trades_3y_live$", "Live rule", "warrior_strict live rule · 3 years (2025 held out)"),
    (r"^bt17_trades_3y_(\d{4})_ctl$", "BT52", "Checkpoint stop · control · {1}"),
    (r"^bt17_trades_3y_(\d{4})_cp$", "BT52", "Checkpoint stop · checkpoint arm · {1}"),
    (r"^bt17_trades_bt53_(\d{4})_\d+$", "BT53", "Peak-hours cutoff · arm · {1}"),
    (r"^bt17_trades_bt50_(\d{2})_(ctl|new)$", "BT50", "Session-resistance target · {2} · 20{1}"),
    (r"^bt17_trades_struct(\d{2})_(control|structural)$", "BT47",
     "Structural support/resistance exit · {2} · 20{1}"),
    (r"^bt17_trades_macdtol_.+$", "BT48", "MACD 'open' tolerance · {rest}"),
    (r"^bt17_trades_mm25_(control|strict)$", "BT25", "Max-move checklist · {1}"),
    (r"^bt17_trades_cs(\d{2})_(base|cost)$", "BT43", "Cost-aware stop · {2} · 20{1}"),
    (r"^bt17_trades_coststop23_.+$", "BT43", "Cost-aware stop · earlier run · {rest}"),
    (r"^bt17_trades_cs(\d{2})_(half|pyr)$", "BT44", "Add to winner · {2} · 20{1}"),
    (r"^bt17_trades_b[ef](\d{2})_(ctl|r15)$", "BT46", "Breakeven at 1.5R · {2} · 20{1}"),
    (r"^bt17_trades_pv_w\d_(on|off)$", "BT41", "Price/volume gate · {rest}"),
    (r"^bt17_trades_shelf_(on|off)$", "BT38", "Volume-shelf levels · {1}"),
    (r"^bt17_trades_fb26h1_.+$", "BT45", "Two-close false break · {rest}"),
    (r"^bt17_trades_fb24_.+$", "BT42", "False break · 2024 · {rest}"),
    (r"^bt17_trades_fb_.+$", "BT42", "False break · {rest}"),
    (r"^bt17_trades_(lat|rest|r2)_.+$", "BT36", "Entry fill model · {rest}"),
    (r"^bt17_trades_v\d_.+$", "BT36", "Entry fill model · {rest}"),
    (r"^bt17_trades_qs_.+$", "BT22", "Quality selectivity · {rest}"),
    (r"^bt17_trades_me_.+$", "BT21", "Multi-entry · {rest}"),
    (r"^bt17_trades_pbord$", "BT20", "Pullback ordinal"),
    (r"^bt17_trades_vs_.+$", "BT23", "Volatility-scaled entry · {rest}"),
    (r"^bt17_trades_loc26$", "BT24", "Entry location · 2026"),
    (r"^bt17_trades_res_.+$", "BT17 · resistance", "Resistance veto · {rest}"),
    (r"^bt17_trades_(trend_full|trend_min|fixed_2r|smoke_(trend_full|trend_min|fixed_2r))$",
     "BT17 · exit modes", "Exit mode · {rest}"),
    (r"^bt17_trades_1ma_(on|off)$", "BT30", "One-minute agreement · {1}"),
    (r"^bt17_trades_demo15$", "BT32", "Demo · 15 mid-caps · 2024"),
    (r"^bt17_trades$", "BT17", "Momentum pool · 2024-25 (default run)"),
    (r"^bt29_clean_pool$", "BT29", "Candlestick filter · clean pool"),
    (r"^bt29_trades_scored$", "BT29", "Candlestick filter · every past trade, scored"),
    (r"^bt17_trades_(.+)$", "BT17", "{1}"),
    (r"^bt31_.+$", "BT31", "Volume surge (scored)"),
    (r"^bt35_.+$", "BT35", "Absorption base / failed breakdown"),
    (r"^bt45_two_close_continuation(_2026h1)?$", "BT45", "Two-close continuation audit"),
]
BT_ORDER_RE = re.compile(r"^BT(\d+)")

_registry_cache: tuple[float, dict] = (-1.0, {})


def registry() -> dict:
    """`dashboard_runs.json`: {"<csv stem or glob>": {"bt","title","hypothesis"}}.

    This is where a NEW run gets its BT number and title — no Python edit. It
    wins over RULES. A key may be a glob (`bt17_trades_bt56_*`); `{rest}` in the
    title is whatever the `*` matched. Keys starting with `_` are bookkeeping.
    Read fresh whenever the file changes.
    """
    global _registry_cache
    if not REGISTRY.exists():
        return {}
    m = REGISTRY.stat().st_mtime
    if m != _registry_cache[0]:
        try:
            data = json.loads(REGISTRY.read_text())
        except Exception as e:
            print(f"dashboard_runs.json is not valid JSON ({e}); ignoring it")
            data = {}
        _registry_cache = (m, {k: v for k, v in data.items() if not k.startswith("_")})
    return _registry_cache[1]


def from_registry(stem: str) -> dict | None:
    reg = registry()
    if stem in reg:
        return {**reg[stem], "_rest": stem}
    for key, val in reg.items():
        if "*" in key and fnmatch.fnmatchcase(stem, key):
            return {**val, "_rest": stem[len(key.split("*")[0]):]}
    return None


def classify(stem: str) -> tuple[str, str]:
    for pat, bt, title in RULES:
        m = re.match(pat, stem)
        if not m:
            continue
        groups = [m.group(0)] + [g or "" for g in m.groups()]
        rest = re.sub(r"^bt\d+_trades_", "", stem)
        v = ("" if len(groups) < 2 or not groups[1] else " · " + groups[1].strip("_"))
        out = title.replace("{rest}", rest).replace("{v}", v)
        out = re.sub(r"\{(\d)\}", lambda k: groups[int(k.group(1))] if int(k.group(1)) < len(groups)
                     else "", out)
        return bt, out.replace("_", " ").strip()
    return "other", stem


def label(stem: str) -> tuple[str, str, bool, str]:
    """(bt, title, labelled, hypothesis). `labelled` is False when only the catch-all
    rule matched — the run is listed, but nobody said what it is."""
    hit = from_registry(stem)
    if hit:
        title = str(hit.get("title") or stem).replace("{rest}", hit["_rest"])
        return str(hit.get("bt") or "other"), title, True, str(hit.get("hypothesis") or "")
    bt, title = classify(stem)
    generic = (bool(re.fullmatch(r"bt17_trades_.+", stem))
               and title == stem.replace("bt17_trades_", "").replace("_", " "))
    return bt, title, bt != "other" and not generic, ""


def bt_sort_key(bt: str) -> tuple[int, int, str]:
    """The live rule first (what is deployed), then BT numbers ascending, then the rest."""
    if bt == "Live rule":
        return (-1, 0, bt)
    m = BT_ORDER_RE.match(bt)
    if m:
        return (0, int(m.group(1)), bt)
    return (1, 0, bt)


# ---------------------------------------------------------------- discovery

def _bar_files() -> set[str]:
    names = {p.name for p in bt32.CACHE.glob("*.parquet")} if bt32.CACHE.exists() else set()
    # US cache files are <SYMBOL>_<conId>_<year>.parquet; list them as <SYMBOL>_<year>.parquet
    # so the same lookup answers for both markets.
    if bt32.CACHE_US.exists():
        for p in bt32.CACHE_US.glob("*.parquet"):
            sym, _, year = p.stem.rsplit("_", 2)
            names.add(f"{sym}_{year}.parquet")
    return names


def read_run(path: Path) -> pd.DataFrame | None:
    try:
        head = pd.read_csv(path, nrows=0)
    except Exception:
        return None
    if REQUIRED - set(head.columns):
        return None
    df = pd.read_csv(path)
    return df if not df.empty else None


def summary_rows(df: pd.DataFrame) -> list[dict]:
    """The CSV-derived row for every trade, in CSV order. The row's list index is
    the trade id used by /api/run/<run>/trade/<i>."""
    return [bt32.summary_fields(r) for _, r in df.iterrows()]


def run_stats(path: Path, df: pd.DataFrame, rows: list[dict], bars: set[str]) -> dict:
    s = bt32.summarise(rows)
    reasons: dict[str, int] = {}
    for r in rows:
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
    have = sum(1 for r in rows if f'{r["symbol"]}_{r["date"][:4]}.parquet' in bars)
    stem = path.stem
    bt, title, labelled, hypothesis = label(stem)
    st = path.stat()
    return {
        "run": stem, "file": path.name, "bt": bt, "title": title,
        "labelled": labelled, "hypothesis": hypothesis,
        "start": min(r["date"] for r in rows), "end": max(r["date"] for r in rows),
        "currency": s["currency"], "n": s["n"], "symbols": s["symbols"], "days": s["days"],
        "gross_pct": s["gross_pct"], "net_real_pct": s["net_real_pct"],
        "net_stress_pct": s["net_stress_pct"], "win_real": s["win_real"],
        "net_real_inr": s["net_real_inr"], "gross_inr": s["gross_inr"],
        "target_hit_pct": round(100.0 * reasons.get("target", 0) / s["n"], 1),
        "target_hit_n": reasons.get("target", 0),
        "exit_mix": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "bars_pct": round(100.0 * have / s["n"], 1),
        "mtime": st.st_mtime, "size": st.st_size,
    }


def scan(force: bool = False) -> list[dict]:
    """Every compatible run, with its headline numbers. Statistics are cached by
    (mtime, size) so a restart only re-reads CSVs that changed."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache: dict = {}
    if INDEX_CACHE.exists() and not force:
        try:
            cache = json.loads(INDEX_CACHE.read_text())
            if cache.get("version") != CACHE_VERSION:
                cache = {}
        except Exception:
            cache = {}
    old = cache.get("runs", {})
    bars = _bar_files()
    runs: dict[str, dict] = {}
    for path in sorted(HERE.glob("*.csv")):
        st = path.stat()
        hit = old.get(path.stem)
        if hit and hit["mtime"] == st.st_mtime and hit["size"] == st.st_size:
            # labels come from the registry/RULES, not the cache, so renaming needs no rescan
            hit["bt"], hit["title"], hit["labelled"], hit["hypothesis"] = label(path.stem)
            runs[path.stem] = hit
            continue
        df = read_run(path)
        if df is None:
            continue
        try:
            rows = summary_rows(df)
        except Exception as e:  # a CSV with the columns but unusable values
            print(f"skip {path.name}: {e}")
            continue
        runs[path.stem] = run_stats(path, df, rows, bars)
    if cache.get("runs") != runs or cache.get("version") != CACHE_VERSION:
        INDEX_CACHE.write_text(json.dumps({"version": CACHE_VERSION, "runs": runs}))
    return sorted(runs.values(), key=lambda r: (bt_sort_key(r["bt"]), r["start"], r["run"]))


def static_reports() -> list[dict]:
    """HTML reports already built in this folder (other BTs' bespoke reports)."""
    out = []
    for p in sorted(HERE.glob("*.html")):
        head = p.read_text(errors="ignore")[:4000]
        m = re.search(r"<title>(.*?)</title>", head, re.S)
        out.append({"file": p.name, "title": html.unescape(m.group(1)).strip() if m else p.stem,
                    "mb": round(p.stat().st_size / 1e6, 1)})
    return out


# ------------------------------------------------------------------ reports

class Runs:
    """Lazy, thread-safe holder for the run list and per-run summary rows."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.runs: dict[str, dict] = {}
        self.rows: dict[str, tuple[float, list[dict], pd.DataFrame]] = {}
        self.build_locks: dict[tuple[str, int], threading.Lock] = {}
        self.registry_mtime = 0.0
        self.refresh()

    def refresh(self, force: bool = False) -> list[dict]:
        listing = scan(force)
        with self.lock:
            self.runs = {r["run"]: r for r in listing}
            self.registry_mtime = REGISTRY.stat().st_mtime if REGISTRY.exists() else 0.0
        return listing

    def ensure(self, run: str) -> None:
        """Re-scan when `run` is unknown (a CSV written after the server started — the
        link an agent hands you is /run/<new-stem>, not the index), when its CSV changed
        (a re-run), or when dashboard_runs.json changed (a new label)."""
        meta = self.runs.get(run)
        path = HERE / f"{run}.csv"
        reg = REGISTRY.stat().st_mtime if REGISTRY.exists() else 0.0
        stale = (meta is None and path.exists()
                 or meta is not None and (not path.exists() or path.stat().st_mtime != meta["mtime"])
                 or reg != self.registry_mtime)
        if stale:
            self.refresh()

    def load(self, run: str) -> tuple[list[dict], pd.DataFrame] | None:
        meta = self.runs.get(run)
        if meta is None:
            return None
        with self.lock:
            hit = self.rows.get(run)
            if hit and hit[0] == meta["mtime"]:
                return hit[1], hit[2]
        df = read_run(HERE / meta["file"])
        if df is None:
            return None
        rows = summary_rows(df)
        with self.lock:
            self.rows[run] = (meta["mtime"], rows, df)
        return rows, df

    def detail(self, run: str, i: int) -> dict | None:
        """One trade's chart payload, from disk cache or built now (and cached)."""
        meta = self.runs.get(run)
        got = self.load(run)
        if meta is None or got is None:
            return None
        rows, df = got
        if not 0 <= i < len(df):
            return None
        d = CACHE_DIR / f"{run}-{int(meta['mtime'])}"
        f = d / f"{i}.json"
        if f.exists():
            return json.loads(f.read_text())
        with self.lock:
            lk = self.build_locks.setdefault((run, i), threading.Lock())
        with lk:  # two cards asking for the same trade build it once
            if f.exists():
                return json.loads(f.read_text())
            built = bt32.build_detail(df.iloc[i])
            if built is None:
                return {"_missing": True}
            d.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(built))
            tmp.replace(f)
            return built


def report_page(runs: Runs, run: str, group: str) -> str | None:
    got = runs.load(run)
    meta = runs.runs.get(run)
    if got is None or meta is None:
        return None
    rows, _ = got
    data = {
        "trades": rows, "summary": bt32.summarise(rows),
        "weak_strength": bt32.STRENGTH_WEAK_BELOW, "rules_version": bt32.PATTERN_RULES_VERSION,
        "lazy": True, "detail_url": f"/api/run/{run}/trade/",
        "back": {"href": "/", "text": "← All backtests"},
    }
    if group:
        data["default_group"] = group
    sub = (
        f"{meta['n']} trades from <code>{html.escape(meta['file'])}</code>, "
        f"{meta['start']} → {meta['end']}. "
        "Each chart is built from the cached 1-minute bars when you scroll to it (first view of a "
        "trade takes about a second; it is cached afterwards) and resampled to the 5-minute frame "
        "the engine decides on. Support/resistance is recomputed <b>as of the entry bar only</b>. "
        "Costs are shown twice: bt17's stressed number and a realistic one recomputed per trade "
        "with the itemised MIS model the engine books exits with. "
        "<b>Group by</b> (side panel) splits the trades by exit reason, target hit, outcome, "
        "symbol, setup, month, weekday or entry hour; <b>target hit</b> means the exit was the "
        "target fill. Descriptive report of an already-measured window — not a new test."
    )
    if meta.get("hypothesis"):
        sub += f" Hypothesis / write-up: <code>{html.escape(meta['hypothesis'])}</code>."
    if meta["bars_pct"] < 100:
        sub += (f" <b>{100 - meta['bars_pct']:.0f}% of this run's trades have no cached 1-minute "
                "bars, so those show no chart; they still count in every number.</b>")
    title = f"{meta['bt']} — {meta['title']}"
    return bt32.build_html(title, sub, data)


# ---------------------------------------------------------------- index page

INDEX_CSS = """
:root{--bg:#0b1017;--panel:#121a25;--line:#1e2b3c;--ink:#e7eef8;--dim:#8fa1bb;--up:#2dd4bf;--dn:#fb7185;--acc:#fbbf24}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:22px 26px 6px}
h1{margin:0 0 4px;font-size:20px}
header p{margin:4px 0;color:var(--dim);max-width:1100px}
.chips{display:flex;flex-wrap:wrap;gap:7px;padding:8px 26px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:4px 11px;font-size:11.5px;color:var(--dim)}
.chip b{color:var(--ink)}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:10px 26px}
.bar input,.bar select{background:#0f1620;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font-size:12.5px}
.bar input{width:280px}
.bar label{color:var(--dim);font-size:12px;display:flex;gap:6px;align-items:center}
main{padding:0 26px 50px}
.bt{margin:18px 0 6px;display:flex;gap:12px;align-items:baseline}
.bt h2{margin:0;font-size:15px;color:var(--acc)}
.bt span{color:var(--dim);font-size:11.5px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);font-size:12.5px;white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:11.5px;user-select:none;background:#0f1620}
th.sortable{cursor:pointer}
th.sortable:hover{color:var(--ink)}
th.sorted{color:var(--acc)}
.sorthint{opacity:.45;font-size:10px}
td.ran{font-variant-numeric:tabular-nums}
td.ran small{display:block;color:var(--dim);font-size:11px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr{cursor:pointer}
tbody tr:hover{background:#16202e}
tbody tr:last-child td{border-bottom:0}
.pos{color:var(--up)}.neg{color:var(--dn)}
.name{font-weight:600;white-space:normal;min-width:240px}
.name small{display:block;color:var(--dim);font-weight:400;font-size:11px}
.mix{display:flex;height:9px;width:150px;border-radius:3px;overflow:hidden;background:#0f1620}
.mix i{display:block;height:100%}
.tag{font-size:10.5px;border:1px solid var(--line);border-radius:5px;padding:0 5px;color:var(--dim);margin-left:6px}
.tag.warn{color:var(--acc);border-color:var(--acc)}
.statics{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.statics a{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 11px;color:var(--ink);text-decoration:none;font-size:12px;max-width:430px}
.statics a:hover{border-color:var(--acc)}
.statics small{color:var(--dim)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;padding:0 26px 4px;color:var(--dim);font-size:11.5px}
.legend span{display:inline-flex;gap:5px;align-items:center}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.empty{color:var(--dim);padding:30px}
"""

INDEX_JS = r"""
var RUNS = DATA.runs, REASON_COL = {
  target:"#34d399", stop:"#fb7185", checkpoint_stop:"#f472b6", trail_stop:"#fb923c",
  ema9_break:"#a78bfa", macd_fade:"#60a5fa", volume_climax:"#fbbf24", support_break:"#f87171",
  time_stop:"#94a3b8", false_break:"#e879f9", resistance_reject:"#22d3ee", eod:"#64748b"
}, EXTRA = ["#2dd4bf","#c084fc","#facc15","#38bdf8","#fda4af","#86efac","#fdba74","#a5b4fc"];
var colCache = {}, ex = 0;
function col(k){ if(REASON_COL[k]) return REASON_COL[k]; if(!colCache[k]) colCache[k]=EXTRA[ex++%EXTRA.length]; return colCache[k]; }
function nice(s){ return String(s).split("_").join(" "); }
function pct(x){ return (x>=0?"+":"")+x.toFixed(3)+"%"; }
function inr(x, cur){ return cur==="USD" ? (x<0?"−":"")+"$"+Math.abs(x).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})
  : (x<0?"−":"")+"₹"+Math.abs(Math.round(x)).toLocaleString("en-IN"); }
function cls(x){ return x>0?"pos":x<0?"neg":""; }
function esc(s){ return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];}); }
var sortKey = "mtime", sortDir = -1;   // newest run first
var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function ran(ts){
  var d = new Date(ts*1000), p = function(n){ return String(n).padStart(2,"0"); };
  var days = Math.floor((Date.now()-d.getTime())/864e5);
  var ago = days<=0 ? "today" : days===1 ? "yesterday" : days+" days ago";
  return d.getDate()+" "+MONTHS[d.getMonth()]+" "+d.getFullYear()+", "+p(d.getHours())+":"+p(d.getMinutes())+"<small>"+ago+"</small>";
}
var COLS = [
  // [sort key, heading, class, tooltip]; "backtest" is the one column that does not sort
  ["title","backtest","",""],["mtime","ran","","When the run's CSV was written"],
  ["start","data window","","First trade date"],["n","trades","num",""],
  ["gross_pct","gross / trade","num",""],["net_real_pct","net / trade @ real","num",""],
  ["win_real","win %","num",""],["target_hit_pct","target hit","num","Share of trades that exited at the target"],
  ["net_real_inr","net ₹","num",""],
  ["exit_mix","exit mix","","Sorts by the biggest single exit reason's share of the trades"]
];
function sortable(k){ return k !== "title"; }
function topShare(r){ return Math.max.apply(null, Object.keys(r.exit_mix).map(function(k){ return r.exit_mix[k]; })) / r.n; }
function mixBar(r){
  var tot = r.n, tip = Object.keys(r.exit_mix).map(function(k){return nice(k)+" "+r.exit_mix[k]+" ("+(100*r.exit_mix[k]/tot).toFixed(0)+"%)";}).join("\n");
  var h = "<div class='mix' title='"+esc(tip)+"'>";
  Object.keys(r.exit_mix).forEach(function(k){ h += "<i style='width:"+(100*r.exit_mix[k]/tot)+"%;background:"+col(k)+"'></i>"; });
  return h+"</div>";
}
function filtered(){
  var q = document.getElementById("q").value.toLowerCase().trim();
  var bt = document.getElementById("btsel").value;
  var min = +document.getElementById("minn").value || 0;
  return RUNS.filter(function(r){
    if (bt && r.bt !== bt) return false;
    if (r.n < min) return false;
    return !q || (r.bt+" "+r.title+" "+r.run+" "+r.start+" "+r.end).toLowerCase().indexOf(q) >= 0;
  });
}
function render(){
  var list = filtered(), main = document.getElementById("main"), grouped = document.getElementById("grp").value === "bt";
  list = list.slice().sort(function(a,b){
    var x=a[sortKey], y=b[sortKey];
    if (sortKey==="exit_mix") { x=topShare(a); y=topShare(b); }
    return (x>y?1:x<y?-1:0)*sortDir || (b.mtime - a.mtime);
  });
  // grouped layout: one section per BT, sections ordered by where they first appear in the sorted list
  var sets = grouped ? {} : null, order = [];
  if (sets) { list.forEach(function(r){ if(!sets[r.bt]){ sets[r.bt]=[]; order.push(r.bt); } sets[r.bt].push(r); }); }
  var parts = [];
  function table(rows){
    var h = "<table><thead><tr>"+COLS.map(function(c){
      var on = sortKey===c[0], mark = !sortable(c[0]) ? "" : on ? (sortDir>0?" ▲":" ▼") : " <span class='sorthint'>⇅</span>";
      return "<th class='"+(c[2]||"")+(sortable(c[0])?" sortable":"")+(on?" sorted":"")+"' data-k='"+c[0]+"' title='"+(sortable(c[0])?(c[3]||"Click to sort")+(c[3]?" · click to sort":""):"")+"'>"+c[1]+mark+"</th>";
    }).join("")+"</tr></thead><tbody>";
    rows.forEach(function(r){
      h += "<tr data-run='"+esc(r.run)+"'><td class='name'>"+(sets?"":"<span class='tag'>"+esc(r.bt)+"</span> ")+esc(r.title)+"<small>"+esc(r.file)+(r.bars_pct<100?" <span class='tag warn' title='share of trades with cached 1-minute bars'>charts for "+r.bars_pct.toFixed(0)+"%</span>":"")+"</small></td>"
        +"<td class='ran'>"+ran(r.mtime)+"</td><td>"+r.start+" → "+r.end+"</td><td class='num'>"+r.n.toLocaleString("en-IN")+"</td>"
        +"<td class='num "+cls(r.gross_pct)+"'>"+pct(r.gross_pct)+"</td>"
        +"<td class='num "+cls(r.net_real_pct)+"'>"+pct(r.net_real_pct)+"</td>"
        +"<td class='num'>"+r.win_real.toFixed(1)+"%</td>"
        +"<td class='num' title='"+r.target_hit_n+" trades exited at the target'>"+r.target_hit_pct.toFixed(1)+"%</td>"
        +"<td class='num "+cls(r.net_real_inr)+"'>"+inr(r.net_real_inr, r.currency)+"</td><td>"+mixBar(r)+"</td></tr>";
    });
    return h+"</tbody></table>";
  }
  if (!list.length) main.innerHTML = "<div class='empty'>No backtest matches.</div>";
  else if (sets) {
    order.forEach(function(k){
      var rs = sets[k], tn = rs.reduce(function(s,r){return s+r.n;},0);
      parts.push("<div class='bt'><h2>"+esc(k)+"</h2><span>"+rs.length+" run"+(rs.length>1?"s":"")+" · "+tn.toLocaleString("en-IN")+" trades</span></div>"+table(rs));
    });
    main.innerHTML = parts.join("");
  } else main.innerHTML = table(list);
  document.getElementById("shown").textContent = list.length+" of "+RUNS.length+" runs";
  Array.prototype.forEach.call(main.querySelectorAll("tbody tr"), function(tr){
    tr.onclick = function(ev){
      var u = "/run/"+encodeURIComponent(tr.getAttribute("data-run"));
      if (ev.metaKey || ev.ctrlKey) window.open(u, "_blank"); else location.href = u;
    };
  });
  Array.prototype.forEach.call(main.querySelectorAll("th"), function(th){
    var k = th.getAttribute("data-k");
    if (!sortable(k)) return;
    th.onclick = function(){
      sortDir = k === sortKey ? -sortDir : (k==="start"?1:-1); sortKey = k; render();
    };
  });
}
function init(){
  var bts = {}; RUNS.forEach(function(r){ bts[r.bt]=1; });
  var sel = document.getElementById("btsel");
  Object.keys(bts).forEach(function(k){ var o=document.createElement("option"); o.value=k; o.textContent=k; sel.appendChild(o); });
  ["q","btsel","minn","grp"].forEach(function(id){ document.getElementById(id).oninput = render; });
  var lg = document.getElementById("legend"), seen = {};
  RUNS.forEach(function(r){ Object.keys(r.exit_mix).forEach(function(k){ seen[k]=(seen[k]||0)+r.exit_mix[k]; }); });
  Object.keys(seen).sort(function(a,b){return seen[b]-seen[a];}).slice(0,12).forEach(function(k){
    lg.innerHTML += "<span><i style='background:"+col(k)+"'></i>"+nice(k)+"</span>";
  });
  render();
}
document.addEventListener("DOMContentLoaded", init);
"""


def index_page(runs: list[dict], statics: list[dict]) -> str:
    total = sum(r["n"] for r in runs)
    bts = len({r["bt"] for r in runs})
    statics_html = "".join(
        f'<a href="/files/{html.escape(s["file"])}" target="_blank">{html.escape(s["title"])}'
        f'<br><small>{html.escape(s["file"])} · {s["mb"]} MB · pre-built, no Group by</small></a>'
        for s in statics)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest dashboard</title><style>{INDEX_CSS}</style></head><body>
<header>
  <h1>Backtest dashboard</h1>
  <p><a href="/lab" style="color:var(--acc);font-weight:600">➜ Open the Lab</a> — pick indicators with
  checkboxes, apply them to one fixed base trade, and keep every result.</p>
  <p>Every backtest run in <code>research/backtests/</code>, newest first. Click a run to open all of
  its trades, each on its own candle chart with EMA9/20/200, VWAP, MACD, volume, support/resistance
  and the BUY / SELL / stop / target lines — and use <b>Group by</b> there to split the trades by exit
  reason or by whether the target was hit. Click a column heading to sort. <b>Ran</b> is when the run's
  CSV was written; <b>data window</b> is the market dates it covers. ⌘/ctrl-click opens a run in a new tab.</p>
  <p>These are <b>descriptive</b> views of runs that were already measured — none of them is a new
  test, and a spent window can kill an idea but not bless one.</p>
</header>
<div class="chips">
  <span class="chip"><b>{bts}</b> backtests</span>
  <span class="chip"><b>{len(runs)}</b> runs with trade-level charts</span>
  <span class="chip"><b>{total:,}</b> trades</span>
  <span class="chip" id="shown"></span>
</div>
<div class="bar">
  <input id="q" type="search" placeholder="Search — BT52, checkpoint, 2024, struct…" autofocus>
  <label>BT <select id="btsel"><option value="">all</option></select></label>
  <label>Min trades <input id="minn" type="number" min="0" value="0" style="width:80px"></label>
  <label>Layout <select id="grp"><option value="">flat list</option>
    <option value="bt">grouped by BT</option></select></label>
</div>
<div class="legend" id="legend"><span style="color:var(--ink)">Exit mix:</span></div>
<main id="main"></main>
<main>
  <div class="bt"><h2>Other pre-built reports</h2>
    <span>bespoke reports for backtests whose trades are not in the bt17 format</span></div>
  <div class="statics">{statics_html}</div>
</main>
<script>var DATA = {json.dumps({"runs": runs})};</script>
<script>{INDEX_JS}</script>
</body></html>"""


# -------------------------------------------------------------------- server

def make_handler(runs: Runs):
    class H(BaseHTTPRequestHandler):
        server_version = "bt-dashboard"

        def log_message(self, fmt: str, *args: object) -> None:  # quiet; errors still print
            if args and str(args[1]).startswith(("4", "5")):
                sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, status: int, obj: object) -> None:
            self.send(status, json.dumps(obj).encode(), "application/json")

        def lab(self, method: str) -> bool:
            """The lab (btlab/): indicator experiments on a fixed base trade, saved per run."""
            u = urlparse(self.path)
            path = unquote(u.path)
            if not (path == "/lab" or path.startswith(("/lab/", "/api/lab/"))):
                return False
            try:
                from btlab import server as lab_server
            except Exception as e:  # keep the rest of the dashboard alive if the lab cannot load
                self.json(500, {"error": f"lab unavailable: {e!r}"})
                return True
            return lab_server.handle(self, method, path, u.query)

        def do_POST(self) -> None:  # noqa: N802
            if not self.lab("POST"):
                self.send(404, b"not found", "text/plain")

        def do_PATCH(self) -> None:  # noqa: N802
            if not self.lab("PATCH"):
                self.send(404, b"not found", "text/plain")

        def do_DELETE(self) -> None:  # noqa: N802
            if not self.lab("DELETE"):
                self.send(404, b"not found", "text/plain")

        def do_GET(self) -> None:  # noqa: N802
            if self.lab("GET"):
                return
            u = urlparse(self.path)
            path = unquote(u.path)
            q = parse_qs(u.query)
            try:
                if path == "/":
                    listing = runs.refresh(force="refresh" in q)
                    page = index_page(listing, static_reports())
                    return self.send(200, page.encode(), "text/html; charset=utf-8")
                m = re.fullmatch(r"/run/([^/]+)", path)
                if m:
                    runs.ensure(m.group(1))
                    page = report_page(runs, m.group(1), (q.get("group") or [""])[0])
                    if page is None:
                        return self.send(404, b"unknown run", "text/plain")
                    return self.send(200, page.encode(), "text/html; charset=utf-8")
                m = re.fullmatch(r"/api/run/([^/]+)/trade/(\d+)", path)
                if m:
                    runs.ensure(m.group(1))
                    d = runs.detail(m.group(1), int(m.group(2)))
                    if d is None:
                        return self.json(404, {"error": "unknown trade"})
                    if d.get("_missing"):
                        return self.json(404, {"error": "no cached 1-minute bars for this symbol/day"})
                    return self.json(200, d)
                m = re.fullmatch(r"/files/([\w.\-]+\.html)", path)
                if m and (HERE / m.group(1)).is_file():
                    return self.send(200, (HERE / m.group(1)).read_bytes(), "text/html; charset=utf-8")
                return self.send(404, b"not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # keep the server alive; show the cause in the browser
                sys.stderr.write(f"error on {self.path}: {e!r}\n")
                try:
                    self.json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": repr(e)})
                except Exception:
                    pass

    return H


def check_unlabelled(rescan: bool) -> int:
    """Runs nobody named, and trade CSVs the dashboard cannot chart. Exit 1 if either."""
    raw = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    legacy = set(raw.get("_legacy_unlabelled", []))
    legacy_bad = set(raw.get("_legacy_not_chartable", []))
    listing = scan(rescan)
    todo = [r for r in listing if not r["labelled"] and r["run"] not in legacy]
    for r in todo:
        print(f'{r["run"]:<46} {r["n"]:>5} trades  {r["start"]} → {r["end"]}')
    print(f"{len(todo)} unlabelled run(s)"
          + (" — add them to research/backtests/dashboard_runs.json" if todo else ""))
    # a CSV named like a trade file that fails the column check is NOT listed at all,
    # which would look like success: say so, with the columns it lacks
    listed = {r["run"] for r in listing}
    bad = []
    for f in sorted(HERE.glob("*trades*.csv")):
        if f.stem in listed or f.stem in legacy_bad:
            continue
        try:
            cols = set(pd.read_csv(f, nrows=0).columns)
        except Exception as e:
            bad.append((f.name, f"unreadable: {e}"))
            continue
        miss = sorted(REQUIRED - cols)
        bad.append((f.name, "missing columns: " + ", ".join(miss) if miss
                    else "no rows, or a row failed to parse"))
    for name, why in bad:
        print(f"NOT LISTED  {name}: {why}")
    if bad:
        print(f"{len(bad)} trade CSV(s) the dashboard cannot chart — write the required "
              "columns (see AGENTS.md), or list them under _legacy_not_chartable")
    return 1 if todo or bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument("--list", action="store_true", help="print the run list and exit")
    ap.add_argument("--rescan", action="store_true", help="ignore the cached statistics")
    ap.add_argument("--unlabelled", action="store_true",
                    help="list runs nobody has given a BT number/title (add them to "
                         "dashboard_runs.json) and trade CSVs that cannot be charted; "
                         "exit non-zero if there are any")
    a = ap.parse_args()
    if a.unlabelled:
        return check_unlabelled(a.rescan)
    if a.list:
        for r in scan(a.rescan):
            print(f'{r["bt"]:<18} {r["run"]:<42} {r["n"]:>5} trades  {r["start"]} → {r["end"]}  '
                  f'gross {r["gross_pct"]:+.3f}%  net@real {r["net_real_pct"]:+.3f}%  '
                  f'target {r["target_hit_pct"]:.0f}%')
        return 0
    runs = Runs()
    if a.rescan:
        runs.refresh(force=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(runs))
    except OSError as e:
        print(f"port {a.port} is already in use ({e.strerror}) — most likely an older copy of this "
              f"dashboard, which keeps serving its old code.\n"
              f"  stop it:  lsof -ti :{a.port} | xargs kill\n"
              f"  or pick another port:  pnpm bt:dashboard --port {a.port + 1}")
        return 1
    print(f"backtest dashboard: {len(runs.runs)} runs — http://localhost:{a.port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
