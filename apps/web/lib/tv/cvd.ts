import type { Bar } from './types';

/**
 * Cumulative Volume Delta, as TradingView's CVD indicator builds it.
 *
 * Every lower-timeframe bar ("intrabar") under a chart candle counts as buying or selling by its own direction:
 * close above open = +volume, close below open = −volume; a flat bar follows the direction of its close against
 * the previous bar's close, and a bar with neither keeps the previous sign. The deltas are summed from the start
 * of the anchor period (session, week or month), and each chart candle is drawn as an open/high/low/close
 * candle of that running total: open = total before the candle, close = after, high/low = the extremes the total
 * reached between its intrabars.
 *
 * ⚠ Yahoo serves bars, not trades, so this is an estimate, as in TradingView on a symbol without tick data.
 * Before the oldest intrabar loaded (older history) the chart candle itself is the one intrabar, which is
 * what TradingView does when it has no lower timeframe to look at.
 *
 * Pre/post-market: Yahoo reports no volume for those bars (the one exception is the 16:00 minute, which
 * carries the whole closing-auction cross: 1–3M shares in a single "bar" that is not buying or selling). So
 * when the caller says which times are regular hours, everything outside them is left out of the total and
 * the candle is marked `missing` (drawn as a gap, read as n/a) instead of a flat zero that looks like data.
 */

export type CvdAnchor = 'session' | 'week' | 'month';

export const CVD_ANCHORS: { id: CvdAnchor; label: string }[] = [
  { id: 'session', label: 'Session' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
];

export interface CvdCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  /** No volume is reported for this candle (outside regular hours): open/high/low/close just carry the total. */
  missing?: true;
}

/** Sign state carried from bar to bar: 1 buying, −1 selling, 0 not known yet. */
function directionOf(b: Bar, prevClose: number | null, sign: number): number {
  if (b.close > b.open) return 1;
  if (b.close < b.open) return -1;
  if (prevClose != null && b.close > prevClose) return 1;
  if (prevClose != null && b.close < prevClose) return -1;
  return sign;
}

/** Signed volume of each bar (buying positive, selling negative). */
export function signedVolumes(bars: Bar[]): number[] {
  let sign = 0;
  let prevClose: number | null = null;
  return bars.map((b) => {
    sign = directionOf(b, prevClose, sign);
    prevClose = b.close;
    return sign * b.volume;
  });
}

/** The key of the anchor period a session day ('YYYY-MM-DD') belongs to; the total restarts when it changes. */
export function anchorKey(day: string, anchor: CvdAnchor): string {
  if (anchor === 'month') return day.slice(0, 7);
  if (anchor === 'week') {
    const d = new Date(`${day}T00:00:00Z`);
    const sinceMonday = (d.getUTCDay() + 6) % 7;
    return new Date(d.getTime() - sinceMonday * 86400000).toISOString().slice(0, 10);
  }
  return day;
}

/**
 * One CVD candle per chart bar. `sub` are the intrabars (ascending), `secs` the chart bar's length, and
 * `anchorOf` maps a bar's start time to the key of its anchor period. `regular`, when given, says whether a
 * time is inside regular trading hours; bars outside them add nothing and a candle with none inside is `missing`.
 */
export function computeCvd(
  bars: Bar[],
  allSub: Bar[],
  secs: number,
  anchorOf: (time: number) => string,
  regular?: (time: number) => boolean,
): CvdCandle[] {
  const sub = regular ? allSub.filter((b) => regular(b.time)) : allSub;
  const signed = signedVolumes(sub);
  const out: CvdCandle[] = [];
  let level = 0;
  let key = '';
  let j = 0;
  let fbSign = 0;
  let fbPrev: number | null = null;
  for (const b of bars) {
    const k = anchorOf(b.time);
    if (k !== key) {
      key = k;
      level = 0;
    }
    while (j < sub.length && sub[j]!.time < b.time) j += 1;
    const end = b.time + secs;
    const open = level;
    let high = level;
    let low = level;
    let n = 0;
    while (j < sub.length && sub[j]!.time < end) {
      level += signed[j]!;
      high = Math.max(high, level);
      low = Math.min(low, level);
      j += 1;
      n += 1;
    }
    fbSign = directionOf(b, fbPrev, fbSign);
    fbPrev = b.close;
    // Older than every intrabar loaded: the candle stands in for its own intrabar. A candle inside the loaded
    // range that has none had no trades to count.
    const standIn = n === 0 && !(sub.length > 0 && b.time >= sub[0]!.time);
    // A candle that starts outside regular hours and has no regular-hours intrabar (a 1h candle starting 09:00
    // does) has no volume to read.
    if (regular && n === 0 && !regular(b.time)) {
      out.push({ open, high: open, low: open, close: open, missing: true });
      continue;
    }
    if (standIn) {
      level += fbSign * b.volume;
      high = Math.max(open, level);
      low = Math.min(open, level);
    }
    out.push({ open, high, low, close: level });
  }
  return out;
}
