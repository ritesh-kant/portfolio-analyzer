"""The US half of the news indicator: the company's own SEC EDGAR filings.

Same job as the NSE half in `news.py`, same shape: a per-symbol on-disk cache shared by every run
(`.cache_sec_news/<SYMBOL>.json`, git-ignored), one request per symbol (plus one for the ticker->CIK
table and one per older submissions page when a window reaches back past the ~1,000 newest filings),
read-only while a run is applied. Descriptive plumbing for the lab; it never feeds a live decision.

Why EDGAR: it is the only legitimate free US source (see the 2026-09-24 sources audit). A small-cap's
catalyst press release is attached to an 8-K (items 7.01 / 8.01 / 1.01 / 2.02, exhibit 99.1), so the
filing's timestamp is the moment the wire release reached the public record.

Limits, stated rather than hidden:
  * The 8-K item codes say what KIND of event it is, not what the release said. A 7.01 / 8.01 can be
    a contract win or a conference-call notice; both count as news (tier "material") and the price
    reaction is what separates them.
  * `company_tickers_exchange.json` is today's ticker list: a delisted or renamed symbol has no CIK
    here and counts as "no filing" (reported by the fetch job as `unmapped`).
  * SEC requires a contact in the User-Agent: set `SEC_USER_AGENT` (environment or the repo `.env`),
    e.g. "Your Name you@example.com". Nothing is fetched without it.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from .paths import BACKTESTS, REPO

from src.momentum_trader import us_news_context as _ctx  # noqa: E402  (signal-engine, on sys.path via btlab/__init__)

CACHE = BACKTESTS / ".cache_sec_news"
TICKERS = CACHE / "_tickers.json"
GAP = 0.15                   # seconds between requests (SEC allows 10/s)
NY = _ctx.NY

# 8-K item codes. material = a company-disclosed event that can move a price: agreement (1.01),
# acquisition/disposal completed (2.01), results (2.02), Reg FD disclosure (7.01) and "other events" (8.01),
# which is where small caps put contract wins, trial data and partnerships. offering = dilution (424B*, S-1/S-3,
# unregistered share sale 3.02): news, but the wrong sign for a long, so it never counts as a catalyst.
_MATERIAL_ITEMS = {"1.01", "2.01", "2.02", "7.01", "8.01"}
_UNCLEAR_ITEMS = {"1.02", "2.03", "2.04", "2.05", "2.06", "3.01", "3.03", "4.01", "4.02", "5.01", "5.02",
                  "5.03", "5.07", "5.08", "8.02"}
_MATERIAL_FORMS = re.compile(r"^(6-K|425|8-A12B)(/A)?$")      # 6-K = a foreign issuer's press release; 425 = a deal communication
_UNCLEAR_FORMS = re.compile(r"^(10-Q|10-K|20-F|40-F|10-KT|11-K|SC TO-[CIT]|SC 14D9)(/A)?$")
TIER_ORDER = {"material": 0, "unclear": 1, "offering": 2, "routine": 3}


def sec_tier(form: str, items: str) -> str:
    """material | unclear | offering | routine for one EDGAR filing, from its form and 8-K item codes."""
    if _ctx.DILUTION_FORMS.match(form):
        return "offering"
    codes = {c.strip() for c in (items or "").split(",") if c.strip()}
    if "3.02" in codes:
        return "offering"
    if form.startswith("8-K"):
        if codes & _MATERIAL_ITEMS:
            return "material"
        return "unclear" if codes & _UNCLEAR_ITEMS else "routine"
    if _MATERIAL_FORMS.match(form):
        return "material"
    if _UNCLEAR_FORMS.match(form):
        return "unclear"
    return "routine"


def headline(form: str, items: str) -> str:
    """'8-K · 2.02 Results of operations', or the form's plain name."""
    codes = [c.strip() for c in (items or "").split(",") if c.strip() and c.strip() != "9.01"]
    if codes:
        return f"{form} · " + " · ".join(f"{c} {_ctx.ITEM_NAMES.get(c, '')}".strip() for c in codes)
    return _ctx.FORM_NAMES.get(re.sub(r"/A$", "", form), form)


