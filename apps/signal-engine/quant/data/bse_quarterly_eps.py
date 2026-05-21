"""BSE historical quarterly EPS ingest — PDF approach.

Fetches actual quarterly standalone EPS for Nifty Midcap 150 back to 2015 by
downloading and parsing quarterly result PDFs from BSE's announcement archive.
This is the prerequisite for training the LightGBM model on consistent quarterly
EPS features across the full training window (2015–2023).

Why PDFs
--------
BSE's structured financial-result API endpoints (FinancialResultsNew, etc.) return
HTML pages, not JSON.  The AnnSubCategoryGetData endpoint IS JSON and returns
ATTACHMENTNAME for each quarterly result filing.  The PDF at
/xml-data/corpfiling/AttachHis/{ATTACHMENTNAME} contains the SEBI-mandated
quarterly result table in a consistent columnar format.  Post-2018, extraction
success rate is ~95%.  Pre-2018, ~75% (format varies by company; failures are
skipped and existing TTM EPS is retained).

Pipeline
--------
1. NSE symbol → BSE scrip code via ListofScripData API
2. All financial-result announcements in date range via AnnSubCategoryGetData
3. Filter to quarterly announcements; parse period-end from NEWSSUB headline
4. Download PDF from /xml-data/corpfiling/AttachHis/{ATTACHMENTNAME}
5. Extract basic EPS (current quarter = first value in the "Basic" row)
6. Merge into (symbol, fiscal_quarter, fiscal_year, eps_quarterly) table
7. Patch existing parquet via tickertape_earnings.merge_with_existing_parquet

Usage
-----
  cd apps/signal-engine

  # Probe one symbol — shows extracted quarters without writing
  python -m quant.data.bse_quarterly_eps --probe MPHASIS

  # Dry run for a few symbols
  python -m quant.data.bse_quarterly_eps --max-symbols 5 --dry-run

  # Full ingest (training window default: 2015-01-01 → 2023-06-30)
  python -m quant.data.bse_quarterly_eps

Rate limiting: ~2 s/announcement (1 metadata + 1 PDF request).
Full run (150 symbols × ~32 quarters avg) ≈ 2–3 hours; best run overnight.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import time
from io import BytesIO
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

from quant.data.earnings_ingest import _parquet_path, compute_yoy_columns
from quant.data.tickertape_earnings import _safe_float

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_API_BASE    = "https://api.bseindia.com/BseIndiaAPI/api"
_ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}

_API_SLEEP = 1.5   # between API metadata calls
_PDF_SLEEP = 2.0   # between PDF downloads (larger files)

# ── Period parsing ─────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

# Matches dates like "31 December 2023", "30 September 2023", "March 2024"
_DATE_RE = re.compile(
    r"(\d{1,2})?\s*"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[\s,]*(\d{4})",
    re.IGNORECASE,
)

_ANNUAL_RE = re.compile(r"\bannual\b|\bfull.?year\b|\byear.?ended\s+march\b", re.IGNORECASE)

# Matches numbers in the EPS row, including negatives in parentheses: (19.80)
# Requires decimal point in both forms to avoid matching row indices like (1), (2).
_NUM_RE = re.compile(r"\([\d]*\.[\d]+\)|[-\d]+\.\d+")


def _month_to_fq_fy(month: int, year: int) -> tuple[int, int]:
    """Period-end (month, calendar year) → (fiscal_quarter, fiscal_year) Indian convention."""
    if month in (4, 5, 6):   return 1, year + 1
    if month in (7, 8, 9):   return 2, year + 1
    if month in (10, 11, 12): return 3, year + 1
    return 4, year  # Jan/Feb/Mar


def _parse_period(newssub: str, dt_tm: str) -> tuple[int, int] | None:
    """Return (fiscal_quarter, fiscal_year) from a BSE result headline.

    Returns None if this looks like an annual result or is unparseable.
    """
    if _ANNUAL_RE.search(newssub):
        return None

    m = _DATE_RE.search(newssub)
    if m:
        mon = _MONTH_MAP.get(m.group(2).lower()[:3])
        if mon is None:
            return None
        return _month_to_fq_fy(mon, int(m.group(3)))

    # No date in headline — estimate from announcement timestamp (results ~45d after qend)
    try:
        est = pd.Timestamp(dt_tm) - pd.Timedelta(days=45)
        return _month_to_fq_fy(est.month, est.year)
    except Exception:
        return None


# ── BSE API ────────────────────────────────────────────────────────────────────

def lookup_bse_code(nse_symbol: str, session: requests.Session) -> str | None:
    """NSE symbol → BSE numeric scrip code string."""
    try:
        r = session.get(
            f"{_API_BASE}/ListofScripData/w",
            params={"segment": "Equity", "scripname": nse_symbol},
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            return None
        sym_up = nse_symbol.upper()
        # Exact match on BSE short-code first, then name substring
        for row in rows:
            if row.get("scrip_id", "").upper() == sym_up:
                return str(row["SCRIP_CD"])
        for row in rows:
            if sym_up in row.get("Scrip_Name", "").upper():
                return str(row["SCRIP_CD"])
        return str(rows[0]["SCRIP_CD"]) if rows else None
    except Exception as exc:
        logger.debug("BSE code lookup %s: %s", nse_symbol, exc)
        return None


def _get_announcements_page(
    bse_code: str, start_fmt: str, end_fmt: str, pageno: int,
    session: requests.Session,
) -> list[dict]:
    try:
        r = session.get(
            f"{_API_BASE}/AnnSubCategoryGetData/w",
            params={
                "pageno": str(pageno),
                "strCat": "Result",
                "strPrevDate": start_fmt,
                "strScrip": bse_code,
                "strSearch": "P",
                "strToDate": end_fmt,
                "strType": "C",
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("Table", [])
    except Exception as exc:
        logger.debug("Announcements page=%d bse=%s: %s", pageno, bse_code, exc)
        return []


def fetch_announcements(
    bse_code: str, start: str, end: str, session: requests.Session
) -> list[dict]:
    """All financial-result announcements for a BSE scrip in [start, end]."""
    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")
    all_rows: list[dict] = []
    for pageno in range(1, 20):   # max 20 pages ≈ 200 results
        rows = _get_announcements_page(bse_code, s, e, pageno, session)
        if not rows:
            break
        all_rows.extend(rows)
        time.sleep(_API_SLEEP)
    return all_rows


# ── PDF download + EPS extraction ──────────────────────────────────────────────

def download_pdf(attachment_name: str, session: requests.Session) -> bytes | None:
    url = f"{_ATTACH_BASE}/{attachment_name}"
    try:
        r = session.get(url, timeout=60)
        if r.ok and r.content[:4] == b"%PDF":
            return r.content
    except Exception as exc:
        logger.debug("PDF download %s: %s", attachment_name, exc)
    return None


def extract_basic_eps(pdf_bytes: bytes) -> float | None:
    """Extract current-quarter basic EPS from a SEBI quarterly result PDF.

    The SEBI-format P&L table has a row:
        Basic ( ₹)   19.80   20.79   21.90   ...
    where the first number is the current quarter.  Pages 1–5 are checked.
    """
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:5]:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    ll = line.lower().strip()
                    if "basic" not in ll:
                        continue
                    # Skip non-EPS "basic" rows (par value, capital, ratio, etc.)
                    if re.search(
                        r"basic\s*eps\s*%|capital|ratio|shares"
                        r"|par\s*value|face\s*value|nominal\s*value|denomination",
                        ll,
                    ):
                        continue
                    # Must look like an EPS row: has numbers or is the EPS header
                    if not (re.search(r"\d+\.\d+", line) or "per share" in ll or "earning" in ll):
                        continue
                    nums = _NUM_RE.findall(line)
                    if not nums:
                        continue
                    raw = nums[0]
                    negative = raw.startswith("(")
                    val = _safe_float(raw.strip("()"))
                    if val is None:
                        continue
                    if negative:
                        val = -val
                    # Sanity: EPS in ₹ per share, not crores
                    if abs(val) >= 10_000:
                        continue
                    # Reject face-value look-alikes: if ALL numbers on the row are
                    # the same round value (1.00, 2.00, 5.00, 10.00), it's the face
                    # value row slipping through (e.g. "Par Value (Basic) (₹1.00)")
                    if val in (1.0, -1.0, 2.0, -2.0, 5.0, -5.0, 10.0, -10.0):
                        all_vals = [_safe_float(n.strip("()")) for n in nums]
                        all_vals = [v for v in all_vals if v is not None]
                        if len(all_vals) >= 2 and all(abs(v - abs(val)) < 0.01 for v in all_vals):
                            continue
                    return val
    except Exception as exc:
        logger.debug("EPS extraction error: %s", exc)
    return None


# ── Per-symbol pipeline ────────────────────────────────────────────────────────

def fetch_symbol(
    nse_symbol: str,
    bse_code: str,
    start: str,
    end: str,
    session: requests.Session,
) -> pd.DataFrame:
    """Return quarterly EPS rows for one symbol from BSE PDFs."""
    announcements = fetch_announcements(bse_code, start, end, session)

    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()

    for ann in announcements:
        newssub = ann.get("NEWSSUB", "")
        dt_tm   = ann.get("DT_TM") or ann.get("NEWS_DT", "")
        attach  = ann.get("ATTACHMENTNAME", "")

        period = _parse_period(newssub, dt_tm)
        if period is None or not attach:
            continue

        fq, fy = period
        if (fq, fy) in seen:
            continue

        time.sleep(_PDF_SLEEP)
        pdf = download_pdf(attach, session)
        if pdf is None:
            continue

        eps = extract_basic_eps(pdf)
        if eps is None:
            logger.debug("  %s FY%d Q%d: EPS extraction failed", nse_symbol, fy, fq)
            continue

        rows.append({
            "symbol": nse_symbol.upper(),
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "eps_quarterly": eps,
            "revenue_quarterly_cr": None,
        })
        seen.add((fq, fy))
        logger.info("    FY%d Q%d: eps=%.2f  [%s]", fy, fq, eps, newssub[:50])

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Quality report ─────────────────────────────────────────────────────────────

def quality_report(df: pd.DataFrame) -> None:
    total = len(df)
    bse_rows = df["source_url"].str.startswith("bse:", na=False).sum()
    html_rows = df["source_url"].str.contains("quarterly_html", na=False).sum()
    ttm_rows  = df["source_url"].str.contains("ttm_eps", na=False).sum()
    eps_fill  = df["eps_reported"].notna().mean()
    yoy_fill  = df["yoy_eps_prev"].notna().mean()

    print("\n=== BSE Quarterly EPS Patch Quality ===")
    print(f"Total rows:            {total:,}")
    print(f"BSE PDF patched:       {bse_rows:,}  ({100*bse_rows/total:.1f}%)")
    print(f"Screener HTML patched: {html_rows:,}  ({100*html_rows/total:.1f}%)")
    print(f"Remaining TTM:         {ttm_rows:,}  ({100*ttm_rows/total:.1f}%)")
    print(f"eps_reported fill:     {eps_fill:.1%}")
    print(f"yoy_eps_prev fill:     {yoy_fill:.1%}")

    train = df[pd.to_datetime(df["business_date"]) <= pd.Timestamp("2023-06-30")]
    if not train.empty:
        tr_bse = train["source_url"].str.startswith("bse:", na=False).sum()
        print(f"\nTraining window (≤2023-06-30):")
        print(f"  BSE quarterly rows:  {tr_bse:,} / {len(train):,}  ({100*tr_bse/len(train):.1f}%)")
        print(f"  yoy fill rate:       {train['yoy_eps_prev'].notna().mean():.1%}")
    print()


# ── Probe mode ─────────────────────────────────────────────────────────────────

def probe_symbol(
    nse_symbol: str,
    start: str = "2022-01-01",
    end: str = "2024-06-30",
) -> None:
    """Print scraped quarterly EPS for one symbol (no writes)."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    print(f"\nProbing {nse_symbol}  ({start} → {end})")
    bse_code = lookup_bse_code(nse_symbol, session)
    print(f"BSE scrip code: {bse_code}")
    if not bse_code:
        print("ERROR: BSE code not found")
        return

    time.sleep(_API_SLEEP)
    df = fetch_symbol(nse_symbol, bse_code, start, end, session)

    if df.empty:
        print("No quarterly EPS extracted — check PDF format or date range")
    else:
        print(f"\n{len(df)} quarters extracted:")
        print(df.sort_values(["fiscal_year", "fiscal_quarter"]).to_string(index=False))


