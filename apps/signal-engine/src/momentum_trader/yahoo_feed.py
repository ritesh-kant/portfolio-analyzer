"""Free US market data for the paper arm: Yahoo Finance + the Nasdaq halt feed.

Implements `us_scanner.Feed`. No key, no IB Gateway, no desktop app, so it runs
on Fargate the same way the NSE scanner does. Nothing here places an order.

Why Yahoo, measured rather than assumed (2026-09-24)
----------------------------------------------------
Every free BROKER feed in the US is a slice of the market: IBKR's free tier is
Cboe One + IEX (~15% of volume), Alpaca's is IEX (~3%). A slice misses prints,
and missed prints mean missing wicks — the exact defect that flipped the IKS
entry on NSE (`project_live_bar_sampling`: highs wrong 18/26 minutes, always
too low). Yahoo's minute bars are built from the consolidated tape: summed over
a session they reach 93.0% (SOUN), 91.9% (BBAI) and 96.8% (PLUG) of the day's
volume. The remainder is the opening and closing auctions, which print as
single lumps outside the regular minutes.

What each Feed method uses
--------------------------
snapshot  Yahoo screener. Criteria 2 and 4 (up 10%, $1-$20) and the listed
          exchanges are applied AT THE SOURCE to keep the request count down,
          so the funnel only ever shows rejections on criteria 1, 3 and 5.
          `describe()` says so on every stored watchlist.
bars_1m   Yahoo chart history, regular session only.
facts     Yahoo `floatShares`, cached once per day with `as_of` = that day.
halted    Nasdaq Trader's official halt RSS (all US-listed names, 1-minute TTL,
          with the reason code). Not inferred from gaps in the bars.

What it does NOT do
-------------------
* **Criterion 3 (news).** Yahoo's news list was checked against yesterday's top
  movers and found the cause for 1 of 5 (BENF); WHLR, VSA, ARTL had nothing
  newer than months, IPDN had nothing at all. Most items are articles ABOUT a
  move ("Top Midday Decliners"), which are caused by the price jump and would
  make every mover look catalysed. So `has_catalyst` is left None and Yahoo's
  news is not read at all (a 2026-09-24 audit found 0 of 42 media items on 16
  movers published before the move; SEC EDGAR is the company's own source, see
  `mt-watchlist.ts`). The official `T1` halt code
  ("news pending") is exposed as `news_pending` — rare, but a genuine marker.
* **Guarantees.** Yahoo is unofficial. It can rate-limit or change shape
  without notice. Fine for paper; not something to put money behind.

⚠️ Still unmeasured: how soon minute T's bar is FINAL. `settle_seconds` holds a
bar back after it closes; its default is a guess until `--check` has been run
during a live session. Same open question as the NSE `I1` candle fix.
"""

from __future__ import annotations

import argparse
import json
import logging
import time as _time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .market import US
from .us_screener import USQuote
from .us_universe import USNameFacts, USUniverseConfig

logger = logging.getLogger(__name__)

ET_TZ = US.timezone
COLS = ["open", "high", "low", "close", "volume"]
HALTS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
_NDAQ = "{http://www.nasdaqtrader.com/}"

# Yahoo's exchange codes -> the names `us_universe.allowed_exchanges` uses.
# Without this every name is rejected as `exchange:NMS`.
EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
    "NYQ": "NYSE", "NYS": "NYSE",
    "ASE": "AMEX", "AMX": "AMEX",
    "PCX": "ARCA",
    "BTS": "BATS", "BATS": "BATS",
    "OID": "OTC", "PNK": "OTC", "OQB": "OTC", "OQX": "OTC", "OEM": "OTC", "OBB": "OTC",
}
LISTED_YAHOO_CODES = ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS")

SCREEN_PAGE = 250           # Yahoo's maximum page size
SETTLE_SECONDS = 20         # UNMEASURED — see module docstring
HALTS_TTL_SECONDS = 60      # the RSS declares <ttl>1</ttl>

HALT_REASONS = {
    "LUDP": "LULD pause",
    "LUDS": "LULD straddle",
    "T1": "news pending",
    "T2": "news released",
    "T5": "single-stock pause",
    "T12": "additional information requested",
    "H4": "non-compliance",
    "H10": "SEC trading suspension",
    "H11": "regulatory concern",
    "M": "volatility pause",
    "MWC1": "market-wide circuit breaker",
}


