import type { Bar } from './types';

/** Seconds in an intraday source interval ('1m', '15m', '60m', '1h'); null for daily and longer. */
export function intervalSecs(interval: string): number | null {
  const m = /^(\d+)(m|h)$/.exec(interval);
  if (!m) return null;
  return Number(m[1]) * (m[2] === 'h' ? 3600 : 60);
}

/**
 * Yahoo ends every intraday series with a pseudo-bar: zero volume, stamped with the time of the last trade
 * (11:11:44 inside the 11:00 candle) instead of a candle start. Its time changes on nearly every poll, so
 * series that update one after another briefly hold two different last times, and a chart scrolled away
 * from the live edge slips by a bar each time.
 *
 * It is the freshest price, so it is folded into the candle it falls inside (close, and high/low if it
 * extends them) and dropped as a bar of its own.
 */
export function foldTrailingTick(bars: Bar[], secs: number | null): Bar[] {
  if (!secs || bars.length < 2) return bars;
  const last = bars[bars.length - 1]!;
  const prev = bars[bars.length - 2]!;
  if (last.time <= prev.time || last.time >= prev.time + secs) return bars;
  return [
    ...bars.slice(0, -2),
    {
      ...prev,
      high: Math.max(prev.high, last.high),
      low: Math.min(prev.low, last.low),
      close: last.close,
    },
  ];
}
