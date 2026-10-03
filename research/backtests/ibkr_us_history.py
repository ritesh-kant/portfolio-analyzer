"""Download read-only IBKR US stock 1-minute history for a reproducible replay.

Run from the repository root with IB Gateway signed in and its socket API
enabled (Configure → Settings → API → Settings; note the port it shows):

    apps/signal-engine/.venv/bin/python research/backtests/ibkr_us_history.py \
      --symbols AAPL,MSFT --start 2026-01-01 --end 2026-10-02

The API connection requests read-only mode; this module has no order methods.
Bars are regular-hours TRADES, indexed in America/New_York, one parquet per
contract. Each downloaded week is checkpointed before the next API request.

An empty week is never assumed to be a quiet week. IBKR answers both "there is
nothing in this window" and "this request failed" with an empty bar list, so
every reply is judged before the cursor moves: only an explicit no-data verdict
steps a week back (printed, and recorded in the manifest as a gap), a pacing
violation or a timeout is retried, and anything else stops the run. Stepping
over a failed week would leave a hole that no later reader could tell apart
from a holiday.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "research" / "backtests" / ".cache_ibkr_us"
ET = "America/New_York"
COLUMNS = ("open", "high", "low", "close", "volume")
WEEK = timedelta(days=7)

# ── judging an empty reply ──────────────────────────────────────────────────
# IBKR explains itself on errorEvent, keyed by request id. The bar list it
# returns when a request fails is empty and carries the same id, so the two can
# be paired and the failure told apart from an empty window.
GAP_MARKERS = ("no data", "hmds query returned no data")
PACING_MARKERS = ("pacing violation",)
FATAL_MARKERS = ("no market data permission", "not subscribed", "invalid contract")

_REQUEST_ERRORS: dict[int, list[str]] = {}


@dataclass(frozen=True)
class Settings:
    """Download knobs. `pause` spaces requests out politely; the retry numbers
    are how a failure that is not an absence of data is answered."""
    start: date
    end: date
    pause: float = 1.0
    retries: int = 5
    retry_wait: float = 15.0
    cooldown: float = 600.0
    timeout: float = 90.0


def _on_error(req_id: int, code: int, message: str, _contract: object = None) -> None:
    if req_id != -1:
        _REQUEST_ERRORS.setdefault(req_id, []).append(f"{code} {message}")


def _verdict(messages: list[str]) -> tuple[str, str]:
    """Read an empty bar list as ('gap' | 'transient' | 'fatal', its reason)."""
    if not messages:
        return "transient", "no reply before the request timeout"
    detail = "; ".join(messages)
    text = detail.lower()
    if any(marker in text for marker in FATAL_MARKERS):
        return "fatal", detail
    if any(marker in text for marker in PACING_MARKERS):
        return "transient", detail
    if any(marker in text for marker in GAP_MARKERS):
        return "gap", detail
    return "transient", detail


def _frame(bars: list) -> pd.DataFrame:
    """Convert IBKR bars without manufacturing missing or zero-volume minutes."""
    if not bars:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=ET))
    rows = [{"date": b.date, **{col: float(getattr(b, col)) for col in COLUMNS}}
            for b in bars]
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.pop("date"), utc=True).dt.tz_convert(ET)
    frame.index.name = "date"
    return frame.sort_index().loc[:, COLUMNS]


def _merge(path: Path, fresh: pd.DataFrame) -> pd.DataFrame:
    if path.exists():
        old = pd.read_parquet(path)
        if old.index.tz is None:
            raise ValueError(f"{path}: cached timestamps have no timezone")
        fresh = pd.concat([old, fresh])
    fresh = fresh.sort_index()
    fresh = fresh[~fresh.index.duplicated(keep="last")]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    fresh.to_parquet(temporary)
    temporary.replace(path)
    return fresh


def _symbols(args: argparse.Namespace) -> list[str]:
    names = (args.symbols or "").split(",")
    if args.symbols_file:
        names += args.symbols_file.read_text().replace(",", "\n").splitlines()
    symbols = sorted({name.strip().upper() for name in names if name.strip()})
    if not symbols:
        raise SystemExit("Provide --symbols or --symbols-file")
    if any(not name.replace(".", "").replace("-", "").isalnum() for name in symbols):
        raise SystemExit("Symbols must contain only letters, digits, dot or hyphen")
    return symbols


def _page(ib, contract, stop: datetime, symbol: str,
          settings: Settings) -> tuple[pd.DataFrame, str, str]:
    """One weekly request, retried while the failure is a transient one.

    Returns the bars (present, or empty for a real absence) with the verdict
    that explains an empty one. A request that failed is never reported as
    empty: it is retried, and the run stops if it cannot be made to answer."""
    for attempt in range(1, settings.retries + 1):
        bars = ib.reqHistoricalData(
            contract, endDateTime=stop, durationStr="1 W", barSizeSetting="1 min",
            whatToShow="TRADES", useRTH=True, formatDate=2, keepUpToDate=False,
            timeout=settings.timeout,
        )
        frame = _frame(bars)
        if not frame.empty:
            return frame, "data", ""
        kind, detail = _verdict(_REQUEST_ERRORS.pop(bars.reqId, []))
        if kind == "gap":
            return frame, kind, detail
        if kind == "fatal":
            raise RuntimeError(f"{symbol}: IBKR refused the request — {detail}")
        wait = settings.cooldown if "pacing" in detail.lower() else settings.retry_wait * attempt
        if attempt == settings.retries:
            raise RuntimeError(f"{symbol}: {attempt} attempts failed — {detail}")
        print(f"{symbol}: {detail} — retry {attempt}/{settings.retries} in {wait:.0f}s",
              flush=True)
        time.sleep(wait)
    raise RuntimeError(f"{symbol}: unreachable")


def fetch_symbol(ib, symbol: str, settings: Settings, cache: Path) -> dict:
    from ib_async import Stock

    contracts = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
    if len(contracts) != 1 or contracts[0] is None or contracts[0].secType != "STK":
        raise ValueError(f"{symbol}: IBKR did not resolve one US stock contract")
    contract = contracts[0]
    # One file per contract per start year, so a download for another year
    # cannot land in a 2026 file and be read as 2026 data.
    path = cache / "1m" / f"{symbol}_{contract.conId}_{settings.start.year}.parquet"
    # IBKR endDateTime is exclusive. Noon UTC on the next day includes the
    # entire prior US regular session in both EST and EDT.
    stop = datetime.combine(settings.end + timedelta(days=1), datetime.min.time(), timezone.utc)
    stop += timedelta(hours=12)
    floor = datetime.combine(settings.start, datetime.min.time(), timezone.utc)
    pages, gaps = 0, []
    if path.exists():
        old = pd.read_parquet(path)
        if not old.empty:
            oldest = old.index.min().tz_convert("UTC").to_pydatetime()
            newest = old.index.max().tz_convert(ET).date()
            if newest >= settings.end:
                stop = min(stop, oldest - timedelta(seconds=1))
            else:
                # An incomplete newer range cannot safely be extended from its
                # oldest timestamp; re-fetch from the requested end and dedupe.
                print(f"{symbol}: refreshing newer range through {settings.end}", flush=True)
    while stop > floor:
        frame, kind, detail = _page(ib, contract, stop, symbol, settings)
        pages += 1
        if kind == "gap":
            window = f"{(stop - WEEK).date()}..{stop.date()}"
            gaps.append(window)
            print(f"{symbol}: page {pages}, no data in {window} — {detail}", flush=True)
            stop -= WEEK
        else:
            earliest = frame.index.min().tz_convert("UTC").to_pydatetime()
            kept = frame[(frame.index.date >= settings.start)
                         & (frame.index.date <= settings.end)]
            if not kept.empty:
                _merge(path, kept)
            if earliest >= stop:
                raise RuntimeError(f"{symbol}: IBKR did not move the history cursor")
            stop = earliest - timedelta(seconds=1)
            print(f"{symbol}: page {pages}, {len(kept)} bars; cursor {stop.date()}", flush=True)
        if stop > floor and settings.pause:
            time.sleep(settings.pause)
    stored = pd.read_parquet(path) if path.exists() else _frame([])
    return {"symbol": symbol, "conId": contract.conId, "bars": len(stored),
            "first": str(stored.index.min()) if len(stored) else None,
            "last": str(stored.index.max()) if len(stored) else None,
            "pages": pages, "gaps": gaps, "path": str(path)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="Comma-separated US stock symbols")
    ap.add_argument("--symbols-file", type=Path, help="One symbol per line")
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--port", type=int, default=4002,
                    help="Gateway socket API port (Gateway shows it under API → Settings)")
    ap.add_argument("--client-id", type=int, default=71)
    ap.add_argument("--pause", type=float, default=1.0, help="Seconds between pages")
    ap.add_argument("--retries", type=int, default=5,
                    help="Attempts per page when a request fails rather than empties")
    ap.add_argument("--retry-wait", type=float, default=15.0,
                    help="Seconds before a retry, multiplied by the attempt number")
    ap.add_argument("--cooldown", type=float, default=600.0,
                    help="Seconds to wait after a pacing violation")
    ap.add_argument("--timeout", type=float, default=90.0, help="Seconds per request")
    ap.add_argument("--cache", type=Path, default=CACHE)
    args = ap.parse_args()
    if args.start > args.end:
        ap.error("--start must be on or before --end")
    settings = Settings(start=args.start, end=args.end, pause=args.pause,
                        retries=args.retries, retry_wait=args.retry_wait,
                        cooldown=args.cooldown, timeout=args.timeout)
    symbols = _symbols(args)
    try:
        from ib_async import IB, StartupFetch
    except ImportError as exc:
        raise SystemExit("Install optional dependency: uv pip install --python "
                         "apps/signal-engine/.venv/bin/python 'ib-async>=2.1,<3'") from exc
    ib = IB()
    ib.errorEvent.connect(_on_error)
    results: list[dict] = []
    skipped: list[dict] = []
    failed = None
    try:
        try:
            ib.connect("127.0.0.1", args.port, clientId=args.client_id,
                       readonly=True, timeout=10, fetchFields=StartupFetch(0))
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise SystemExit(
                f"Cannot connect to IB Gateway on 127.0.0.1:{args.port}. "
                "Enable the socket API in Gateway (Configure -> Settings -> API "
                "-> Settings), check the port shown there, and keep 127.0.0.1 in "
                "the trusted IPs."
            ) from exc
        if not ib.isConnected():
            raise ConnectionError("IB Gateway is not connected")
        for symbol in symbols:
            try:
                results.append(fetch_symbol(ib, symbol, settings, args.cache))
            except ValueError as exc:
                # Not a US stock IBKR will serve (a unit, a warrant, a typo).
                # Recorded, not passed over: the batch carries on without it.
                skipped.append({"symbol": symbol, "reason": str(exc)})
                print(f"skipped {exc}", flush=True)
            except RuntimeError as exc:
                # Data that could not be fetched. Stopping keeps the manifest
                # honest about what the cache does and does not hold.
                failed = str(exc)
                print(f"stopped: {failed}", flush=True)
                break
    finally:
        ib.disconnect()
    manifest: dict[str, object] = {
        "source": "IBKR Gateway", "whatToShow": "TRADES", "useRTH": True,
        "barSize": "1 min", "requested_start": str(args.start),
        "requested_end": str(args.end), "contracts": results, "skipped": skipped}
    if failed:
        manifest["failed"] = failed
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)
    if failed:
        raise SystemExit(f"incomplete: {failed}")


if __name__ == "__main__":
    main()
