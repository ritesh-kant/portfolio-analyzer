/**
 * Session shading, as TradingView draws it: hours outside the instrument's
 * REGULAR session are tinted, and a dashed line marks the start of each
 * trading day.
 *
 * Regular hours are exchange rules, not something the data source reports, so
 * they live here. Futures use the exchange's regular-hours window (e.g. COMEX
 * gold 08:20–13:30 ET), which is NOT the 23-hour electronic session the bars
 * cover. Instruments with no defined window (NSE equities, FX, crypto) get no
 * shading.
 */

/** Regular session as minutes after midnight in the exchange's zone. */
export interface SessionDef {
  start: number;
  end: number;
}

const hm = (h: number, m = 0) => h * 60 + m;

const US_EQUITY: SessionDef = { start: hm(9, 30), end: hm(16) };

// Futures roots → regular hours in US Eastern time.
const FUTURES_RTH: Record<string, SessionDef> = {
  GC: { start: hm(8, 20), end: hm(13, 30) },
  MGC: { start: hm(8, 20), end: hm(13, 30) },
  SI: { start: hm(8, 25), end: hm(13, 25) },
  HG: { start: hm(8, 10), end: hm(13, 0) },
  PL: { start: hm(8, 20), end: hm(13, 5) },
  PA: { start: hm(8, 20), end: hm(13, 0) },
  CL: { start: hm(9, 0), end: hm(14, 30) },
  BZ: { start: hm(9, 0), end: hm(14, 30) },
  NG: { start: hm(9, 0), end: hm(14, 30) },
  ES: { start: hm(9, 30), end: hm(16) },
  NQ: { start: hm(9, 30), end: hm(16) },
  YM: { start: hm(9, 30), end: hm(16) },
  RTY: { start: hm(9, 30), end: hm(16) },
  ZB: { start: hm(8, 20), end: hm(15, 0) },
  ZN: { start: hm(8, 20), end: hm(15, 0) },
  ZC: { start: hm(10, 30), end: hm(14, 20) },
  ZS: { start: hm(10, 30), end: hm(14, 20) },
  ZW: { start: hm(10, 30), end: hm(14, 20) },
};

export interface SessionInfo {
  symbol: string;
  /** Yahoo's instrumentType: EQUITY, ETF, FUTURE, … */
  instrumentType: string;
  timezone: string;
}

/** Regular-hours window for a symbol, or null when none is defined. */
export function sessionFor(info: SessionInfo): SessionDef | null {
  const type = info.instrumentType.toUpperCase();
  if (info.timezone !== 'America/New_York') return null;
  if (type === 'FUTURE') return FUTURES_RTH[info.symbol.replace(/=F$/, '')] ?? null;
  if (type === 'EQUITY' || type === 'ETF') return US_EQUITY;
  return null;
}

/** Whether pre/post-market bars exist to shade (US stocks only; futures bars already cover the whole day). */
export const hasExtendedBars = (info: SessionInfo) =>
  info.timezone === 'America/New_York' &&
  ['EQUITY', 'ETF'].includes(info.instrumentType.toUpperCase());

/** For each candle: is it outside regular hours? `wall` is exchange wall-clock seconds. */
export function extendedMask(wall: number[], def: SessionDef): boolean[] {
  return wall.map((t) => {
    const minute = Math.floor((t % 86400) / 60);
    return minute < def.start || minute >= def.end;
  });
}

/** Contiguous runs of `true` as inclusive [firstIndex, lastIndex] pairs. */
export function runs(mask: boolean[]): [number, number][] {
  const out: [number, number][] = [];
  let from = -1;
  mask.forEach((on, i) => {
    if (on && from < 0) from = i;
    if (!on && from >= 0) {
      out.push([from, i - 1]);
      from = -1;
    }
  });
  if (from >= 0) out.push([from, mask.length - 1]);
  return out;
}

/**
 * Indices of candles that start a new session: the first one after a gap longer
 * than two candles (overnight, weekend, or futures' daily maintenance break).
 */
export function sessionBreaks(times: number[], candleSecs: number): number[] {
  const out: number[] = [];
  for (let i = 1; i < times.length; i += 1) {
    if (times[i]! - times[i - 1]! > candleSecs * 2) out.push(i);
  }
  return out;
}
