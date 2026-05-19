"""NSE Bhavcopy PIT ingest — Month 1, Block 4.

Downloads daily NSE CM (Capital Market) Bhavcopy files from the NSE archives,
normalises them to a common schema, stamps each row with an as_of_timestamp
representing when the data became publicly available, and writes year-partitioned
Parquet files to the L1 data lake.

CLI:
    python -m quant.data.ingest --source nse_bhavcopy --from 2015-01-01
    python -m quant.data.ingest --source nse_bhavcopy --from 2024-01-01 --to 2024-12-31
    python -m quant.data.ingest --source nse_bhavcopy --from 2015-01-01 --refresh

PIT rule (plan §4.3):
    as_of_timestamp = business_date at 18:00:00 IST (12:30:00 UTC).
    Bhavcopy is typically published within 2–3 h of market close (15:30 IST).
    18:00 IST is a conservative floor — data is definitely available by then.
    Nothing downstream may use data with as_of_timestamp > inference_date.

Lake layout (env var QUANT_DATA_DIR controls root, default ./data/lake):
    <QUANT_DATA_DIR>/nse_bhavcopy/{YYYY}.parquet
    One file per calendar year; all series included; filtered at read time.
"""

from __future__ import annotations

import argparse
import io
import logging
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ── PIT constant ──────────────────────────────────────────────────────────────
# 18:00 IST = UTC+5:30 → 12:30 UTC. Used as as_of_timestamp for all rows.
_AS_OF_OFFSET_HOURS = 12
_AS_OF_OFFSET_MINUTES = 30

# ── NSE URL templates ─────────────────────────────────────────────────────────
# Old archive format (2015–2023 approximately)
_OLD_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES"
    "/{yyyy}/{MMM}/cm{DD}{MMM}{yyyy}bhav.csv.zip"
)
# New BhavCopy format (introduced ~2024-01-01)
_NEW_URL = (
    "https://nsearchives.nseindia.com/content/cm"
    "/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
_NEW_FORMAT_FROM = date(2024, 1, 1)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
}
_REQUEST_DELAY_S = 0.5   # polite delay between archive requests
_TIMEOUT_S = 30
_FLUSH_EVERY_N_DAYS = 30  # flush pending rows to parquet every N days


# ── Lake path helpers ─────────────────────────────────────────────────────────

def _lake_dir() -> Path:
    import os
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    p = Path(base) / "nse_bhavcopy"
    p.mkdir(parents=True, exist_ok=True)
    return p


def year_path(year: int) -> Path:
    """Return the parquet path for a given year."""
    return _lake_dir() / f"{year}.parquet"


# ── URL construction ──────────────────────────────────────────────────────────

def _urls_for_date(dt: date) -> list[str]:
    """Return candidate download URLs, most likely format first."""
    yyyy = dt.strftime("%Y")
    mmm = dt.strftime("%b").upper()   # JAN, FEB, …
    dd = dt.strftime("%d")
    yyyymmdd = dt.strftime("%Y%m%d")
    if dt >= _NEW_FORMAT_FROM:
        return [
            _NEW_URL.format(yyyymmdd=yyyymmdd),
            _OLD_URL.format(yyyy=yyyy, MMM=mmm, DD=dd),
        ]
    return [
        _OLD_URL.format(yyyy=yyyy, MMM=mmm, DD=dd),
        _NEW_URL.format(yyyymmdd=yyyymmdd),
    ]


# ── Download ──────────────────────────────────────────────────────────────────

def _download_bhavcopy(dt: date, client: httpx.Client) -> bytes | None:
    """Try each candidate URL; return raw ZIP bytes, or None on 404 (holiday)."""
    for url in _urls_for_date(dt):
        try:
            resp = client.get(url, timeout=_TIMEOUT_S)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            logger.warning(
                "bhavcopy_http_error date=%s url=%s status=%d",
                dt, url, exc.response.status_code,
            )
        except Exception as exc:
            logger.warning("bhavcopy_download_error date=%s url=%s error=%s", dt, url, exc)
    return None


# ── Parse ─────────────────────────────────────────────────────────────────────

