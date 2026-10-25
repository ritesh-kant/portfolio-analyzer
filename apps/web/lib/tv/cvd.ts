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

/**
 * NSE closing prints. On large NSE stocks (RELIANCE, TCS, HDFCBANK, SBIN) nothing trades after 15:14: Yahoo serves
 * no bars (one stray tick of a few hundred shares at most), Upstox flat zero-volume ones, and then both have one flat
 * bar at 15:28 or 15:29 at the day's closing price that carries the closing volume (872,733 shares on RELIANCE
 * 2026-10-22, 26.6M on the 2026-08-31 index rebalance), sometimes followed by another zero-volume minute. One price
 * for the whole lot: there is no buyer or seller side to read, and it is not a candle the session traded.
 *
 * A 1-minute bar is one when it is flat (O=H=L=C), starts 15:15–15:29 exchange time and no later bar of its session
 * day has volume. Stocks that really trade to 15:29 (IKS, RHIM) end on ordinary bars, so nothing is flagged there; a
 * genuinely flat last trade in that window is flagged too, at the cost of one illiquid minute's volume.
 * `minuteOf` gives a time's minute of the exchange day, `dayOf` its session day.
 */
export function nseClosingPrints(
  bars: Bar[],
  minuteOf: (time: number) => number,
  dayOf: (time: number) => string,
): boolean[] {
  return bars.map((b, i) => flat(b) && lastInClosingWindow(bars, i, minuteOf, dayOf));
}

const flat = (b: Bar) => b.open === b.high && b.high === b.low && b.low === b.close;

/** Bar `i` starts 15:15–15:29 and is the last bar of its session day with volume (zero-volume fillers may follow). */
function lastInClosingWindow(
  bars: Bar[],
  i: number,
  minuteOf: (time: number) => number,
  dayOf: (time: number) => string,
): boolean {
  const b = bars[i]!;
  const minute = minuteOf(b.time);
  if (minute < 15 * 60 + 15 || minute >= 15 * 60 + 30) return false;
  const day = dayOf(b.time);
  for (let k = i + 1; k < bars.length && dayOf(bars[k]!.time) === day; k += 1) {
    if (bars[k]!.volume > 0) return false;
  }
  return true;
}

/**
 * Chart candles that are an NSE closing print: the session's last candle with volume, starting 15:15–15:29, with at
 * least 90% of its intrabar volume in a closing print (`subPrints`, from `nseClosingPrints` on `sub`). Yahoo's 15m/1h
 * candle there is the print plus the 15:14 price, so it has just two prices (open and close at the two extremes, or
 * flat); where no intrabars are loaded under it, that shape marks it. Measured on 60 days of 15m candles: 51/55
 * RELIANCE 15:15 candles have that shape, 0/60 on IKS and RHIM.
 */
export function closingPrintCandles(
  bars: Bar[],
  sub: Bar[],
  subPrints: boolean[],
  secs: number,
  minuteOf: (time: number) => number,
  dayOf: (time: number) => string,
): boolean[] {
  let j = 0;
  return bars.map((b, i) => {
    while (j < sub.length && sub[j]!.time < b.time) j += 1;
    if (!lastInClosingWindow(bars, i, minuteOf, dayOf)) return false;
    let all = 0;
    let print = 0;
    for (let k = j; k < sub.length && sub[k]!.time < b.time + secs; k += 1) {
      all += sub[k]!.volume;
      if (subPrints[k]) print += sub[k]!.volume;
    }
    if (all > 0) return print >= 0.9 * all;
    const twoPrice = (b.open === b.high && b.low === b.close) || (b.open === b.low && b.high === b.close);
    return twoPrice;
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
 * `prints`, when given, flags NSE closing prints among the intrabars (`nseClosingPrints`) and the chart bars
 * (`closingPrintCandles`): a flagged
 * intrabar adds nothing, and a flagged chart bar is `missing`.
 */
export function computeCvd(
  bars: Bar[],
  allSub: Bar[],
  secs: number,
  anchorOf: (time: number) => string,
  regular?: (time: number) => boolean,
  prints?: { sub: boolean[]; bars: boolean[] },
): CvdCandle[] {
  const sub = allSub.filter((b, k) => (!regular || regular(b.time)) && !prints?.sub[k]);
  const signed = signedVolumes(sub);
  const out: CvdCandle[] = [];
  let level = 0;
  let key = '';
  let j = 0;
  let fbSign = 0;
  let fbPrev: number | null = null;
  for (const [i, b] of bars.entries()) {
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
    // A closing print is the close, not trading: nothing to read either (and anything under it is not counted).
    if ((regular && n === 0 && !regular(b.time)) || prints?.bars[i]) {
      level = open;
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
