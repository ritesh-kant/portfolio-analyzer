"""'Check news' for a saved run: did each traded stock have an NSE filing in the 24h before its entry?

Descriptive only. It reads NSE's corporate-announcements API and never feeds a trade decision.

NSE is asked politely:
  * ONE request per symbol, not per trade: one `from_date..to_date` call covers all of a
    symbol's trades (NSE returns the whole span in one reply: 726 rows for 4.7 years of KEC).
  * strictly sequential, REQUEST_GAP seconds apart, plus a longer rest after every BATCH
    symbols; one shared session, cookies re-primed every REPRIME_EVERY requests.
  * per symbol, the filings and the date ranges already fetched are cached on disk
    (`.cache_nse_news/<SYMBOL>.json`, git-ignored) and shared by EVERY run: another run
    only asks for the dates not yet covered, so a symbol seen before costs no request.
  * a failed window is retried with back-off, then reported as "error" (never as "no news").

NSE only: US runs have no equivalent here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np  # noqa: F401
import pandas as pd

from . import service, store  # noqa: F401  (service imports the engine path shim)
from .paths import BACKTESTS, RUNS_DIR, bar_file

from src.scrapers import nse  # noqa: E402  (signal-engine, on sys.path via btlab/__init__)

CACHE = BACKTESTS / ".cache_nse_news"
RESULT_VERSION = 2       # 2 = filings carry a tier and a trade is 'news' only for a material one
LOOKBACK = timedelta(hours=24)
REQUEST_GAP = 1.5        # seconds between requests
BATCH = 20               # symbols per batch, then a longer rest
BATCH_REST = 15.0
REPRIME_EVERY = 25       # re-fetch the NSE home page (cookies) this often
RETRIES = 3
BODY_CHARS = 400        # NSE's own one-line description of the filing, kept for the AI classifier
MAX_ITEMS = 8            # headlines kept per trade

# NSE gives each filing a short generic subject ("Updates", "Press Release" say nothing about the content),
# so this is a judgement on the SUBJECT only. Tiers: material = can move a price (results, orders,
# deals, M&A, rating, dividend, fund-raising, litigation, UPSI); query = the exchange asking about a
# move that already happened (reverse causality, not a catalyst); unclear = could be either;
# routine = compliance paperwork. A trade is "news" only if it has a MATERIAL filing.
_MATERIAL = re.compile(
    r"outcome of board meeting|financial results?|integrated filing- ?financial|acquisition|bagging|receiving of orders|"
    r"\border|agreements?\b|memorandum of understanding|\bmou\b|scheme of arrangement|amalgamation|merger|demerger|"
    r"credit rating|^dividend( updates)?$|issue of securities|qualified institutional|preferential|rights issue|"
    r"buy ?back|capacity addition|commencement of commercial|sale or disposal|disposal|"
    r"other upsi|material event|material issue|pendency of litigation|action\(s\) (taken|initiated)|"
    r"guarantees/indemnity|redemption|incorporation|joint venture", re.I)
_QUERY = re.compile(r"spurt in volume|price movement|news verification|rumou?r verification|clarification", re.I)
_UNCLEAR = re.compile(r"^(general )?updates?$|^press release|investor presentation|change in (management|director)|"
                      r"^appointment$|^resignation|^cessation|^retirement|amendment to|communication to shareholders|"
                      r"^corrigendum|memorandum", re.I)
TIER_ORDER = {"material": 0, "unclear": 1, "query": 2, "routine": 3}


def tier(headline: str) -> str:
    """material | unclear | query | routine, judged on the filing's subject ("SYMBOL: subject")."""
    subj = headline.split(": ", 1)[1] if ": " in headline else headline
    subj = subj.strip()
    if _QUERY.search(subj):
        return "query"
    if _MATERIAL.search(subj):
        return "material"
    if _UNCLEAR.search(subj):
        return "unclear"
    return "routine"


_IST = timezone(timedelta(hours=5, minutes=30))
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def news_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "news.json"


