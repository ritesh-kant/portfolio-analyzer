"""Tick → 1-minute bar builder for the live scanner.

Upstox FULL-mode feed gives, per tick: last price (ltp), last trade time (ltt,
ms epoch) and `vtt` = cumulative volume traded today. Bar volume is therefore
the *difference* in vtt across the bar, which is exact, rather than a sum of
last-trade quantities, which misses trades between ticks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


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
        """All bars whose minute has fully elapsed at `now` (IST)."""
        cutoff = now.tz_convert(IST).floor("min")
        rows = [b for b in self._bars.get(key, []) if b.start < cutoff]
        if not rows:
            return pd.DataFrame(columns=COLS)
        df = pd.DataFrame(
            [(b.open, b.high, b.low, b.close, b.volume) for b in rows],
            index=pd.DatetimeIndex([b.start for b in rows]),
            columns=COLS,
        )
        return df

    def keys(self) -> list[str]:
        return list(self._bars.keys())

    def latest_close(self, key: str) -> float | None:
        bars = self._bars.get(key)
        return bars[-1].close if bars else None