# ── Full ingest ────────────────────────────────────────────────────────────────

def ingest(
    universe_file: Path,
    start: str = "2015-01-01",
    end: str = "2023-06-30",
    max_symbols: int | None = None,
    dry_run: bool = False,
    parquet_path: Path | None = None,
) -> pd.DataFrame:
    """Fetch BSE quarterly EPS for all symbols and patch the parquet."""
    symbols = pd.read_csv(universe_file)["symbol"].str.upper().tolist()
    if max_symbols:
        symbols = symbols[:max_symbols]

    logger.info("BSE quarterly EPS ingest: %d symbols, %s → %s", len(symbols), start, end)

    session = requests.Session()
    session.headers.update(_HEADERS)

    all_frames: list[pd.DataFrame] = []
    for i, sym in enumerate(symbols):
        logger.info("[%d/%d] %s", i + 1, len(symbols), sym)
        time.sleep(_API_SLEEP)

        bse_code = lookup_bse_code(sym, session)
        if not bse_code:
            logger.warning("  %s: BSE code not found — skipping", sym)
            continue

        logger.info("  %s → BSE %s", sym, bse_code)
        time.sleep(_API_SLEEP)

        df = fetch_symbol(sym, bse_code, start, end, session)
        if df.empty:
            logger.info("  %s: no EPS extracted", sym)
            continue

        all_frames.append(df)
        logger.info("  %s: %d quarters", sym, len(df))

    if not all_frames:
        logger.warning("No data extracted")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol", "fiscal_quarter", "fiscal_year"])
    logger.info("Total: %d rows, %d symbols", len(combined), combined["symbol"].nunique())

    if dry_run:
        logger.info("Dry run — parquet not modified")
        return combined

    from quant.data.tickertape_earnings import merge_with_existing_parquet
    merged = merge_with_existing_parquet(combined, parquet_path=parquet_path)

    # Re-tag BSE rows (merge_with_existing uses "tickertape:quarterly" as source)
    bse_keys = set(zip(combined["symbol"], combined["fiscal_quarter"], combined["fiscal_year"]))
    def _tag(row):
        k = (row["symbol"], row.get("fiscal_quarter"), row.get("fiscal_year"))
        if k in bse_keys:
            return f"bse:{row['symbol']}:quarterly_pdf"
        return row["source_url"]
    merged["source_url"] = merged.apply(_tag, axis=1)

    merged = compute_yoy_columns(merged)
    quality_report(merged)
    return merged


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Ingest BSE historical quarterly EPS from quarterly result PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--probe", metavar="SYMBOL",
                        help="Probe one symbol — show extracted EPS without writing")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end",   default="2023-06-30",
                        help="Default covers the full training window")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--universe", type=Path,
                        default=Path("data/lake/midcap150_constituents.csv"))
    parser.add_argument("--parquet", type=Path, default=None)
    args = parser.parse_args()

    if args.probe:
        probe_symbol(args.probe.upper(), start=args.start, end=args.end)
        return

    parquet_path = args.parquet or _parquet_path()
    if not parquet_path.exists():
        logger.error("Parquet not found: %s", parquet_path)
        sys.exit(1)
    if not args.universe.exists():
        logger.error("Universe file not found: %s", args.universe)
        sys.exit(1)

    merged = ingest(
        universe_file=args.universe,
        start=args.start,
        end=args.end,
        max_symbols=args.max_symbols,
        dry_run=args.dry_run,
        parquet_path=args.parquet,
    )

    if args.dry_run or merged.empty:
        return

    backup = parquet_path.with_suffix(".parquet.bak2")
    shutil.copy2(parquet_path, backup)
    logger.info("Backed up parquet to %s", backup)
    merged.to_parquet(parquet_path, index=False)
    bse_count = merged["source_url"].str.startswith("bse:", na=False).sum()
    logger.info("Written %d rows (%d BSE-quarterly-patched) to %s",
                len(merged), bse_count, parquet_path)


if __name__ == "__main__":
    main()
