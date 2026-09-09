"""Upstox API client: instrument master, historical / intraday candles, and the
V3 market-data WebSocket (via the official SDK for protobuf decoding).

Docs: https://upstox.com/developer/api-documentation/
  * Historical candle V3:  GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  * Intraday candle V3:    GET /v3/historical-candle/intraday/{key}/{unit}/{interval}
  * Instrument master:     https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
  * Feed V3:               wss://api.upstox.com/v3/feed/market-data-feed (SDK MarketDataStreamerV3)

Rate limits (documented): 50 req/s, 500 req/min, 2000 req/30 min. The client
paces at half of each.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

from .bars import COLS, IST

logger = logging.getLogger(__name__)

API_BASE = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class UpstoxAuthError(RuntimeError):
    """401 from Upstox — the configured market-data credential was rejected."""


class _Pacer:
    """Sliding-window rate limiter just under the documented limits
    (50/s, 500/min, 2000/30 min); 429s are retried with backoff by the caller."""

    def __init__(self, per_sec: int = 40, per_min: int = 450, per_30min: int = 1900) -> None:
        self._limits = ((1.0, per_sec), (60.0, per_min), (1800.0, per_30min))
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                while self._hits and now - self._hits[0] > 1800.0:
                    self._hits.popleft()
                sleep_for = 0.0
                for window, limit in self._limits:
                    recent = [h for h in self._hits if now - h <= window]
                    if len(recent) >= limit:
                        sleep_for = max(sleep_for, window - (now - recent[0]) + 0.01)
                if sleep_for <= 0:
                    self._hits.append(now)
                    return
                time.sleep(sleep_for)


@dataclass(frozen=True)
class Instrument:
    symbol: str          # NSE trading symbol, e.g. RELIANCE
    isin: str
    key: str             # Upstox instrument_key, e.g. NSE_EQ|INE002A01018
    name: str
    tick_size: float


def candles_to_frame(candles: list[list[object]]) -> pd.DataFrame:
    """Upstox candle rows are [ts, open, high, low, close, volume, oi], newest first."""
    if not candles:
        return pd.DataFrame(columns=COLS, dtype=float)
    df = pd.DataFrame(candles).iloc[:, :6]
    df.columns = ["ts", *COLS]
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST)
    df = df.set_index("ts").sort_index()
    return df.astype(float)[COLS]


class UpstoxClient:
    def __init__(self, access_token: str | None, cache_dir: Path | None = None) -> None:
        self.token = access_token or ""
        self.cache_dir = cache_dir
        self._pacer = _Pacer()
        self._http = httpx.Client(timeout=_TIMEOUT, headers={"Accept": "application/json"})

    # ── low level ─────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, str] | None = None, auth: bool = True) -> dict:
        self._pacer.wait()
        headers = {"Authorization": f"Bearer {self.token}"} if (auth and self.token) else {}
        for attempt in range(4):
            r = self._http.get(f"{API_BASE}{path}", params=params, headers=headers)
            if r.status_code == 401:
                raise UpstoxAuthError(r.text[:200])
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("status") != "success":
                raise RuntimeError(f"upstox {path}: {body}")
            data = body.get("data", {})
            return dict(data) if isinstance(data, dict) else {}
        r.raise_for_status()
        return {}

    # ── instruments ───────────────────────────────────────────────────────────

    def nse_equities(self) -> dict[str, Instrument]:
        """trading_symbol → Instrument for NSE_EQ / EQ series. Cached on disk for a day."""
        cache = (self.cache_dir / "NSE.json.gz") if self.cache_dir else None
        raw: bytes | None = None
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < 86_400:
            raw = cache.read_bytes()
        if raw is None:
            r = self._http.get(INSTRUMENTS_URL)
            r.raise_for_status()
            raw = r.content
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        rows = json.loads(gzip.decompress(raw))
        out: dict[str, Instrument] = {}
        for x in rows:
            if x.get("segment") != "NSE_EQ" or x.get("instrument_type") != "EQ":
                continue
            out[x["trading_symbol"]] = Instrument(
                symbol=x["trading_symbol"], isin=x.get("isin", ""), key=x["instrument_key"],
                name=x.get("name", ""), tick_size=float(x.get("tick_size", 5.0)) / 100.0,
            )
        return out

    # ── candles ───────────────────────────────────────────────────────────────

    def historical(
        self, key: str, unit: str, interval: int, start: date, end: date
    ) -> pd.DataFrame:
        """Historical candles; paginates monthly for minute data (Upstox caps a
        1-min request at ~1 month). Returns ascending, IST-indexed."""
        frames: list[pd.DataFrame] = []
        chunk = timedelta(days=28) if unit == "minutes" else timedelta(days=365 * 3)
        cur = start
        while cur <= end:
            to = min(cur + chunk, end)
            path = (f"/v3/historical-candle/{key}/{unit}/{interval}"
                    f"/{to.isoformat()}/{cur.isoformat()}")
            try:
                data = self._get(path, auth=bool(self.token))
                part = candles_to_frame(data.get("candles", []))
                if not part.empty:
                    frames.append(part)
            except httpx.HTTPStatusError as exc:
                logger.warning("historical %s %s..%s failed: %s", key, cur, to, exc)
            cur = to + timedelta(days=1)
        if not frames:
            return pd.DataFrame(columns=COLS, dtype=float)
        df = pd.concat(frames).astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def historical_1m(self, key: str, start: date, end: date) -> pd.DataFrame:
        return self.historical(key, "minutes", 1, start, end)

    def daily(self, key: str, start: date, end: date) -> pd.DataFrame:
        df = self.historical(key, "days", 1, start, end)
        if not df.empty:
            df.index = df.index.normalize()
        return df

    def intraday_1m(self, key: str) -> pd.DataFrame:
        """Today's 1-min candles so far (used to seed the bar builder on a late start)."""
        path = f"/v3/historical-candle/intraday/{key}/minutes/1"
        return candles_to_frame(self._get(path, auth=bool(self.token)).get("candles", []))

    # ── cached 1-min history for the backtest ────────────────────────────────

    def cached_1m(self, inst: Instrument, start: date, end: date) -> pd.DataFrame:
        """Per-symbol parquet cache, one file per calendar year, with a sidecar
        recording the date range the file covers. A file is reused only if it
        covers the requested slice of that year; otherwise the slice is fetched
        and merged (so a 3-month smoke run cannot poison a 2-year run)."""
        if self.cache_dir is None:
            return self.historical_1m(inst.key, start, end)
        frames: list[pd.DataFrame] = []
        for year in range(start.year, end.year + 1):
            stem = self.cache_dir / "1m" / f"{inst.symbol.replace('&', '_')}_{year}"
            f, meta = stem.with_suffix(".parquet"), stem.with_suffix(".range.json")
            want0, want1 = max(date(year, 1, 1), start), min(date(year, 12, 31), end)
            want1 = min(want1, date.today() - timedelta(days=1))
            if want0 > want1:
                continue
            have: pd.DataFrame | None = None
            cov0 = cov1 = None
            if f.exists() and meta.exists():
                rng = json.loads(meta.read_text())
                cov0, cov1 = date.fromisoformat(rng["start"]), date.fromisoformat(rng["end"])
                have = pd.read_parquet(f)
                if cov0 <= want0 and cov1 >= want1 and not have.empty:
                    frames.append(have.astype(float))
                    continue
            fetched = self.historical_1m(inst.key, want0, want1)
            parts = [x for x in (have, fetched) if x is not None and not x.empty]
            merged = (pd.concat(parts).astype(float) if parts
                      else pd.DataFrame(columns=COLS, dtype=float))
            if not merged.empty:
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            # coverage = union only when the old and new ranges touch; else the new range
            if cov0 is not None and cov1 is not None and cov0 <= want1 + timedelta(days=1) \
                    and want0 <= cov1 + timedelta(days=1):
                new0, new1 = min(cov0, want0), max(cov1, want1)
            else:
                new0, new1 = want0, want1
            f.parent.mkdir(parents=True, exist_ok=True)
            merged.to_parquet(f)
            meta.write_text(json.dumps({"start": new0.isoformat(), "end": new1.isoformat()}))
            frames.append(merged)
        frames = [x for x in frames if not x.empty]
        if not frames:
            return pd.DataFrame(columns=COLS, dtype=float)
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[(df.index.date >= start) & (df.index.date <= end)]

    # ── websocket ─────────────────────────────────────────────────────────────

    def stream(
        self,
        keys: list[str],
        on_tick: Callable[[str, int, float, float | None], None],
        mode: str = "full",
        on_status: Callable[[str], None] | None = None,
    ) -> object:
        """Open the V3 feed and route ticks to `on_tick(key, ltt_ms, ltp, vtt)`.

        Returns the SDK streamer (call .disconnect()). FULL mode carries `vtt`
        (volume traded today) which the bar builder needs; LTPC does not.
        """
        import upstox_client  # heavy import (protobuf); keep it lazy

        cfg = upstox_client.Configuration()
        cfg.access_token = self.token
        api = upstox_client.ApiClient(cfg)
        streamer = upstox_client.MarketDataStreamerV3(api, keys, mode)

        def _msg(message: dict) -> None:
            feeds = message.get("feeds") or {}
            for key, feed in feeds.items():
                ltpc = feed.get("ltpc")
                vtt: float | None = None
                if ltpc is None:
                    ff = (feed.get("fullFeed") or {}).get("marketFF") or {}
                    ltpc = ff.get("ltpc")
                    if ff.get("vtt") is not None:
                        vtt = float(ff["vtt"])
                if not ltpc or "ltp" not in ltpc:
                    continue
                try:
                    on_tick(key, int(ltpc.get("ltt", 0)), float(ltpc["ltp"]), vtt)
                except Exception:  # noqa: BLE001 — never let a bad tick kill the feed thread
                    logger.exception("on_tick failed for %s", key)

        streamer.on("message", _msg)
        if on_status:
            streamer.on("open", lambda *_: on_status("open"))
            streamer.on("close", lambda *_: on_status("close"))
            streamer.on("error", lambda e, *_: on_status(f"error: {e}"))
        streamer.auto_reconnect(True, 5, 20)
        streamer.connect()
        return streamer
