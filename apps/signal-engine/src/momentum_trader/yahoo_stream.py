"""Yahoo's push price stream for the US arm: fills between bars, and the newest bar.

Why this exists
---------------
The Yahoo REST feed made the US arm slow in exactly the two places a
fast-moving small cap punishes:

* an armed buy-stop was checked every 10 s, so a stock that ran more than the
  1% chase cap inside those 10 s was cancelled as "chased" — the trend we were
  waiting for, refused because we looked too late;
* a decision was taken 22 s after each minute closed (20 s for Yahoo's REST bar
  to settle + 2 s), where NSE decides at +2 s.

`yf.WebSocket` (yfinance >= 0.2.54) is the stream Yahoo's own pages use. This
module holds it open on a background thread and serves two things from memory:
every new trade price since the last look (`drain`, for fills), and 1-minute
bars built from those prices (`closed_bars`, for the newest decision bar).

What a message is — measured 2026-09-25, 13:50 ET, 73 movers + SPY
------------------------------------------------------------------
``{"id", "price", "time" (ms, always a whole second), "day_volume", "last_size"?,
"market_hours"}``. `time` is the LAST SALE's time, not the send time: a message
whose `day_volume` rose but whose `time` and `price` did not is odd-lot /
non-last-sale volume, re-sent for up to a minute with the old `time` (DDOG sat
on one `time` for 50 s while its volume kept rising). So:

* a NEW TRADE is a message whose (`time`, `price`) differs from the previous one
  for that symbol — only those set a bar's OHLC or can fill an order;
* delivery latency is receipt minus `time` on new trades: 0.8-1.8 s on SPY;
* `day_volume` is the consolidated day total (like Upstox `vtt`), so a bar's
  volume is the rise in it across the minute, bucketed by receipt time.

What it cannot do
-----------------
It is conflated: SPY arrives at ~1 message a second, not every print. A bar
built from it can miss the extreme of a wick that lived under a second — the
same defect as NSE's sampled LTP (`project_live_bar_sampling`). That is why the
stream only ever supplies the NEWEST minute, the one REST has not settled yet;
every older minute is Yahoo's REST bar, and `compare_bars` measures how often
the two disagree. It is also unofficial and can vanish; `healthy()` goes False
within `stale_after` seconds and every caller falls back to REST.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time as _time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .market import US

logger = logging.getLogger(__name__)

ET_TZ = US.timezone
COLS = ["open", "high", "low", "close", "volume"]
HEARTBEAT = "SPY"            # always subscribed: trades every second in session
STALE_AFTER_SECONDS = 10.0   # no message at all for this long = unhealthy
SETTLE_SECONDS = 3.0         # a minute is final this long after it closes
RECONNECT_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)
TICK_BUFFER = 2_000          # per symbol, between drains


@dataclass(frozen=True)
class Trade:
    """One new last-sale print, as observed."""

    at: pd.Timestamp        # the trade's own time (ET, whole seconds)
    price: float
    received: float         # epoch seconds we received it


def _minute(epoch: float) -> int:
    return int(epoch // 60) * 60


@dataclass
class _SymbolState:
    last_key: tuple[str, float] | None = None
    last_dv: float | None = None
    # minute start (epoch s) -> [open, high, low, close, volume]
    bars: dict[int, list[float]] = field(default_factory=dict)
    first_complete: int | None = None    # first minute fully covered by the stream
    incomplete: set[int] = field(default_factory=set)
    pending_vol: dict[int, float] = field(default_factory=dict)
    needs_baseline: bool = True
    trades: deque[Trade] = field(default_factory=lambda: deque(maxlen=TICK_BUFFER))
    messages: int = 0
    new_trades: int = 0


def _parse(msg: dict[str, Any]) -> tuple[str, float, int, float | None, bool] | None:
    try:
        sym = str(msg["id"]).upper()
        price = float(msg["price"])
        t_ms = int(msg["time"])
    except (KeyError, TypeError, ValueError):
        return None
    dv_raw = msg.get("day_volume")
    try:
        dv = float(dv_raw) if dv_raw is not None else None
    except (TypeError, ValueError):
        dv = None
    regular = msg.get("market_hours") in (1, "1", "REGULAR_MARKET")
    return sym, price, t_ms, dv, regular


class YahooStream:
    """One websocket, many symbols. Thread-safe: the socket thread writes under
    `_lock`, the session loop reads under it.

    `ws_factory` returns an object with ``subscribe(list)``, ``listen(handler)``
    (blocks until the connection ends) and ``close()`` — `yf.WebSocket`'s
    surface — so the tests drive it with a fake and never touch the network.
    """

    def __init__(
        self,
        ws_factory: Callable[[], Any] | None = None,
        *,
        clock: Callable[[], float] = _time.time,
        stale_after: float = STALE_AFTER_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
        delivery_lag: float = 1.0,
    ) -> None:
        self._factory = ws_factory or _default_factory
        self._clock = clock
        self.stale_after = stale_after
        self.settle_seconds = settle_seconds
        # Volume arrives with the message, not with its trade time; the trades
        # behind a rise happened ~one delivery latency earlier.
        self.delivery_lag = delivery_lag
        self._lock = threading.Lock()
        self._tick = threading.Event()
        self._stop = threading.Event()
        self._ws: Any = None
        self._thread: threading.Thread | None = None
        self._subs: set[str] = {HEARTBEAT}
        self._state: dict[str, _SymbolState] = {}
        self._connected = False
        self._last_msg: float | None = None
        self._dropped_at: float | None = None
        self.connects = 0
        self.drops = 0

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="yahoo-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001 — closing a dead socket must not raise
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                ws = self._factory()
                self._ws = ws
                with self._lock:
                    subs = sorted(self._subs)
                ws.subscribe(subs)
                with self._lock:
                    self._connected = True
                    self.connects += 1
                attempt = 0
                logger.info("yahoo stream connected, %d symbols", len(subs))
                ws.listen(self.on_message)
            except Exception as exc:  # noqa: BLE001 — a drop is routine; log and reconnect
                if not self._stop.is_set():
                    logger.warning("yahoo stream ended: %s", exc)
            finally:
                self._on_drop()
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ws = None
            if self._stop.is_set():
                break
            wait = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            attempt += 1
            self._stop.wait(wait)

    def _on_drop(self) -> None:
        with self._lock:
            if self._connected:
                self.drops += 1
            self._connected = False
            self._dropped_at = self._clock()
            for st in self._state.values():
                st.needs_baseline = True

    # ── subscriptions ───────────────────────────────────────────────────────
    def subscribe(self, symbols: Iterable[str]) -> None:
        new = {s.upper() for s in symbols} - self._subs
        if not new:
            return
        with self._lock:
            self._subs |= new
        ws = self._ws
        if ws is not None and self._connected:
            try:
                ws.subscribe(sorted(new))
            except Exception as exc:  # noqa: BLE001 — the reconnect resubscribes all
                logger.warning("yahoo stream subscribe %s failed: %s", sorted(new), exc)

    @property
    def symbols(self) -> set[str]:
        return set(self._subs)

    def has_seen(self, symbol: str) -> bool:
        """Whether any message has arrived for `symbol` (a name Yahoo does not
        stream would otherwise look like one that never trades)."""
        with self._lock:
            return symbol.upper() in self._state

    # ── the socket thread ───────────────────────────────────────────────────
    def on_message(self, msg: dict[str, Any]) -> None:
        parsed = _parse(msg)
        rx = self._clock()
        with self._lock:
            self._last_msg = rx
        if parsed is None:
            return
        sym, price, t_ms, dv, regular = parsed
        if not regular:
            return  # pre/post market: REST bars are regular-session only too
        with self._lock:
            st = self._state.setdefault(sym, _SymbolState())
            st.messages += 1
            if st.needs_baseline:
                # First message since (re)connecting: nothing before it is
                # known, so every minute up to and including this one is
                # incomplete, and this day_volume is only a baseline.
                gap_from = self._dropped_at if st.first_complete is not None else None
                if gap_from is not None:
                    for m in range(_minute(gap_from - self.delivery_lag), _minute(rx) + 60, 60):
                        st.incomplete.add(m)
                else:
                    st.first_complete = _minute(rx) + 60
                st.needs_baseline = False
                st.last_dv = dv
                st.last_key = (str(t_ms), price)
                return

            key = (str(t_ms), price)
            if key != st.last_key:
                st.last_key = key
                t = t_ms / 1000.0
                m = _minute(t)
                bar = st.bars.get(m)
                if bar is None:
                    if st.bars and m < max(st.bars):
                        m = -1   # an out-of-order print for a minute already past
                    else:
                        st.bars[m] = [price, price, price, price, st.pending_vol.pop(m, 0.0)]
                else:
                    bar[1] = max(bar[1], price)
                    bar[2] = min(bar[2], price)
                    bar[3] = price
                if m >= 0:
                    st.new_trades += 1
                    st.trades.append(Trade(pd.Timestamp(t, unit="s", tz="UTC").tz_convert(ET_TZ),
                                           price, rx))
                    self._tick.set()
            if dv is not None:
                if st.last_dv is not None and dv > st.last_dv:
                    vm = _minute(rx - self.delivery_lag)
                    target = st.bars.get(vm)
                    if target is not None:
                        target[4] += dv - st.last_dv
                    elif not st.bars or vm > max(st.bars):
                        # Shares traded before this minute's first new last sale.
                        # Held until that sale opens the bar; if none comes, the
                        # minute is a flat bar at the last price (`closed_bars`).
                        st.pending_vol[vm] = st.pending_vol.get(vm, 0.0) + dv - st.last_dv
                st.last_dv = dv if st.last_dv is None else max(st.last_dv, dv)

    # ── readers ─────────────────────────────────────────────────────────────
    def healthy(self) -> bool:
        with self._lock:
            return (self._connected and self._last_msg is not None
                    and self._clock() - self._last_msg <= self.stale_after)

    def wait_for_trade(self, timeout: float) -> bool:
        """Block until a new trade arrives on any symbol, or `timeout`."""
        hit = self._tick.wait(timeout)
        self._tick.clear()
        return hit

    def drain(self, symbol: str) -> list[Trade]:
        """Every new trade seen for `symbol` since the last drain, oldest first."""
        with self._lock:
            st = self._state.get(symbol.upper())
            if st is None:
                return []
            out = list(st.trades)
            st.trades.clear()
            return out

    def closed_bars(self, symbol: str, now: pd.Timestamp | float) -> pd.DataFrame:
        """Minutes this stream saw from start to end, closed `settle_seconds` ago.

        A minute is left out, never guessed, when the stream was not listening
        for all of it: before the first message after subscribing, and across
        any disconnect. A minute with no new trade has no bar — like REST.
        """
        t_now = now.timestamp() if isinstance(now, pd.Timestamp) else float(now)
        with self._lock:
            st = self._state.get(symbol.upper())
            if st is None or st.first_complete is None or not self._connected:
                return _empty()
            limit = t_now - 60 - self.settle_seconds
            bars = {m: list(b) for m, b in st.bars.items()}
            for m, vol in st.pending_vol.items():
                if m not in bars and m <= limit:
                    prior = [k for k in bars if k < m]
                    if prior:
                        last = bars[max(prior)][3]
                        bars[m] = [last, last, last, last, vol]
            rows = {m: tuple(b) for m, b in bars.items()
                    if m >= st.first_complete and m not in st.incomplete and m <= limit}
        if not rows:
            return _empty()
        idx = sorted(rows)
        return pd.DataFrame(
            [rows[m] for m in idx],
            index=pd.DatetimeIndex(
                [pd.Timestamp(m, unit="s", tz="UTC") for m in idx]).tz_convert(ET_TZ),
            columns=COLS,
        )

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "source": "yahoo_websocket",
                "connected": self._connected,
                "connects": self.connects,
                "drops": self.drops,
                "symbols": len(self._subs),
                "messages": sum(s.messages for s in self._state.values()),
                "new_trades": sum(s.new_trades for s in self._state.values()),
                "settle_seconds": self.settle_seconds,
            }


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz=ET_TZ))


def _default_factory() -> Any:
    import yfinance as yf

    return yf.WebSocket(verbose=False)


# ── measurement: stream bars vs Yahoo's REST bars ───────────────────────────
def compare_bars(stream: pd.DataFrame, rest: pd.DataFrame, tick_pct: float = 0.0) -> dict[str, Any]:
    """How far the stream's minute bars are from Yahoo's REST bars, minute by
    minute over the minutes both have.

    `tick_pct` is the tolerance, as % of price, for calling two prices equal
    (0 = exact to the cent). Direction matters more than size: a stream high
    that reads LOW hides a breakout; a candle of the wrong colour flips a
    pattern — both are counted on their own.
    """
    both = stream.index.intersection(rest.index)
    out: dict[str, Any] = {"minutes_stream": len(stream), "minutes_rest": len(rest),
                           "minutes_compared": len(both)}
    if len(both) == 0:
        return out
    s, r = stream.loc[both], rest.loc[both]
    tol = r["close"].abs() * tick_pct / 100.0 + 0.005

    def pct(mask: pd.Series) -> float:
        return round(100.0 * float(mask.mean()), 1)

    for col in ("open", "high", "low", "close"):
        diff = s[col] - r[col]
        out[f"{col}_match_pct"] = pct(diff.abs() <= tol)
        out[f"{col}_median_abs_diff_pct"] = round(float((diff.abs() / r[col] * 100).median()), 3)
    out["high_too_low_pct"] = pct(s["high"] < r["high"] - tol)
    out["low_too_high_pct"] = pct(s["low"] > r["low"] + tol)
    colour_s = (s["close"] - s["open"]).apply(lambda x: 0 if abs(x) < 1e-9 else math.copysign(1, x))
    colour_r = (r["close"] - r["open"]).apply(lambda x: 0 if abs(x) < 1e-9 else math.copysign(1, x))
    out["colour_flip_pct"] = pct((colour_s * colour_r) < 0)
    rv = r["volume"].replace(0, pd.NA)
    ratio = (s["volume"] / rv).dropna().astype(float)
    out["volume_ratio_median"] = round(float(ratio.median()), 3) if len(ratio) else None
    return out


def replay_file(path: str) -> dict[str, pd.DataFrame]:
    """Rebuild stream bars from a `--record` JSONL file, exactly as live."""
    rows = [json.loads(line) for line in open(path)]
    clock_now = [0.0]
    stream = YahooStream(ws_factory=lambda: None, clock=lambda: clock_now[0])
    stream._connected = True
    for row in rows:
        clock_now[0] = row["rx"]
        if "m" in row:
            stream.on_message(row["m"])
        elif row.get("event") == "drop":
            stream._on_drop()
            stream._connected = True
    end = clock_now[0] + 3600
    return {sym: stream.closed_bars(sym, end) for sym in list(stream._state)}


def _rest_bars(symbol: str, day: str) -> pd.DataFrame:
    import yfinance as yf

    start = pd.Timestamp(day)
    raw = yf.Ticker(symbol).history(start=start, end=start + pd.Timedelta(days=1),
                                    interval="1m", prepost=False)
    if raw is None or raw.empty:
        return _empty()
    df = raw.rename(columns=str.lower)[COLS].copy()
    df.index = pd.DatetimeIndex(df.index).tz_convert(ET_TZ)
    return df


def record(path: str, symbols: list[str], minutes: float) -> int:
    """Append every message for `symbols` to `path` as JSONL for `minutes`."""
    lock = threading.Lock()
    with open(path, "a", buffering=1) as fh:
        def write(obj: dict[str, Any]) -> None:
            with lock:
                fh.write(json.dumps(obj) + "\n")

        stream = YahooStream()
        original = stream.on_message

        def tee(msg: dict[str, Any]) -> None:
            write({"rx": _time.time(), "m": msg})
            original(msg)

        stream.on_message = tee  # type: ignore[method-assign]
        stream.subscribe(symbols)
        stream.start()
        end = _time.time() + minutes * 60
        drops = 0
        while _time.time() < end:
            _time.sleep(1)
            if stream.drops > drops:
                drops = stream.drops
                write({"rx": _time.time(), "event": "drop"})
        stream.stop()
    print(json.dumps(stream.describe()))
    return 0


def compare_file(path: str, day: str, min_minutes: int = 10) -> int:
    """Stream bars rebuilt from a recording vs Yahoo REST, per symbol + pooled."""
    stream_bars = replay_file(path)
    pooled_s, pooled_r = [], []
    print(f"{'symbol':8}{'min':>5}{'O=':>6}{'H=':>6}{'L=':>6}{'C=':>6}{'H low':>7}{'L high':>7}"
          f"{'flip':>6}{'vol x':>7}")
    for sym, s in sorted(stream_bars.items()):
        if len(s) < min_minutes:
            continue
        r = _rest_bars(sym, day)
        c = compare_bars(s, r)
        if c["minutes_compared"] < min_minutes:
            continue
        both = s.index.intersection(r.index)
        pooled_s.append(s.loc[both].set_index(s.loc[both].index.map(lambda t, x=sym: f"{x}{t}")))
        pooled_r.append(r.loc[both].set_index(r.loc[both].index.map(lambda t, x=sym: f"{x}{t}")))
        print(f"{sym:8}{c['minutes_compared']:5}{c['open_match_pct']:6.0f}{c['high_match_pct']:6.0f}"
              f"{c['low_match_pct']:6.0f}{c['close_match_pct']:6.0f}{c['high_too_low_pct']:7.0f}"
              f"{c['low_too_high_pct']:7.0f}{c['colour_flip_pct']:6.0f}"
              f"{(c['volume_ratio_median'] or 0):7.2f}")
    if pooled_s:
        allc = compare_bars(pd.concat(pooled_s), pd.concat(pooled_r))
        print("\nPOOLED", json.dumps(allc, indent=1))
    return 0


def bars_from_doc(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """`chart_bars_doc` rows (as stored in Mongo) back into a bar frame."""
    if not bars:
        return _empty()
    df = pd.DataFrame(bars)
    idx = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True)).tz_convert(ET_TZ)
    df.index = idx.rename(None)
    return df[COLS].astype(float)


def compare_db(day: str, min_minutes: int = 5) -> int:
    """After a production session: the stream's bars (`mt_us_stream_bars`) vs
    the REST bars the engine ended the day on (`mt_us_watch_bars`). The newest
    minute of the REST series may itself be a stream minute, so it is dropped."""
    from pymongo import MongoClient

    from src.config import Settings

    settings = Settings()
    db: Any = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)[
        settings.mongodb_db_name]
    q = {"market": US.code, "date": day}
    rest = {d["symbol"]: bars_from_doc(d.get("bars") or [])
            for d in db["mt_us_watch_bars"].find(q, {"symbol": 1, "bars": 1})}
    pooled_s, pooled_r = [], []
    for d in db["mt_us_stream_bars"].find(q, {"symbol": 1, "bars": 1}):
        sym = d["symbol"]
        s, r = bars_from_doc(d.get("bars") or []), rest.get(sym, _empty()).iloc[:-1]
        c = compare_bars(s, r)
        if c["minutes_compared"] < min_minutes:
            continue
        print(sym, json.dumps(c))
        both = s.index.intersection(r.index)
        pooled_s.append(s.loc[both].set_index(both.map(lambda t, x=sym: f"{x}{t}")))
        pooled_r.append(r.loc[both].set_index(both.map(lambda t, x=sym: f"{x}{t}")))
    if not pooled_s:
        print(f"no symbol with {min_minutes}+ comparable minutes on {day}")
        return 1
    print("\nPOOLED", json.dumps(compare_bars(pd.concat(pooled_s), pd.concat(pooled_r)), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Yahoo price stream: record, compare")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record the stream to JSONL (run in US hours)")
    r.add_argument("path")
    r.add_argument("symbols", nargs="+")
    r.add_argument("--minutes", type=float, default=30)
    c = sub.add_parser("compare", help="stream bars from a recording vs Yahoo REST bars")
    c.add_argument("path")
    c.add_argument("--day", required=True, help="session date, YYYY-MM-DD")
    d = sub.add_parser("compare-db", help="a production session's stream bars vs REST, from Mongo")
    d.add_argument("--day", required=True, help="session date, YYYY-MM-DD")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "record":
        return record(args.path, args.symbols, args.minutes)
    if args.cmd == "compare-db":
        return compare_db(args.day)
    return compare_file(args.path, args.day)


if __name__ == "__main__":
    raise SystemExit(main())