class _Session:
    """One httpx client, cookies refreshed periodically, requests rate-limited."""

    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None
        self.n = 0
        self.last = 0.0

    async def _prime(self) -> None:
        if self.client:
            await self.client.aclose()
        self.client = httpx.AsyncClient(headers=nse._HEADERS, timeout=nse._TIMEOUT, follow_redirects=True)
        await self.client.get(nse._NSE_HOME)
        await asyncio.sleep(0.5)

    async def get(self, symbol: str, a: date, b: date) -> list[dict]:
        params = {"index": "equities", "symbol": symbol.upper(),
                  "from_date": a.strftime("%d-%m-%Y"), "to_date": b.strftime("%d-%m-%Y")}
        err: Exception | None = None
        for attempt in range(RETRIES):
            if self.client is None or self.n % REPRIME_EVERY == 0 or attempt:
                await self._prime()
            wait = REQUEST_GAP - (time.monotonic() - self.last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                self.n += 1
                r = await self.client.get(nse._NSE_ANN_URL, params=params)  # type: ignore[union-attr]
                self.last = time.monotonic()
                r.raise_for_status()
                rows = nse._normalise(r.json())
                return [{"t": x["published_at"].astimezone(timezone.utc).isoformat() if x["published_at"] else None,
                         "h": x["headline"], "u": x["url"],
                         "b": x["raw_text"][len(x["headline"]) + 2:][:BODY_CHARS].strip()} for x in rows]
            except Exception as e:  # noqa: BLE001 - any failure is retried, then reported
                err = e
                self.last = time.monotonic()
                await asyncio.sleep(3 * (attempt + 1))
        raise RuntimeError(f"{symbol} {a}..{b}: {err!r}"[:200])

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()


def _entry_dt(row: pd.Series) -> datetime:
    hh, mm = str(row["entry_time"]).split(":")[:2]
    d = datetime.fromisoformat(str(row["date"])[:10])
    return d.replace(hour=int(hh), minute=int(mm), tzinfo=_IST)


def _sym_file(symbol: str) -> Path:
    return CACHE / f"{symbol}.json"


def _load_sym(symbol: str) -> dict:
    """{"covered": [[from, to], ...] ISO dates, "rows": [...]}: everything ever fetched for a symbol, from any run."""
    f = _sym_file(symbol)
    try:
        d = json.loads(f.read_text()) if f.exists() else {"covered": [], "rows": []}
    except ValueError:
        d = {"covered": [], "rows": []}
    d.setdefault("covered_text", [])      # ranges fetched since filing text was kept; older fetches have subjects only
    return d


def filing_key(symbol: str, x: dict) -> str:
    return hashlib.sha1(f"{symbol}|{x['t']}|{x['h']}".encode()).hexdigest()[:16]


def _gaps(covered: list[list[str]], a: date, b: date) -> list[tuple[date, date]]:
    """The parts of [a, b] not inside any covered interval."""
    out, cur = [], a
    for x, y in sorted((date.fromisoformat(p), date.fromisoformat(q)) for p, q in covered):
        if y < cur:
            continue
        if x > b:
            break
        if x > cur:
            out.append((cur, x - timedelta(days=1)))
        cur = max(cur, y + timedelta(days=1))
        if cur > b:
            break
    if cur <= b:
        out.append((cur, b))
    return out


def _merge(covered: list[list[str]], a: date, b: date) -> list[list[str]]:
    iv = sorted([date.fromisoformat(p), date.fromisoformat(q)] for p, q in [*covered, [a.isoformat(), b.isoformat()]])
    out = [iv[0]]
    for x, y in iv[1:]:
        if x <= out[-1][1] + timedelta(days=1):
            out[-1][1] = max(out[-1][1], y)
        else:
            out.append([x, y])
    return [[x.isoformat(), y.isoformat()] for x, y in out]


async def _fetch(spans: dict[str, tuple[date, date]], job: dict, want_text: bool = False) -> tuple[dict[str, dict], set[str]]:
    """One request per symbol for its span, minus whatever any earlier run already fetched. -> (per-symbol cache contents, failed symbols)."""
    job.update(total=len(spans), done=0, requests=0, cached=0)
    CACHE.mkdir(exist_ok=True)
    data: dict[str, dict] = {}
    failed: set[str] = set()
    sess = _Session()
    asked = 0
    try:
        for s, (a, b) in spans.items():
            if job.get("cancel"):
                break
            d = data[s] = _load_sym(s)
            gaps = _gaps(d["covered_text" if want_text else "covered"], a, b)
            if not gaps:
                job["cached"] += 1
            else:
                lo, hi = gaps[0][0], gaps[-1][1]        # one request spanning the gaps (re-fetching a covered bit is harmless)
                try:
                    got = await sess.get(s, lo, hi)
                    have = {(x["t"], x["h"]): x for x in d["rows"]}
                    for x in got:
                        if (x["t"], x["h"]) in have:
                            have[(x["t"], x["h"])].setdefault("b", x["b"])
                        else:
                            d["rows"].append(x)
                    d["covered"] = _merge(d["covered"], lo, hi)
                    d["covered_text"] = _merge(d["covered_text"], lo, hi)
                    _sym_file(s).write_text(json.dumps(d))
                    job["requests"] += 1
                except Exception as e:  # noqa: BLE001
                    failed.add(s)
                    job.setdefault("errors", []).append(str(e))
                asked += 1
                if asked % BATCH == 0:
                    await asyncio.sleep(BATCH_REST)
            job["done"] += 1
    finally:
        await sess.close()
    return data, failed


async def _run(run_id: str, tr: pd.DataFrame, job: dict, want_text: bool = False, labels: dict | None = None) -> dict:
    """One request per symbol for the span of its trades, minus whatever any earlier run already fetched."""
    by_sym: dict[str, list[date]] = {}
    for _, r in tr.iterrows():
        by_sym.setdefault(str(r["symbol"]), []).append(date.fromisoformat(str(r["date"])[:10]))
    spans = {s: (min(ds) - timedelta(days=1), max(ds)) for s, ds in sorted(by_sym.items())}
    data, failed = await _fetch(spans, job, want_text)
    return _classify(run_id, tr, data, failed, labels)


def _classify(run_id: str, tr: pd.DataFrame, data: dict[str, dict], failed: set[str], labels: dict | None = None) -> dict:
    """Per trade: news (material filing) / minor (only routine, unclear or exchange-query filings) / none / error / skipped, from the per-symbol cache contents in `data`."""
    trades = []
    for i, r in tr.reset_index(drop=True).iterrows():
        sym, d = str(r["symbol"]), date.fromisoformat(str(r["date"])[:10])
        t1 = _entry_dt(r).astimezone(timezone.utc)
        t0 = t1 - LOOKBACK
        info = data.get(sym)
        if info is None or sym in failed or _gaps(info["covered"], d - timedelta(days=1), d):
            trades.append({"i": int(i), "symbol": sym, "date": str(d), "status": "error" if sym in failed else "skipped",
                           "count": None, "items": []})
            continue
        hits = []
        for x in info["rows"]:
            if x["t"] and t0 <= datetime.fromisoformat(x["t"]) <= t1:
                kw = tier(x["h"])
                lab = labels.get(filing_key(sym, x)) if labels else None
                hits.append({**x, "kw": kw, "k": lab["k"] if lab else kw, **({"ai": lab["c"]} if lab else {})})
        hits.sort(key=lambda x: (TIER_ORDER[x["k"]], -datetime.fromisoformat(x["t"]).timestamp()))
        mat = sum(1 for x in hits if x["k"] == "material")
        trades.append({"i": int(i), "symbol": sym, "date": str(d), "status": "news" if mat else "minor" if hits else "none",
                       "count": len(hits), "material": mat, "items": hits[:MAX_ITEMS]})
    return {"run_id": run_id, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lookback_h": 24, "source": "NSE corporate announcements", "trades": trades,
            "version": RESULT_VERSION,
            "summary": {k: sum(1 for t in trades if t["status"] == k) for k in ("news", "minor", "none", "error", "skipped")}}


def start(run_id: str) -> dict:
    rec = store.get_run(run_id)
    if rec is None:
        return {"error": "unknown run"}
    if (rec["config"]["base"].get("market") or "NSE") != "NSE":
        return {"error": "news check is NSE-only"}
    with _lock:
        cur = _jobs.get(run_id)
        if cur and cur["state"] == "running":
            return status(run_id)
        job = {"state": "running", "started": time.time(), "done": 0, "total": 0}
        _jobs[run_id] = job

    def work() -> None:
        try:
            tr = service.ensure_trades(run_id)
            res = asyncio.run(_run(run_id, tr, job))
            news_path(run_id).write_text(json.dumps(res))
            job["state"] = "cancelled" if job.get("cancel") else "done"
        except Exception as e:  # noqa: BLE001
            job["state"] = "failed"
            job["error"] = repr(e)[:300]

    threading.Thread(target=work, daemon=True).start()
    return status(run_id)


def cancel(run_id: str) -> bool:
    j = _jobs.get(run_id)
    if j and j["state"] == "running":
        j["cancel"] = True
        return True
    return False


def status(run_id: str) -> dict:
    j = _jobs.get(run_id)
    return {"job": ({k: v for k, v in j.items() if k != "cancel"} if j else None), "result": load_result(run_id)}


def load_result(run_id: str) -> dict | None:
    """The saved result, re-scored from the cache (no NSE call) if it predates tiers."""
    p = news_path(run_id)
    if not p.exists():
        return None
    try:
        r = json.loads(p.read_text())
    except ValueError:
        return None
    if r.get("version") != RESULT_VERSION:
        tr = service.ensure_trades(run_id)
        data = {x: _load_sym(x) for x in tr["symbol"].astype(str).unique() if _sym_file(x).exists()}
        new = _classify(run_id, tr, data, set())
        new["checked_at"] = r.get("checked_at", new["checked_at"])
        if r.get("from_cache"):
            new["from_cache"] = True
        p.write_text(json.dumps(new))
        r = new
    return r


def summary(run_id: str) -> dict | None:
    """Small marker for the run list: None until a check has finished (partial = stopped early)."""
    r = load_result(run_id)
    if r is None:
        return None
    sm = r.get("summary", {})
    return {"checked_at": r.get("checked_at"), **sm, "complete": not (sm.get("error") or sm.get("skipped"))}


def attach_from_cache(run_id: str) -> bool:
    """Give a run the news signs from what is ALREADY cached (any earlier run's fetch). Never calls NSE.

    A new run that re-uses trades of an already-checked run is therefore marked at once;
    trades on symbols/dates not cached yet show "news ?" and the button offers to fill the gaps.
    Returns True if a result was written.
    """
    rec = store.get_run(run_id)
    if (rec is None or news_path(run_id).exists()
            or (rec["config"]["base"].get("market") or "NSE") != "NSE" or not CACHE.exists()):
        return False
    tr = service.ensure_trades(run_id)
    if tr.empty:
        return False
    data = {s: _load_sym(s) for s in tr["symbol"].astype(str).unique() if _sym_file(s).exists()}
    res = _classify(run_id, tr, data, set())
    if not (res["summary"]["news"] or res["summary"]["minor"] or res["summary"]["none"]):
        return False
    res["from_cache"] = True
    news_path(run_id).write_text(json.dumps(res))
    return True


# ------------------------------------------------------------------ the `news_reaction` entry indicator
#
# The indicator reads the SAME per-symbol cache as the check above and never calls NSE while a run is
# applied. Symbols whose filings were never fetched must be prefetched first (`prefetch_*`), which is
# the only slow step: one request per symbol, 1.5 s apart.

PREFETCH_DAYS = 4        # the indicator looks back at most 72 h (a Monday trade sees Friday evening's filings)
_pre_jobs: dict[str, dict] = {}


def candidate_spans(cands: pd.DataFrame) -> dict[str, tuple[date, date]]:
    """Per symbol, the dates a candidate table needs filings for (PREFETCH_DAYS back from the first trade day)."""
    g = cands.groupby("symbol")["date"].agg(["min", "max"])
    return {str(s): (date.fromisoformat(str(r["min"])[:10]) - timedelta(days=PREFETCH_DAYS),
                     date.fromisoformat(str(r["max"])[:10])) for s, r in g.sort_index().iterrows()}


def missing_symbols(spans: dict[str, tuple[date, date]]) -> list[str]:
    out = []
    for s, (a, b) in spans.items():
        f = _sym_file(s)
        if not f.exists() or _gaps(_load_sym(s)["covered"], a, b):
            out.append(s)
    return out


def prefetch_key(spans: dict) -> str:
    return hashlib.sha1(json.dumps({k: [str(a), str(b)] for k, (a, b) in spans.items()}, sort_keys=True).encode()).hexdigest()[:12]


def prefetch_start(spans: dict[str, tuple[date, date]]) -> dict:
    """Fetch (in a background thread) every symbol of `spans` that the cache does not cover yet."""
    key = prefetch_key(spans)
    todo = {s: spans[s] for s in missing_symbols(spans)}
    with _lock:
        cur = _pre_jobs.get(key)
        if cur and cur["state"] == "running":
            return {"key": key, **{k: v for k, v in cur.items() if k != "cancel"}}
        job = {"state": "running", "started": time.time(), "done": 0, "total": len(todo)}
        _pre_jobs[key] = job

    def work() -> None:
        try:
            _data, failed = asyncio.run(_fetch(todo, job))
            job["failed"] = sorted(failed)
            job["state"] = "cancelled" if job.get("cancel") else "failed" if failed else "done"
            if failed:
                job["error"] = f"{len(failed)} symbol(s) could not be fetched: {sorted(failed)[:5]}; try again (fetched ones are kept)"
        except Exception as e:  # noqa: BLE001
            job["state"], job["error"] = "failed", repr(e)[:300]

    threading.Thread(target=work, daemon=True).start()
    return {"key": key, **{k: v for k, v in job.items() if k != "cancel"}}


def prefetch_status(key: str) -> dict | None:
    j = _pre_jobs.get(key)
    return {"key": key, **{k: v for k, v in j.items() if k != "cancel"}} if j else None


def prefetch_cancel(key: str) -> bool:
    j = _pre_jobs.get(key)
    if j and j["state"] == "running":
        j["cancel"] = True
        return True
    return False


def _reaction_bars(symbol: str, year: int) -> pd.DataFrame | None:
    """Only the prices needed to place a prior-day filing on the historical tape."""
    path = bar_file(symbol, year)
    return pd.read_parquet(path, columns=["open", "close"]).sort_index() if path is not None else None


def stamp_reaction(df: pd.DataFrame, days: dict, lookback_h: float, scope: str) -> "np.ndarray":
    """Per candidate: how far (%) the price has moved since a qualifying NSE filing, as of the decision bar. NaN = no such filing.

    A filing qualifies when it was published inside the `lookback_h` hours before the decision bar closed,
    and its subject is material (scope "material") or material/unclear (scope "material+unclear");
    exchange queries about a price move that already happened ("spurt in volume") never count: that is
    the move causing the filing, not the other way round.

    For an intraday filing, the reference is the first 1-minute open at or after publication,
    even when the filing was on an earlier trading day. A filing with no observable
    post-publication minute on that day is ignored. Off-session filings use the last
    observed close before publication. The reaction price is the decision-bar close.
    Of several filings the largest move counts.
    """
    import numpy as np
    out = np.full(len(df), np.nan)
    ok_tier = {"material"} | ({"unclear"} if scope == "material+unclear" else set())
    cache: dict[str, list[tuple[datetime, str]]] = {}
    bar_cache: dict[tuple[str, int], pd.DataFrame | None] = {}
    window = timedelta(hours=lookback_h)

    def prior_reference(sym: str, t: datetime) -> float | None:
        key = (sym, t.year)
        if key not in bar_cache:
            bar_cache[key] = _reaction_bars(*key)
        bars = bar_cache[key]
        if bars is None or bars.empty:
            return None
        ts = pd.Timestamp(t)
        minute = t.hour * 60 + t.minute
        in_session = 9 * 60 + 15 <= minute < 15 * 60 + 30
        if in_session:
            if t.second or t.microsecond:
                ts += pd.Timedelta(minutes=1)
                ts = ts.replace(second=0, microsecond=0)
            j = int(bars.index.searchsorted(ts))
            if j < len(bars) and bars.index[j].date() == t.date():
                return float(bars["open"].iloc[j])
            return None
        j = int(bars.index.searchsorted(ts)) - 1
        if j >= 0:
            return float(bars["close"].iloc[j])
        previous = (sym, t.year - 1)
        if previous not in bar_cache:
            bar_cache[previous] = _reaction_bars(*previous)
        old = bar_cache[previous]
        return float(old["close"].iloc[-1]) if old is not None and not old.empty else None

    for i, (sym, d, key, dec, prev, close5) in enumerate(zip(df["symbol"], df["date"], df["day_key"],
                                                              df["decision_min"], df["prev_close"], df["close5"])):
        if sym not in cache:
            f = _sym_file(sym)
            rows = _load_sym(sym)["rows"] if f.exists() else []
            cache[sym] = [(datetime.fromisoformat(x["t"]).astimezone(_IST), x["h"]) for x in rows if x["t"]]
        day = datetime.fromisoformat(str(d)[:10])
        t_dec = day.replace(hour=int(dec) // 60, minute=int(dec) % 60, tzinfo=_IST)
        arr = days[key]
        best = np.nan
        for t, h in cache[sym]:
            if not (t_dec - window <= t <= t_dec) or tier(h) not in ok_tier:
                continue
            ref = float(prev)
            if t.date() == t_dec.date():
                tm = t.hour * 60 + t.minute + int(bool(t.second or t.microsecond))
                if tm >= int(arr["m1_min"][0]):
                    j = int(np.searchsorted(arr["m1_min"], tm))
                    if j >= len(arr["m1_min"]) or int(arr["m1_min"][j]) >= int(dec):
                        continue
                    ref = float(arr["m1_o"][j])
            elif t.date() < t_dec.date():
                earlier = prior_reference(sym, t)
                if earlier is None:
                    continue
                ref = earlier
            if ref > 0:
                mv = (float(close5) / ref - 1.0) * 100.0
                if not best == best or mv > best:
                    best = mv
        out[i] = best
    return out
