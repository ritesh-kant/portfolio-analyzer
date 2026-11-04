"""NIFTY (or any NSE index) option chain from Upstox, as ``gex.OptionRow``s.

  * Expiries:     GET /v2/option/contract?instrument_key=NSE_INDEX|Nifty 50
  * Chain:        GET /v2/option/chain?instrument_key=…&expiry_date=YYYY-MM-DD

``oi`` comes back in units (always a multiple of the lot size), ``iv`` in percent.
Works with the read-only analytics token.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx

from src.momentum_trader.upstox import API_BASE, UpstoxAuthError

from .gex import Kind, OptionRow

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_KEY = "NSE_INDEX|Nifty 50"
EXPIRY_TIME = time(15, 30)


class UpstoxChainClient:
    def __init__(self, token: str) -> None:
        self._http = httpx.Client(
            base_url=API_BASE,
            timeout=30.0,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        r = self._http.get(path, params=params)
        if r.status_code == 401:
            raise UpstoxAuthError(r.text[:200])
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success":
            raise RuntimeError(f"upstox {path}: {body}")
        data = body.get("data") or []
        return list(data) if isinstance(data, list) else []

    def expiries(self, key: str = NIFTY_KEY) -> list[date]:
        contracts = self._get("/v2/option/contract", {"instrument_key": key})
        return sorted({date.fromisoformat(c["expiry"]) for c in contracts})

    def chain(self, expiry: date, key: str = NIFTY_KEY) -> tuple[float, list[OptionRow]]:
        """(underlying spot, rows) for one expiry."""
        data = self._get(
            "/v2/option/chain", {"instrument_key": key, "expiry_date": expiry.isoformat()}
        )
        return parse_chain(data, expiry)


def parse_chain(data: list[dict[str, Any]], expiry: date) -> tuple[float, list[OptionRow]]:
    at = datetime.combine(expiry, EXPIRY_TIME, tzinfo=IST)
    spot = float(data[0]["underlying_spot_price"]) if data else 0.0
    rows: list[OptionRow] = []
    sides: tuple[tuple[str, Kind], ...] = (("call_options", "CE"), ("put_options", "PE"))
    for item in data:
        strike = float(item["strike_price"])
        for side, kind in sides:
            opt = item.get(side) or {}
            oi = float((opt.get("market_data") or {}).get("oi") or 0)
            iv = float((opt.get("option_greeks") or {}).get("iv") or 0) / 100.0
            rows.append(OptionRow(expiry=at, strike=strike, kind=kind, oi=oi, iv=iv))
    return spot, rows
