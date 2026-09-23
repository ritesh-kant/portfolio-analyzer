"""Tick → 1-minute bar builder for the live scanner.

Upstox FULL-mode feed gives, per tick: last price (ltp), last trade time (ltt,
ms epoch) and `vtt` = cumulative volume traded today. Bar volume is therefore
the *difference* in vtt across the bar, which is exact, rather than a sum of
last-trade quantities, which misses trades between ticks.

The same message also carries the exchange's own 1-minute candle
(`marketOHLC`, interval `I1`, `ts` = the minute's start). That candle wins
wherever it exists: a FULL-mode message is a periodic SNAPSHOT, not every
trade, so a bar built from its `ltp` misses the wicks and opens late. Measured
on 2026-09-23 against the official intraday candles: IKS high wrong in 18/26
minutes (always too low), low in 17/26 (always too high), open off by up to
₹5.90; ELECON had 6/71 candles of the wrong colour. It flipped a decision: on
the official IKS bars the 09:34 entry never forms (09:33 is a green
continuation candle, not a doji pause). Backtests replay official candles, so
until this the live arm was not trading the tested strategy. The snapshot bar
is kept as the fallback for any minute with no `I1` candle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]
# NSE continuous trading opens at 09:15. Two things arrive on the feed before
# it and are NOT bars of today's session:
#   * the previous session's last trade, delivered in the first FULL-mode
#     snapshot with its own `ltt` (2026-09-22 RHIM: 09-21 15:56 at 367.00 on a
#     day that opened at 389.30), and
#   * the 09:00-09:08 pre-open auction print.
# Every stored trade up to 2026-09-22 carries both, and the engine stepped over
# them: `indicators.volume_ratio` averaged the stale bar into the session's
# first 20 volume baselines, `atr` read the overnight gap as one bar's range,
# and the review chart drew its price axis down to yesterday's last trade.
# Session-scoped helpers (`session_vwap`, `cumulative_session_volume`,
# `price_volume_slopes`) group by day and were immune, which is why this hid
# for so long. `closed_bars` is the one door every consumer comes through, so
# the cut belongs here rather than in each of them.
SESSION_OPEN = (9, 15)


@dataclass
class _Bar:
    start: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    vtt_start: float
    vtt_last: float

    @property
    def volume(self) -> float:
        return max(0.0, self.vtt_last - self.vtt_start)


@dataclass
class BarBuilder:
    """Per-instrument 1-min bars. Thread-safety: the feed thread calls on_tick;
    the scan loop calls closed_bars. Python's GIL makes the list appends atomic
    enough for this use; the scan loop only reads bars strictly older than the
    current minute."""

    _bars: dict[str, list[_Bar]] = field(default_factory=dict)
    _last_vtt: dict[str, float] = field(default_factory=dict)
    # key → minute start → exchange (open, high, low, close, volume). The
    # latest message for a minute overwrites the earlier ones, so a candle
    # still forming when first seen ends as the final one.
    _candles: dict[str, dict[pd.Timestamp, tuple[float, float, float, float, float]]] = field(
        default_factory=dict
    )

    def on_tick(self, key: str, ts_ms: int, ltp: float, vtt: float | None) -> None:
        ts = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(IST).floor("min")
        bars = self._bars.setdefault(key, [])
        if vtt is None:  # LTPC mode fallback — volume unknown, keep last known
            vtt = self._last_vtt.get(key, 0.0)
        self._last_vtt[key] = vtt
        if bars and bars[-1].start == ts:
            b = bars[-1]
            b.high = max(b.high, ltp)
            b.low = min(b.low, ltp)
            b.close = ltp
            b.vtt_last = vtt
            return
        if bars and ts < bars[-1].start:
            return  # late tick for an already-closed bar; ignore
        prev_vtt = bars[-1].vtt_last if bars else vtt
        bars.append(_Bar(start=ts, open=ltp, high=ltp, low=ltp, close=ltp,
                         vtt_start=prev_vtt, vtt_last=vtt))

    def on_candle(self, key: str, ts_ms: int, open_: float, high: float,
                  low: float, close: float, volume: float) -> None:
        """Record the exchange's 1-minute candle for the minute starting at `ts_ms`."""
        ts = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(IST).floor("min")
        self._candles.setdefault(key, {})[ts] = (
            float(open_), float(high), float(low), float(close), float(volume)
        )

    def seed(self, key: str, bars_1m: pd.DataFrame) -> None:
        """Pre-load today's bars fetched over REST (scanner started late / restarted)."""
        if bars_1m.empty:
            return
        out: list[_Bar] = []
        vtt = 0.0
        for ts, r in bars_1m.iterrows():
            start = pd.Timestamp(ts)
            out.append(_Bar(start=start, open=float(r["open"]), high=float(r["high"]),
                            low=float(r["low"]), close=float(r["close"]),
                            vtt_start=vtt, vtt_last=vtt + float(r["volume"])))
            vtt += float(r["volume"])
        existing = self._bars.get(key, [])
        # keep any live bars newer than the seed
        live = [b for b in existing if b.start > out[-1].start]
        self._bars[key] = out + live
        self._last_vtt[key] = max(self._last_vtt.get(key, 0.0), vtt)

    def closed_bars(self, key: str, now: pd.Timestamp) -> pd.DataFrame:
        """Bars of `now`'s own session whose minute has fully elapsed at `now`.

        Bounded at both ends on purpose: the upper bound keeps the unfinished
        minute out, the lower bound keeps everything before the opening bell
        out (see `SESSION_OPEN`).
        """
        cutoff = now.tz_convert(IST).floor("min")
        opened = cutoff.normalize() + pd.Timedelta(
            hours=SESSION_OPEN[0], minutes=SESSION_OPEN[1]
        )
        rows = {b.start: (b.open, b.high, b.low, b.close, b.volume)
                for b in self._bars.get(key, []) if opened <= b.start < cutoff}
        rows.update({ts: c for ts, c in self._candles.get(key, {}).items()
                     if opened <= ts < cutoff})
        if not rows:
            return pd.DataFrame(columns=COLS)
        idx = sorted(rows)
        return pd.DataFrame([rows[ts] for ts in idx], index=pd.DatetimeIndex(idx),
                            columns=COLS)

    def candle_coverage(self, key: str, now: pd.Timestamp) -> tuple[int, int]:
        """(minutes served from the exchange candle, closed minutes in total)."""
        bars = self.closed_bars(key, now)
        official = self._candles.get(key, {})
        return sum(1 for ts in bars.index if ts in official), len(bars)

    def keys(self) -> list[str]:
        return list(self._bars.keys())

    def latest_close(self, key: str) -> float | None:
        bars = self._bars.get(key)
        return bars[-1].close if bars else None