def user_agent() -> str:
    """SEC_USER_AGENT from the environment, else from the repo's .env (never printed or logged)."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    env = REPO / ".env"
    try:
        for line in env.read_text().splitlines():
            if line.startswith("SEC_USER_AGENT="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def sym_file(symbol: str) -> Path:
    return CACHE / f"{symbol}.json"


def load_sym(symbol: str) -> dict:
    """{"cik": int|None, "covered": [[from, to], ...] ISO dates, "rows": [{t, f, i, u}, ...]}."""
    f = sym_file(symbol)
    try:
        d = json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        d = {}
    d.setdefault("cik", None)
    d.setdefault("covered", [])
    d.setdefault("rows", [])
    return d


def rows_of(symbol: str) -> list[tuple[datetime, str, str]]:
    """(filed_at in New York time, headline, tier) for every cached non-routine filing of a symbol."""
    out = []
    for x in load_sym(symbol)["rows"]:
        k = sec_tier(x["f"], x["i"])
        if k != "routine":
            out.append((datetime.fromisoformat(x["t"]).astimezone(NY), headline(x["f"], x["i"]), k))
    return out


def _get(client: httpx.Client, url: str):
    time.sleep(GAP)
    r = client.get(url)
    r.raise_for_status()
    return r.json()


def _ticker_map(client: httpx.Client) -> dict[str, int]:
    """ticker -> CIK, cached for a day (the table is ~10k rows)."""
    CACHE.mkdir(exist_ok=True)
    try:
        if TICKERS.exists() and time.time() - TICKERS.stat().st_mtime < 86_400:
            return json.loads(TICKERS.read_text())
    except ValueError:
        pass
    body = _get(client, _ctx.TICKERS_URL)
    c, t = body["fields"].index("cik"), body["fields"].index("ticker")
    m = {str(r[t]): int(r[c]) for r in body["data"]}
    TICKERS.write_text(json.dumps(m))
    return m


def cik_of(m: dict[str, int], symbol: str) -> int | None:
    issuer = re.sub(r"[.-]?(WS|W|U|R|RT)$", "", symbol) if len(symbol) >= 5 else symbol
    return m.get(symbol) or m.get(issuer)


def _rows_from(recent: dict, cik: int, lo: date) -> list[dict]:
    out = []
    for i, form in enumerate(recent.get("form", [])):
        if _ctx.SKIP_FORMS.match(form):
            continue
        try:
            t = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00"))
        except (KeyError, IndexError, ValueError):
            continue
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        if t.astimezone(NY).date() < lo:
            continue
        acc = recent["accessionNumber"][i]
        items = (recent.get("items") or [""] * len(recent["form"]))[i] or ""
        out.append({"t": t.astimezone(timezone.utc).isoformat(), "f": form, "i": items,
                    "u": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"})
    return out


def fetch_symbol(client: httpx.Client, tickers: dict[str, int], symbol: str, lo: date, hi: date) -> dict:
    """Everything EDGAR holds for `symbol` from `lo` on, as the cache record (does not write it)."""
    d = load_sym(symbol)
    cik = cik_of(tickers, symbol)
    d["cik"] = cik
    if cik is None:
        d["covered"] = _merge_iso(d["covered"], lo, hi)         # known to be unmapped: nothing to fetch
        return d
    body = _get(client, _ctx.SUBMISSIONS_URL.format(cik=cik))
    rows = _rows_from(body["filings"]["recent"], cik, lo)
    for page in body["filings"].get("files", []):               # older filings live on extra pages
        if page.get("filingTo") and date.fromisoformat(page["filingTo"]) >= lo:
            older = _get(client, f"https://data.sec.gov/submissions/{page['name']}")
            rows += _rows_from(older, cik, lo)
    have = {(x["t"], x["f"]) for x in d["rows"]}
    d["rows"] += [x for x in rows if (x["t"], x["f"]) not in have]
    d["covered"] = _merge_iso(d["covered"], lo, hi)
    return d


def _merge_iso(covered: list[list[str]], a: date, b: date) -> list[list[str]]:
    from .news import _merge
    return _merge(covered, a, b)


def fetch_all(spans: dict[str, tuple[date, date]], job: dict) -> tuple[set[str], list[str]]:
    """Fetch every symbol of `spans` (sync; runs in the prefetch thread). -> (failed symbols, symbols with no CIK)."""
    ua = user_agent()
    if not ua:
        raise RuntimeError("SEC_USER_AGENT is not set (SEC requires a contact, e.g. 'Your Name you@example.com')")
    CACHE.mkdir(exist_ok=True)
    job.update(total=len(spans), done=0, requests=0, cached=0)
    failed: set[str] = set()
    unmapped: list[str] = []
    with httpx.Client(headers={"User-Agent": ua}, timeout=20.0, follow_redirects=True) as client:
        tickers = _ticker_map(client)
        for s, (a, b) in spans.items():
            if job.get("cancel"):
                break
            try:
                d = fetch_symbol(client, tickers, s, a, b)
                sym_file(s).write_text(json.dumps(d))
                job["requests"] += 1
                if d["cik"] is None:
                    unmapped.append(s)
            except Exception as e:  # noqa: BLE001
                failed.add(s)
                job.setdefault("errors", []).append(f"{s}: {e!r}"[:200])
            job["done"] += 1
    return failed, unmapped
