"""Refresh the NSE index universe files (Nifty 500 whitelist + Nifty 50 exclusion set).

Run this every 6 months, aligned with NSE's semi-annual rebalance (March / September):

    cd apps/signal-engine
    python scripts/refresh_nifty500.py

What it does, for each index:
  1. Downloads the current constituent list from NSE archives (CSV).
  2. Parses the Symbol column.
  3. Overwrites the corresponding src/news_trader/*.py file with a fresh frozenset.

No DB, no AWS, no side effects — just regenerates the Python files in-place.
Commit the updated files after running.
"""

import csv
import io
import pathlib
from datetime import date
from typing import TypedDict

import httpx

_SRC_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "news_trader"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}

_MAINTENANCE_LINES = [
    "MAINTENANCE: refresh every 6 months (NSE rebalances in March and September):",
    "    cd apps/signal-engine",
    "    python scripts/refresh_nifty500.py",
]


class _Index(TypedDict):
    csv_url: str       # NSE archives CSV — public static file, no session management needed
    api_url: str       # fallback: NSE JSON API (requires a session cookie first)
    output: pathlib.Path
    varname: str
    header: list[str]  # module docstring body lines (between summary and MAINTENANCE)
    footer: list[str]  # extra module-level code after the frozenset


_INDICES: dict[str, _Index] = {
    "Nifty 500": {
        "csv_url": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "api_url": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500",
        "output": _SRC_DIR / "nifty500.py",
        "varname": "NIFTY_500",
        "header": [
            '"""NSE liquid stock universe — Nifty 500 constituents.',
            "",
            "Used by trade_decision to gate positions to tradeable, liquid names.",
            "Microcaps outside this set are skipped: ₹50k position sizes can materially",
            "move thin stocks, and exit slippage becomes unpredictable in live trading.",
        ],
        "footer": [
            "",
            "",
            "def is_liquid(symbol: str) -> bool:",
            '    """Return True if symbol is in the Nifty 500 liquid universe."""',
            "    return symbol.upper() in NIFTY_500",
        ],
    },
    "Nifty 50": {
        "csv_url": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
        "api_url": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
        "output": _SRC_DIR / "nifty50.py",
        "varname": "NIFTY_50",
        "header": [
            '"""NIFTY 50 large-cap exclusion set.',
            "",
            "Used by trade_decision to EXCLUDE large-caps from news-trading (opposite role",
            "to nifty500.NIFTY_500, which is the inclusion whitelist). BT5 (2026-06-10,",
            "n=33 closed trades): NIFTY50 names had negative GROSS P&L — the news is fully",
            "priced in before our 15-min delayed entry, so there is no post-entry drift to",
            "capture. Non-large-caps were gross-positive over the same window.",
        ],
        "footer": [],
    },
}


def _fetch_via_csv(url: str) -> set[str]:
    print(f"Fetching {url} ...")
    r = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    symbols = {row["Symbol"].strip() for row in reader if row.get("Symbol")}
    if not symbols:
        raise ValueError("CSV parsed but no symbols found — column name may have changed")
    return symbols


def _fetch_via_api(url: str) -> set[str]:
    print(f"  CSV fetch failed, trying JSON API: {url} ...")
    with httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
        # Establish session cookie
        client.get("https://www.nseindia.com/")
        r = client.get(url)
        r.raise_for_status()
    data = r.json().get("data", [])
    symbols = {row["symbol"].strip() for row in data if row.get("symbol")}
    if not symbols:
        raise ValueError("API returned data but no symbols found")
    return symbols


def _fetch_symbols(index: _Index) -> set[str]:
    try:
        return _fetch_via_csv(index["csv_url"])
    except Exception as e:
        print(f"  CSV fetch failed: {e}")
        return _fetch_via_api(index["api_url"])


def _write_file(index: _Index, symbols: set[str], today: date) -> None:
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
        *index["header"],
        "",
        *_MAINTENANCE_LINES,
        "",
        f"Last updated: {today} ({len(symbols)} symbols)",
        '"""',
        "",
        "# fmt: off",
        f"{index['varname']}: frozenset[str] = frozenset({{",
        body,
        "})",
        "# fmt: on",
        *index["footer"],
        "",
    ]
    index["output"].write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    today = date.today()
    for name, index in _INDICES.items():
        symbols = _fetch_symbols(index)
        print(f"  {name}: fetched {len(symbols)} symbols.")
        _write_file(index, symbols, today)
        print(f"  Written to {index['output']}")

    print("\nDone. Commit the updated universe files before deploying.")
    print(f"Next refresh due: ~{date(today.year + (1 if today.month >= 9 else 0), 3 if today.month >= 9 else 9, 1)}")


if __name__ == "__main__":
    main()
