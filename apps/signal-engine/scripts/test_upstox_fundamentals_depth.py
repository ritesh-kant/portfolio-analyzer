"""
Test Upstox Company Fundamentals API historical depth.

Goal: verify whether the free Upstox API has quarterly P&L data going back to
2015 for Nifty Midcap 150 symbols — the training window we need for PEAD ML.

Usage:
    # Option A: if you have an Upstox access token already
    python scripts/test_upstox_fundamentals_depth.py --token YOUR_ACCESS_TOKEN

    # Option B: unauthenticated probe (Upstox docs say fundamentals endpoint
    # is public — no auth required for read-only company data)
    python scripts/test_upstox_fundamentals_depth.py --no-auth

What it does:
1. Picks 3 Midcap 150 symbols (large, mid, small by market-cap within the index)
2. Hits the Upstox fundamentals endpoint for each with frequency=quarterly
3. Prints the earliest date in the returned data
4. Summarises: can we get back to Q1-2015 (2015-04)?
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional, Tuple

try:
    import requests
except ImportError:
    print("requests not installed — run: pip3 install requests")
    sys.exit(1)

# Three representative Midcap 150 tickers (ISIN + exchange symbol)
# Using NSE instrument keys as Upstox expects: "NSE_EQ|INE..."
# Covering large (PIDILITIND), mid (MPHASIS), small (IIFL) within the index
TEST_SYMBOLS = [
    ("PIDILITIND", "NSE_EQ|INE318A01026"),
    ("MPHASIS",    "NSE_EQ|INE356A01018"),
    ("IIFL",       "NSE_EQ|INE530B01024"),
]

BASE_URL = "https://api.upstox.com/v2"


def fetch_fundamentals(instrument_key: str, token: Optional[str]) -> dict:
    url = f"{BASE_URL}/instruments/fundamentals"
    params = {
        "instrument_key": instrument_key,
        "frequency": "quarterly",
    }
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def earliest_quarter(data: dict) -> Optional[str]:
    """Extract earliest date from Upstox fundamentals response."""
    # Upstox response structure (from docs):
    #   data.financials[].metadata.date  OR  data.financial_data[].date
    # Try both shapes.
    try:
        financials = data.get("data", {}).get("financials", [])
        if financials:
            dates = [f.get("metadata", {}).get("date", "") for f in financials]
            dates = [d for d in dates if d]
            return min(dates) if dates else None
    except Exception:
        pass

    try:
        rows = data.get("data", {}).get("financial_data", [])
        if rows:
            dates = [r.get("date", "") for r in rows]
            dates = [d for d in dates if d]
            return min(dates) if dates else None
    except Exception:
        pass

    return None


def check_depth(earliest: Optional[str]) -> Tuple[bool, str]:
    """Return (passes, message) — passes=True if data goes back to 2015."""
    target = datetime(2015, 4, 1)  # Q1 FY2016 (April 2015)
    if earliest is None:
        return False, "could not parse date from response"
    try:
        dt = datetime.fromisoformat(earliest.rstrip("Z")[:10])
        ok = dt <= target
        return ok, f"earliest={earliest}  target={target.date()}  {'✓ PASS' if ok else '✗ FAIL'}"
    except ValueError:
        return False, f"unparseable date: {earliest!r}"


def main():
    parser = argparse.ArgumentParser(description="Test Upstox fundamentals API depth")
    parser.add_argument("--token", default=None, help="Upstox access token (optional)")
    parser.add_argument("--no-auth", action="store_true", help="Skip auth header entirely")
    parser.add_argument("--raw", action="store_true", help="Dump raw JSON response for first symbol")
    args = parser.parse_args()

    token = None if args.no_auth else args.token
    results = []

    for name, ikey in TEST_SYMBOLS:
        print(f"\n{'─'*60}")
        print(f"Symbol : {name}")
        print(f"Key    : {ikey}")
        try:
            data = fetch_fundamentals(ikey, token)
            if args.raw and name == TEST_SYMBOLS[0][0]:
                print("RAW RESPONSE (first symbol):")
                print(json.dumps(data, indent=2)[:4000])
            earliest = earliest_quarter(data)
            ok, msg = check_depth(earliest)
            print(f"Depth  : {msg}")
            results.append((name, ok))
        except requests.HTTPError as e:
            print(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
            results.append((name, False))
        except Exception as e:
            print(f"Error  : {e}")
            results.append((name, False))

    print(f"\n{'═'*60}")
    passes = sum(1 for _, ok in results if ok)
    print(f"Result : {passes}/{len(results)} symbols have data back to Q1-2015")
    if passes == len(results):
        print("Decision: Upstox FREE API covers training window — no paid data needed.")
    elif passes > 0:
        print("Decision: Partial coverage — verify which symbols are shallow before relying on it.")
    else:
        print("Decision: Upstox API too shallow — consider EODHD (€59.99/month) or Bharat-SM-Data.")
    print('═'*60)


if __name__ == "__main__":
    main()
