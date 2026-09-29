"""Descriptive-only SEC EDGAR lookup, attached to US `positions` for review.

The US twin of `news_context.py`: purely observational, never read by the
engine, screen or risk code, and never on the live decision path (the session
runs it in a background thread). `USUniverseConfig.require_catalyst` stays
False; this only records what the company itself filed, next to the trade.

Source = the symbol's own EDGAR filings (the same feed the /momentum/us/watchlist
page shows), from the start of the previous trading day to the entry. Headlines
are the form / 8-K item names - the press-release title on the watchlist page
needs an extra exhibit fetch per filing, which is not worth it for an annotation.

Any failure degrades to an empty list; nothing here raises.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
MAX_ITEMS = 10
SEC_GAP_S = 0.12          # SEC allows 10 requests/s

# Insider / ownership / correspondence paperwork - not the company disclosing.
SKIP_FORMS = re.compile(r"^(3|4|5|144|SC 13[DG]|SCHEDULE 13[DG]|13F-HR|13F-NT|CORRESP|UPLOAD)(/A)?$")
DILUTION_FORMS = re.compile(r"^(424B\d|S-1|S-3|F-1|F-3|FWP|EFFECT|D)(/A)?$")

FORM_NAMES = {
    "424B1": "Prospectus (share offering)", "424B3": "Prospectus (share offering)",
    "424B4": "Prospectus (share offering priced)", "424B5": "Prospectus supplement (share offering)",
    "424B7": "Prospectus (resale of shares)", "S-1": "Registration of new shares",
    "S-3": "Shelf registration of new shares", "F-1": "Registration of new shares",
    "F-3": "Shelf registration of new shares", "FWP": "Offering term sheet",
    "EFFECT": "Share registration declared effective", "D": "Exempt share sale notice",
    "10-Q": "Quarterly report", "10-K": "Annual report", "20-F": "Annual report (foreign issuer)",
    "6-K": "Report of a foreign issuer", "425": "Merger communication",
    "DEF 14A": "Proxy statement", "PRE 14A": "Preliminary proxy statement",
}
ITEM_NAMES = {
    "1.01": "Material agreement", "1.02": "Agreement terminated",
    "2.01": "Acquisition or sale completed", "2.02": "Results of operations", "2.03": "New debt",
    "3.01": "Listing-rule notice", "3.02": "Unregistered share sale",
    "3.03": "Change to shareholder rights", "4.01": "Auditor change",
    "5.02": "Director or officer change", "5.03": "Charter or bylaw change",
    "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events",
}

_ciks: dict[str, int] | None = None


def _previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def select_filings(
    recent: dict[str, list[str]], cik: int, at: datetime
) -> list[dict[str, Any]]:
    """Pure step: EDGAR's `filings.recent` columns -> annotation items, newest
    first, for filings accepted from the previous trading day's midnight (New
    York) up to `at`. Split out so it is testable without network access."""
    at_utc = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    at_utc = at_utc.astimezone(timezone.utc)
    start = datetime.combine(_previous_weekday(at_utc.astimezone(NY).date()), datetime.min.time(), NY)
    start_utc = start.astimezone(timezone.utc)

    items: list[dict[str, Any]] = []
    forms = recent.get("form", [])
    for i, form in enumerate(forms):
        if SKIP_FORMS.match(form):
            continue
        try:
            accepted = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00"))
        except (KeyError, IndexError, ValueError):
            continue
        if accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
        if not (start_utc <= accepted <= at_utc):
            continue
        accession = recent["accessionNumber"][i]
        codes = [c.strip() for c in (recent.get("items", [""] * len(forms))[i] or "").split(",")]
        codes = [c for c in codes if c and c != "9.01"]
        base = re.sub(r"/A$", "", form)
        described = (
            " · ".join(f"{c} {ITEM_NAMES.get(c, '')}".strip() for c in codes)
            if codes else FORM_NAMES.get(base, form)
        )
        dilution = bool(DILUTION_FORMS.match(form)) or "3.02" in codes
        items.append({
            "headline": described,
            "source": "sec_edgar",
            "publisher": f"SEC EDGAR · {form}",
            "kind": "offering" if dilution else "filing",
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm",
            "text": None,
            "published_at": accepted.astimezone(timezone.utc).isoformat(),
        })
    items.sort(key=lambda m: m["published_at"], reverse=True)
    return items[:MAX_ITEMS]


def _get(client: httpx.Client, url: str) -> Any:
    time.sleep(SEC_GAP_S)
    res = client.get(url)
    res.raise_for_status()
    return res.json()


def _cik_of(client: httpx.Client, symbol: str) -> int | None:
    global _ciks
    if _ciks is None:
        body = _get(client, TICKERS_URL)
        c, t = body["fields"].index("cik"), body["fields"].index("ticker")
        _ciks = {str(row[t]): int(row[c]) for row in body["data"]}
    # Warrants/units/rights (GLNDW, ...) file under the issuer's CIK.
    issuer = re.sub(r"[.-]?(WS|W|U|R|RT)$", "", symbol) if len(symbol) >= 5 else symbol
    return _ciks.get(symbol) or _ciks.get(issuer)


def recent_news(symbol: str, at: datetime) -> list[dict[str, Any]]:
    """Best-effort: the symbol's EDGAR filings before `at`. Returns [] on any
    failure (including no SEC_USER_AGENT) - never raises, never blocks a trade."""
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent:
        logger.warning("us_news_context: SEC_USER_AGENT not set - skipping %s", symbol)
        return []
    try:
        with httpx.Client(headers={"User-Agent": agent}, timeout=10.0) as client:
            cik = _cik_of(client, symbol)
            if cik is None:
                return []
            body = _get(client, SUBMISSIONS_URL.format(cik=cik))
            return select_filings(body["filings"]["recent"], cik, at)
    except Exception:  # noqa: BLE001
        logger.exception("us_news_context: lookup failed for %s", symbol)
        return []