def map_exchange(code: str | None) -> str:
    """Yahoo code -> canonical venue. Unknown codes pass through unchanged so
    the funnel reports `exchange:<code>` instead of silently accepting them."""
    if not code:
        return ""
    return EXCHANGE_MAP.get(code.upper(), code.upper())


def build_query(cfg: USUniverseConfig) -> Any:
    """Criteria 2 and 4 plus listed venues, applied by Yahoo before anything
    comes back. Built from `cfg` so the source and the screen cannot disagree."""
    import yfinance as yf

    return yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("gte", ["percentchange", cfg.day_chg_min_pct]),
        yf.EquityQuery("btwn", ["intradayprice", cfg.price_min, cfg.price_max]),
        yf.EquityQuery("is-in", ["exchange", *LISTED_YAHOO_CODES]),
    ])


def naive_rvol(volume: Any, avg_volume: Any) -> float | None:
    """Today's volume so far / the 3-month average DAILY volume.

    This is the guide's literal "5x relative volume vs its average". Both halves
    come from Yahoo's consolidated volume — the same tap, which is the one thing
    a ratio needs (a partial live number over a complete average reads ~6x low).
    It is strict early in the session, since the day is not over; that is the
    guide's measure, not an artefact to correct here.
    """
    try:
        v, a = float(volume), float(avg_volume)
    except (TypeError, ValueError):
        return None
    return v / a if a > 0 else None


def parse_halts(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Nasdaq halt RSS -> one dict per halt. Times in the feed are Eastern."""
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        def g(tag: str, item: ET.Element = item) -> str:
            el = item.find(_NDAQ + tag)
            return (el.text or "").strip() if el is not None else ""

        def when(d: str, t: str) -> pd.Timestamp | None:
            if not d or not t:
                return None
            naive = datetime.strptime(f"{d} {t.split('.')[0]}", "%m/%d/%Y %H:%M:%S")
            return pd.Timestamp(naive, tz=ET_TZ)

        halt_date = g("HaltDate")
        out.append({
            "symbol": g("IssueSymbol").upper(),
            "reason": g("ReasonCode"),
            "market": g("Market"),
            "halted_at": when(halt_date, g("HaltTime")),
            "resumed_at": when(g("ResumptionDate") or halt_date, g("ResumptionTradeTime")),
        })
    return out


def _normalise(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Yahoo's capitalised OHLCV -> the engine's lowercase columns, in ET."""
    if raw is None or raw.empty:
        # An empty frame still needs a tz-aware time index, or every caller's
        # `df.index <= cutoff` comparison fails on the no-data path.
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz=ET_TZ))
    df = raw.rename(columns=str.lower)[COLS].copy()
    df.index = pd.DatetimeIndex(df.index).tz_convert(ET_TZ)
    return df


