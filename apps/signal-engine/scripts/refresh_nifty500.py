"""Refresh the Nifty 500 liquid universe file.

Run this every 6 months, aligned with NSE's semi-annual rebalance (March / September):

    cd apps/signal-engine
    python scripts/refresh_nifty500.py

What it does:
  1. Downloads the current Nifty 500 constituent list from NSE archives (CSV).
  2. Parses the Symbol column.
  3. Overwrites src/news_trader/nifty500.py with a fresh frozenset.

No DB, no AWS, no side effects — just regenerates the Python file in-place.
Commit the updated file after running.
"""

import csv
import io
import pathlib
from datetime import date

import httpx

# NSE archives CSV — public static file, no session management needed.
_NSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
_FALLBACK_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"

_OUTPUT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "news_trader" / "nifty500.py"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}


def _fetch_via_csv() -> set[str]:
    print(f"Fetching {_NSE_CSV_URL} ...")
    r = httpx.get(_NSE_CSV_URL, headers=_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    symbols = {row["Symbol"].strip() for row in reader if row.get("Symbol")}
    if not symbols:
        raise ValueError("CSV parsed but no symbols found — column name may have changed")
    return symbols


def _fetch_via_api() -> set[str]:
    """Fallback: NSE JSON API (requires a session cookie first)."""
    print(f"CSV fetch failed, trying JSON API: {_FALLBACK_URL} ...")
    with httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
        # Establish session cookie
        client.get("https://www.nseindia.com/")
        r = client.get(_FALLBACK_URL)
        r.raise_for_status()
    data = r.json().get("data", [])
    symbols = {row["symbol"].strip() for row in data if row.get("symbol")}
    if not symbols:
        raise ValueError("API returned data but no symbols found")
    return symbols


def _fetch_symbols() -> set[str]:
    try:
        return _fetch_via_csv()
    except Exception as e:
        print(f"  CSV fetch failed: {e}")
        return _fetch_via_api()


def _write_file(symbols: set[str], today: date) -> None:
    sorted_syms = sorted(symbols)

    # 5 symbols per row, each row indented 4 spaces inside the frozenset literal
    rows: list[str] = []
    chunk: list[str] = []
    for i, sym in enumerate(sorted_syms):
        chunk.append(f'"{sym}"')
        if len(chunk) == 5 or i == len(sorted_syms) - 1:
            rows.append("    " + ", ".join(chunk) + ",")
            chunk = []
    body = "\n".join(rows)

    # Build with explicit concatenation — avoids textwrap.dedent + f-string
    # multi-line interpolation quirks that corrupt indentation.
    lines = [
        '"""NSE liquid stock universe — Nifty 500 constituents.',
        "",
        "Used by trade_decision to gate positions to tradeable, liquid names.",
        "Microcaps outside this set are skipped: ₹50k position sizes can materially",
        "move thin stocks, and exit slippage becomes unpredictable in live trading.",
        "",
        "MAINTENANCE: refresh every 6 months (NSE rebalances in March and September):",
        "    cd apps/signal-engine",
        "    python scripts/refresh_nifty500.py",
        "",
        f"Last updated: {today} ({len(symbols)} symbols)",
        '"""',
        "",
        "# fmt: off",
        "NIFTY_500: frozenset[str] = frozenset({",
        body,
        "})",
        "# fmt: on",
        "",
        "",
        "def is_liquid(symbol: str) -> bool:",
        '    """Return True if symbol is in the Nifty 500 liquid universe."""',
        "    return symbol.upper() in NIFTY_500",
        "",
    ]
    _OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    symbols = _fetch_symbols()
    print(f"  Fetched {len(symbols)} symbols.")

    today = date.today()
    _write_file(symbols, today)

    print(f"  Written to {_OUTPUT}")
    print(f"\nDone. Commit the updated nifty500.py before deploying.")
    print(f"Next refresh due: ~{date(today.year + (1 if today.month >= 9 else 0), 3 if today.month >= 9 else 9, 1)}")


if __name__ == "__main__":
    main()
