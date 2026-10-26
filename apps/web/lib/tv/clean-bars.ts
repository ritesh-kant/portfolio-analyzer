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

/** How far a no-volume bar's open may sit from the previous close, and its wicks from its body. */
export const SPIKE_TOLERANCE = 0.005;

/**
 * Yahoo's pre/post-market bars carry no volume, and some of their prices are stray quotes, not trades: a TSLA
 * 15-minute bar opening at 346.53 between bars at 372, or a low 7% under the bar. They draw as wicks running off
 * the scale. For a zero-volume bar outside regular hours (`regular(time)` false):
 *   - an open more than SPIKE_TOLERANCE from the previous close, in a bar that directly follows it, is that close;
 *   - high and low are held within SPIKE_TOLERANCE of the bar's own body.
 * Bars with volume, regular-hours bars and the first bar after a gap (where a real gap can open) are left alone.
 */
export function dropSpikeWicks(
  bars: Bar[],
  secs: number | null,
  regular: (time: number) => boolean,
): Bar[] {
  if (!secs) return bars;
  let changed = false;
  const out = bars.map((b, i) => {
    if (b.volume > 0 || regular(b.time)) return b;
    const prev = bars[i - 1];
    let open = b.open;
    if (
      prev &&
      b.time - prev.time === secs &&
      Math.abs(open / prev.close - 1) > SPIKE_TOLERANCE
    ) {
      open = prev.close;
    }
    const top = Math.max(open, b.close);
    const bottom = Math.min(open, b.close);
    const high = Math.max(top, Math.min(b.high, top * (1 + SPIKE_TOLERANCE)));
    const low = Math.min(bottom, Math.max(b.low, bottom * (1 - SPIKE_TOLERANCE)));
    if (open === b.open && high === b.high && low === b.low) return b;
    changed = true;
    return { ...b, open, high, low };
  });
  return changed ? out : bars;
}

/** The backfill never credits the opening bar with more than this share of the day's volume. */
export const OPENING_VOLUME_MAX_SHARE = 0.4;

/**
 * Yahoo's first intraday bar of an NSE session (09:15) usually arrives with volume 0, while its daily bar has
 * the full-day total. Left alone, every intraday volume sum, VWAP and relative-volume reading runs short by the
 * opening volume (RELIANCE 2026-10-26: 7.04M against 7.49M; VWAP 0.6 off TradingView's).
 *
 * For each day whose first bar has no volume but whose other bars do, the opening bar gets the daily total minus
 * those other bars. Skipped when the daily volume is missing, the remainder is not positive, or it exceeds
 * OPENING_VOLUME_MAX_SHARE of the day (a lagging daily bar would otherwise inflate it).
 */
export function backfillOpeningVolume(
  bars: Bar[],
  dailyBars: Bar[],
  dayOf: (time: number) => string,
): Bar[] {
  const daily = new Map<string, number>();
  for (const d of dailyBars) if (d.volume > 0) daily.set(dayOf(d.time), d.volume);
  if (daily.size === 0) return bars;
  const firstOfDay = new Map<string, number>();
  const rest = new Map<string, number>();
  bars.forEach((b, i) => {
    const day = dayOf(b.time);
    if (!firstOfDay.has(day)) firstOfDay.set(day, i);
    else rest.set(day, (rest.get(day) ?? 0) + b.volume);
  });
  let out: Bar[] | null = null;
  for (const [day, i] of firstOfDay) {
    const total = daily.get(day);
    const others = rest.get(day) ?? 0;
    if (!total || bars[i]!.volume > 0 || others <= 0) continue;
    const missing = total - others;
    if (missing <= 0 || missing > total * OPENING_VOLUME_MAX_SHARE) continue;
    out ??= bars.slice();
    out[i] = { ...bars[i]!, volume: missing };
  }
  return out ?? bars;
}