@dataclass
class YahooFeed:
    """`us_scanner.Feed` over Yahoo + Nasdaq. Network calls are injectable so
    the tests never touch the internet."""

    cache_dir: Path = Path(".cache_yahoo_us")
    cfg: USUniverseConfig = field(default_factory=USUniverseConfig)
    settle_seconds: int = SETTLE_SECONDS
    block_delayed: bool = True
    screen_fn: Callable[..., dict] | None = None
    history_fn: Callable[[str], pd.DataFrame] | None = None
    info_fn: Callable[[str], dict] | None = None
    halts_fetch_fn: Callable[[], bytes] | None = None
    history_range_fn: Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None
    daily_fn: Callable[[str], pd.DataFrame] | None = None
    quote_fn: Callable[[str], float | None] | None = None

    # state the scanner never needs but the stored watchlist should carry
    quote_source: dict[str, str] = field(default_factory=dict)
    exchange_of: dict[str, str] = field(default_factory=dict)
    prev_close_of: dict[str, float] = field(default_factory=dict)
    halt_reasons: dict[str, str] = field(default_factory=dict)
    stale_dropped: int = 0
    truncated: bool = False
    halts_ok: bool = False
    _halted: set[str] = field(default_factory=set)
    _halts_at: float = 0.0
    _facts: dict[str, dict] = field(default_factory=dict)
    _facts_day: str = ""

    # ── network defaults ────────────────────────────────────────────────────
    def _screen(self, query: Any) -> dict:
        if self.screen_fn is not None:
            return self.screen_fn(query)
        import yfinance as yf
        result: dict = yf.screen(query, size=SCREEN_PAGE, sortField="percentchange", sortAsc=False)
        return result

    def _history(self, symbol: str) -> pd.DataFrame:
        if self.history_fn is not None:
            return self.history_fn(symbol)
        import yfinance as yf
        return yf.Ticker(symbol).history(period="1d", interval="1m", prepost=False)

    def _history_range(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if self.history_range_fn is not None:
            return self.history_range_fn(symbol, start, end)
        import yfinance as yf
        frame: pd.DataFrame = yf.Ticker(symbol).history(
            start=start, end=end, interval="1m", prepost=False)
        return frame

    def _daily(self, symbol: str) -> pd.DataFrame:
        if self.daily_fn is not None:
            return self.daily_fn(symbol)
        import yfinance as yf
        frame: pd.DataFrame = yf.Ticker(symbol).history(period="3mo", interval="1d")
        return frame

    def _quote(self, symbol: str) -> float | None:
        if self.quote_fn is not None:
            return self.quote_fn(symbol)
        import yfinance as yf
        price = yf.Ticker(symbol).fast_info.last_price
        return float(price) if price else None

    def _info(self, symbol: str) -> dict:
        if self.info_fn is not None:
            return self.info_fn(symbol)
        import yfinance as yf
        return yf.Ticker(symbol).info or {}

    def _fetch_halts(self) -> bytes:
        if self.halts_fetch_fn is not None:
            return self.halts_fetch_fn()
        import requests
        r = requests.get(HALTS_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r.content

    # ── Feed ────────────────────────────────────────────────────────────────
    def snapshot(self, now: pd.Timestamp) -> list[USQuote]:
        """Today's movers. Quotes not stamped inside TODAY's regular session are
        dropped: before the first print of the day Yahoo still reports
        yesterday's change, so a name that ran +190% yesterday would look like
        a +190% mover at 09:31 without having traded."""
        now = now.tz_convert(ET_TZ)
        result = self._screen(build_query(self.cfg))
        quotes = result.get("quotes", []) or []
        total = result.get("total")
        self.truncated = bool(total and total > len(quotes))
        if self.truncated:
            logger.warning("yahoo screen truncated: %s matched, %d returned", total, len(quotes))

        session_start = now.normalize() + pd.Timedelta(
            hours=US.session_start.hour, minutes=US.session_start.minute)
        out: list[USQuote] = []
        self.stale_dropped = 0
        for q in quotes:
            sym = str(q.get("symbol", "")).upper()
            stamp = q.get("regularMarketTime")
            if not sym or stamp is None:
                continue
            if pd.Timestamp(int(stamp), unit="s", tz="UTC") < session_start:
                self.stale_dropped += 1
                continue
            price, chg = q.get("regularMarketPrice"), q.get("regularMarketChangePercent")
            if price is None or chg is None:
                continue
            self.quote_source[sym] = str(q.get("quoteSourceName", ""))
            self.exchange_of[sym] = map_exchange(q.get("exchange"))
            prev = q.get("regularMarketPreviousClose")
            if prev:
                self.prev_close_of[sym] = float(prev)
            out.append(USQuote(
                symbol=sym,
                price=float(price),
                day_chg_pct=float(chg),
                rvol=naive_rvol(q.get("regularMarketVolume"), q.get("averageDailyVolume3Month")),
                has_catalyst=None,
                observed_at=now.isoformat(),
            ))
        return out

    def bars_1m(self, symbol: str, now: pd.Timestamp) -> pd.DataFrame:
        """Closed regular-session minutes, held back `settle_seconds` after they
        close so a still-revising bar is not read as final."""
        now = now.tz_convert(ET_TZ)
        df = _normalise(self._history(symbol))
        cutoff = now - pd.Timedelta(seconds=60 + self.settle_seconds)
        return df[df.index <= cutoff]

    def history_1m(self, symbol: str, now: pd.Timestamp, days: int = 29) -> pd.DataFrame:
        """PRIOR sessions' 1-minute bars — today excluded — for the time-of-day
        volume profile and the indicator warm-up.

        Both are required, not optional: the engine's attention path refuses
        every entry when it has no volume profile (`rvol is None`), and without
        prior-session 5-minute bars the 20-bar EMA cannot warm before the 10:00
        peak-hours deadline, so the two rules together would take zero trades.
        Yahoo serves 1-minute data for the last 30 days, at most 8 days per
        request, so it is fetched in 7-day windows.
        """
        today = now.tz_convert(ET_TZ).normalize()
        frames: list[pd.DataFrame] = []
        cursor = today - pd.Timedelta(days=days)
        while cursor < today:
            end = min(cursor + pd.Timedelta(days=7), today)
            try:
                frames.append(_normalise(self._history_range(symbol, cursor, end)))
            except Exception as exc:  # a missing window shortens the profile
                logger.warning("yahoo 1m history %s %s..%s failed: %s",
                               symbol, cursor.date(), end.date(), exc)
            cursor = end
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=COLS)
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[df.index < today]

    def daily(self, symbol: str, now: pd.Timestamp) -> pd.DataFrame:
        """Completed daily bars before today (yesterday's high/low/close)."""
        df = _normalise(self._daily(symbol))
        return df[df.index < now.tz_convert(ET_TZ).normalize()]

    def last_price(self, symbol: str, now: pd.Timestamp) -> float | None:
        """Latest trade price, for armed entries only. Yahoo has no websocket,
        so an armed buy-stop is checked by polling this every few seconds —
        coarser than NSE's ticks, which can only MISS a fill (a spike through
        the trigger that reverses between polls), never invent one.

        Returns None for a quote Yahoo labels delayed when blocking is on:
        filling from a possibly 15-minute-old price would be trading the past.
        """
        if self.block_delayed and "delayed" in self.quote_source.get(symbol, "").lower():
            return None
        try:
            return self._quote(symbol)
        except Exception as exc:
            logger.warning("yahoo quote failed for %s: %s", symbol, exc)
            return None

    def facts(self, symbols: list[str]) -> dict[str, USNameFacts]:
        """Float and venue. Fetched once per symbol per day; the figure is that
        day's snapshot and says so in `as_of` (look-ahead if replayed on a
        past date — see us_universe)."""
        today = pd.Timestamp.now(tz=ET_TZ).date().isoformat()
        self._load_facts(today)
        dirty = False
        for sym in symbols:
            if sym in self._facts:
                continue
            try:
                info = self._info(sym)
                fl = info.get("floatShares")
                self._facts[sym] = {
                    "float_shares": float(fl) if fl else None,
                    "exchange": self.exchange_of.get(sym) or map_exchange(info.get("exchange")),
                }
            except Exception as exc:  # one bad name must not stop the screen
                logger.warning("yahoo info failed for %s: %s", sym, exc)
                self._facts[sym] = {"float_shares": None, "exchange": self.exchange_of.get(sym, "")}
            dirty = True
        if dirty:
            self._save_facts(today)
        return {
            s: USNameFacts(s, float_shares=self._facts[s]["float_shares"],
                           exchange=self._facts[s]["exchange"], as_of=today)
            for s in symbols if s in self._facts
        }

    def halted(self, now: pd.Timestamp) -> set[str]:
        """Symbols in a trading halt right now, from the official feed.

        On a fetch failure the LAST KNOWN set is kept, not an empty one: an
        empty set would quietly allow entries into names that are halted.
        """
        if _time.monotonic() - self._halts_at < HALTS_TTL_SECONDS and self.halts_ok:
            return set(self._halted)
        now = now.tz_convert(ET_TZ)
        try:
            rows = parse_halts(self._fetch_halts())
        except Exception as exc:
            logger.error("halt feed failed, keeping last known %d halts: %s",
                         len(self._halted), exc)
            self.halts_ok = False
            return set(self._halted)
        current: set[str] = set()
        for r in rows:
            started = r["halted_at"] is not None and r["halted_at"] <= now
            ended = r["resumed_at"] is not None and r["resumed_at"] <= now
            if started and not ended:
                current.add(r["symbol"])
                self.halt_reasons[r["symbol"]] = r["reason"]
        self._halted, self._halts_at, self.halts_ok = current, _time.monotonic(), True
        return set(current)

    # ── audit only ──────────────────────────────────────────────────────────
    @property
    def news_pending(self) -> set[str]:
        """Halted with `T1` ("news pending") — the exchange's own marker that
        material news is due. Rare, but a real hard event, unlike news lists."""
        return {s for s, r in self.halt_reasons.items() if r == "T1" and s in self._halted}

    def describe(self) -> dict:
        """What this feed did and did not do, stored with every watchlist."""
        delayed = sorted(s for s, src in self.quote_source.items() if "delayed" in src.lower())
        return {
            "source": "yahoo+nasdaq_halts",
            "prefilter": "criteria 2 and 4 and listed venues applied at the source",
            "criterion_3": "not sourced — Yahoo news found 1 of 5 movers' causes",
            "stale_quotes_dropped": self.stale_dropped,
            "screen_truncated": self.truncated,
            "delayed_quotes": delayed,
            "halts_feed_ok": self.halts_ok,
            "halt_reasons": dict(sorted(self.halt_reasons.items())),
            "settle_seconds": self.settle_seconds,
        }

    # ── facts cache ─────────────────────────────────────────────────────────
    def _facts_path(self, day: str) -> Path:
        return Path(self.cache_dir) / f"yahoo_facts_{day}.json"

    def _load_facts(self, day: str) -> None:
        if self._facts_day == day:
            return
        self._facts, self._facts_day = {}, day
        path = self._facts_path(day)
        try:
            self._facts = json.loads(path.read_text())
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            # A cache is an optimisation: unreadable means refetch, never crash.
            logger.warning("ignoring unreadable facts cache %s: %s", path, exc)

    def _save_facts(self, day: str) -> None:
        path = self._facts_path(day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._facts))
            tmp.replace(path)   # atomic: a crash mid-write cannot leave half a file
        except OSError as exc:
            logger.warning("could not write facts cache %s: %s", path, exc)