def _parse_old_format(df_raw: pd.DataFrame, business_date: date) -> pd.DataFrame:
    """Normalise old Bhavcopy CSV (pre-2024).

    Columns: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE,
             TOTTRDQTY, TOTTRDVAL, TIMESTAMP, TOTALTRADES, ISIN
    """
    df_raw.columns = [c.strip().upper() for c in df_raw.columns]
    return pd.DataFrame({
        "symbol": df_raw["SYMBOL"].str.strip(),
        "series": df_raw["SERIES"].str.strip(),
        "business_date": business_date,
        "open": pd.to_numeric(df_raw["OPEN"], errors="coerce"),
        "high": pd.to_numeric(df_raw["HIGH"], errors="coerce"),
        "low": pd.to_numeric(df_raw["LOW"], errors="coerce"),
        "close": pd.to_numeric(df_raw["CLOSE"], errors="coerce"),
        "prev_close": pd.to_numeric(df_raw["PREVCLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df_raw["TOTTRDQTY"], errors="coerce").fillna(0).astype("int64"),
        # TOTTRDVAL is in rupees; convert to lacs for consistency with new format
        "turnover_lacs": pd.to_numeric(df_raw["TOTTRDVAL"], errors="coerce") / 100_000,
    })


def _parse_new_format(df_raw: pd.DataFrame, business_date: date) -> pd.DataFrame:
    """Normalise new BhavCopy CSV (2024+).

    Columns: SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE,
             LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY,
             TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
    """
    df_raw.columns = [c.strip().upper() for c in df_raw.columns]
    return pd.DataFrame({
        "symbol": df_raw["SYMBOL"].str.strip(),
        "series": df_raw["SERIES"].str.strip(),
        "business_date": business_date,
        "open": pd.to_numeric(df_raw["OPEN_PRICE"], errors="coerce"),
        "high": pd.to_numeric(df_raw["HIGH_PRICE"], errors="coerce"),
        "low": pd.to_numeric(df_raw["LOW_PRICE"], errors="coerce"),
        "close": pd.to_numeric(df_raw["CLOSE_PRICE"], errors="coerce"),
        "prev_close": pd.to_numeric(df_raw["PREV_CLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df_raw["TTL_TRD_QNTY"], errors="coerce").fillna(0).astype("int64"),
        "turnover_lacs": pd.to_numeric(df_raw["TURNOVER_LACS"], errors="coerce"),
    })


def _detect_and_parse(df_raw: pd.DataFrame, business_date: date) -> pd.DataFrame:
    cols = {c.strip().upper() for c in df_raw.columns}
    if "CLOSE_PRICE" in cols:
        return _parse_new_format(df_raw, business_date)
    return _parse_old_format(df_raw, business_date)


def add_pit_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    """Stamp each row: as_of_timestamp = business_date 18:00 IST in UTC.

    Public so tests can call it directly.
    """
    bd = pd.to_datetime(df["business_date"])
    # 18:00 IST = 12:30 UTC (IST is UTC+5:30)
    as_of_utc = bd + pd.Timedelta(hours=_AS_OF_OFFSET_HOURS, minutes=_AS_OF_OFFSET_MINUTES)
    df = df.copy()
    df["as_of_timestamp"] = as_of_utc.dt.tz_localize("UTC")
    return df


def parse_zip(zip_bytes: bytes, business_date: date) -> pd.DataFrame | None:
    """Extract CSV from ZIP bytes and return a normalised + PIT-stamped DataFrame.

    Public so tests can feed synthetic ZIP bytes without network access.
    Returns None if the ZIP is malformed or the CSV is empty.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                logger.warning("bhavcopy_parse_no_csv date=%s", business_date)
                return None
            with zf.open(csv_names[0]) as f:
                df_raw = pd.read_csv(f, dtype=str)
    except Exception as exc:
        logger.warning("bhavcopy_parse_error date=%s error=%s", business_date, exc)
        return None

    if df_raw.empty:
        return None

    df = _detect_and_parse(df_raw, business_date)
    df = add_pit_timestamp(df)
    df = df.dropna(subset=["close"])
    return df if not df.empty else None


# ── Year-partitioned persistence ──────────────────────────────────────────────

def _read_year(year: int) -> pd.DataFrame | None:
    path = year_path(year)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _write_year(year: int, df: pd.DataFrame) -> None:
    df = df.sort_values(["business_date", "symbol"]).reset_index(drop=True)
    year_path(year).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(year_path(year), index=False)
    logger.info("bhavcopy_write year=%d rows=%d", year, len(df))


def _merge_and_flush(year: int, new_rows: pd.DataFrame) -> None:
    """Merge new_rows into the existing year parquet, overwriting any duplicate dates."""
    existing = _read_year(year)
    if existing is not None and not existing.empty:
        overwrite_dates = set(new_rows["business_date"].astype(str))
        existing = existing[~existing["business_date"].astype(str).isin(overwrite_dates)]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    _write_year(year, combined)


# ── Trading day iteration ─────────────────────────────────────────────────────

def _weekday_range(start: date, end: date):
    """Yield Mon–Fri dates in [start, end]. NSE holidays handled by 404."""
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


# ── Main ingest function ──────────────────────────────────────────────────────

def ingest_range(
    start: date | str,
    end: date | str | None = None,
    *,
    refresh: bool = False,
) -> dict[int, int]:
    """Ingest NSE Bhavcopy for [start, end] and write year-partitioned parquets.

    Skips weekends and NSE holidays (404 → no file published).
    Already-ingested dates are skipped unless refresh=True.

    Returns:
        {year: rows_written} — rows written (or re-written) during this run.
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if end is None:
        end = date.today() - timedelta(days=1)
    elif isinstance(end, str):
        end = date.fromisoformat(end)

    logger.info("bhavcopy_ingest start=%s end=%s refresh=%s", start, end, refresh)

    # Build skip-set of already-ingested dates (keyed by year)
    already_done: dict[int, set[str]] = {}
    if not refresh:
        for year in range(start.year, end.year + 1):
            existing = _read_year(year)
            if existing is not None:
                already_done[year] = set(existing["business_date"].astype(str))

    pending: dict[int, list[pd.DataFrame]] = {}
    rows_written: dict[int, int] = {}

    candidates = list(_weekday_range(start, end))
    total = len(candidates)

    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        # Seed session cookie — some NSE endpoints require it
        try:
            client.get("https://www.nseindia.com/", timeout=15)
        except Exception:
            pass  # archive CDN usually works without a session

        for i, dt in enumerate(candidates, 1):
            year = dt.year
            dt_str = dt.isoformat()

            if not refresh and dt_str in already_done.get(year, set()):
                continue

            if i % 50 == 0 or i == total:
                logger.info("bhavcopy_progress %d/%d date=%s", i, total, dt_str)

            zip_bytes = _download_bhavcopy(dt, client)
            if zip_bytes is None:
                continue   # holiday or future date

            df_day = parse_zip(zip_bytes, dt)
            if df_day is None:
                continue

            pending.setdefault(year, []).append(df_day)
            rows_written[year] = rows_written.get(year, 0) + len(df_day)

            # Flush periodically to cap memory use
            if len(pending.get(year, [])) >= _FLUSH_EVERY_N_DAYS:
                _merge_and_flush(year, pd.concat(pending.pop(year), ignore_index=True))

            time.sleep(_REQUEST_DELAY_S)

    # Final flush
    for year, frames in pending.items():
        if frames:
            _merge_and_flush(year, pd.concat(frames, ignore_index=True))

    logger.info(
        "bhavcopy_ingest_complete total_rows=%d by_year=%s",
        sum(rows_written.values()), rows_written,
    )
    return rows_written


# ── CLI ───────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Ingest NSE source data into the L1 PIT lake."
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["nse_bhavcopy"],
        help="Data source to ingest.",
    )
    parser.add_argument(
        "--from", dest="from_date", required=True, metavar="YYYY-MM-DD",
        help="Start date (inclusive).",
    )
    parser.add_argument(
        "--to", dest="to_date", default=None, metavar="YYYY-MM-DD",
        help="End date (inclusive). Defaults to yesterday.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Re-download and overwrite already-ingested dates.",
    )
    args = parser.parse_args()

    result = ingest_range(
        start=args.from_date,
        end=args.to_date,
        refresh=args.refresh,
    )
    print(f"\nIngest complete. Rows written by year: {result}")


if __name__ == "__main__":
    _main()