# ── live check: run during the US session ───────────────────────────────────
def check(reference: tuple[str, ...] = ("SOUN", "F")) -> int:
    """Measures the two things the tests cannot, during a live session:

    1. How fresh and how final the newest minute bar is.
    2. Whether "Delayed Quote" movers really are delayed. After the 2026-09-23
       close Yahoo labelled many Nasdaq small caps that way (ARTL, IPDN, ...),
       not only NYSE names. If that holds intraday, their price and day change
       are stale and the screen is reading the past for exactly its targets.

    Run after 09:35 ET (19:05 IST). Checks the top live movers — the names the
    screen actually trades — plus a Nasdaq and an NYSE reference.
    """
    feed = YahooFeed(settle_seconds=0)
    now = pd.Timestamp.now(tz=ET_TZ)
    print(f"now {now:%Y-%m-%d %H:%M:%S} ET")
    quotes = feed.snapshot(now)
    delayed = feed.describe()["delayed_quotes"]
    print(f"screen: {len(quotes)} movers today, {feed.stale_dropped} stale dropped, "
          f"truncated={feed.truncated}")
    print(f"quote source: {len(quotes) - len(delayed)} real-time, {len(delayed)} labelled delayed"
          + (f" (e.g. {', '.join(delayed[:5])})" if delayed else ""))
    symbols = tuple(q.symbol for q in quotes[:3]) + reference
    print(f"halts: {len(feed.halted(now))} active, feed ok={feed.halts_ok}, "
          f"news pending={sorted(feed.news_pending)}")
    for sym in symbols:
        first = feed.bars_1m(sym, now)
        if first.empty:
            print(f"{sym:6} no bars (market shut?)")
            continue
        last_start = first.index[-1]
        age = (now - last_start).total_seconds() - 60   # seconds since that minute CLOSED
        _time.sleep(20)
        second = feed.bars_1m(sym, pd.Timestamp.now(tz=ET_TZ))
        row_a = first.loc[last_start]
        row_b = second.loc[last_start] if last_start in second.index else None
        changed = row_b is None or not row_a.equals(row_b)
        label = feed.quote_source.get(sym, "reference")
        print(f"{sym:6} [{label:22}] newest minute {last_start:%H:%M} "
              f"closed {age:5.0f}s ago | "
              f"revised 20s later: {'YES' if changed else 'no'}")
    print("\nRead it like this:")
    print("  * 'closed Ns ago' under ~60 and revisions 'no'  -> SETTLE_SECONDS=20 is safe.")
    print("  * a DELAYED name whose bars are ~15 min old      -> those names must not be traded.")
    print("  * a DELAYED name whose bars are as fresh as the rest -> the label is cosmetic.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="measure bar latency during the session")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.check:
        return check()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
