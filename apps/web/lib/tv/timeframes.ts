import type { Bar } from './types';

export interface Timeframe {
  id: string;
  label: string;
  /** Candle length in seconds (approximate for W / M). */
  secs: number;
  /** Interval requested from the data source. */
  source: string;
  /** Range requested from the data source — the longest it serves for that interval. */
  range: string;
  /** When set, source bars are merged into candles this many seconds long. */
  aggregateSecs?: number;
  intraday: boolean;
}

export const TIMEFRAMES: Timeframe[] = [
  { id: '1m', label: '1m', secs: 60, source: '1m', range: '7d', intraday: true },
  {
    id: '3m',
    label: '3m',
    secs: 180,
    source: '1m',
    range: '7d',
    aggregateSecs: 180,
    intraday: true,
  },
  { id: '5m', label: '5m', secs: 300, source: '5m', range: '60d', intraday: true },
  { id: '15m', label: '15m', secs: 900, source: '15m', range: '60d', intraday: true },
  { id: '30m', label: '30m', secs: 1800, source: '30m', range: '60d', intraday: true },
  { id: '1h', label: '1h', secs: 3600, source: '60m', range: '2y', intraday: true },
  {
    id: '4h',
    label: '4h',
    secs: 14400,
    source: '60m',
    range: '2y',
    aggregateSecs: 14400,
    intraday: true,
  },
  { id: '1D', label: 'D', secs: 86400, source: '1d', range: '10y', intraday: false },
  { id: '1W', label: 'W', secs: 604800, source: '1wk', range: 'max', intraday: false },
  { id: '1M', label: 'M', secs: 2592000, source: '1mo', range: 'max', intraday: false },
];

export const timeframeById = (id: string): Timeframe =>
  TIMEFRAMES.find((tf) => tf.id === id) ?? TIMEFRAMES[2]!;

const zoneParts = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(timeZone: string): Intl.DateTimeFormat {
  let f = zoneParts.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    zoneParts.set(timeZone, f);
  }
  return f;
}

/**
 * lightweight-charts draws every timestamp as if it were UTC. Handing it the
 * exchange's wall-clock time (as if UTC) makes the axis read 09:15 for an NSE
 * open on any viewer's machine.
 */
const wallCache = new Map<string, number>();

export function wallClock(sec: number, timeZone: string): number {
  // Every poll re-derives the clock for thousands of unchanged bars; Intl is the slow part.
  const cacheKey = `${timeZone}|${sec}`;
  const hit = wallCache.get(cacheKey);
  if (hit !== undefined) return hit;
  if (wallCache.size > 200_000) wallCache.clear();
  const parts: Record<string, number> = {};
  for (const p of partsFormatter(timeZone).formatToParts(new Date(sec * 1000))) {
    if (p.type !== 'literal') parts[p.type] = Number(p.value);
  }
  const wall = Math.floor(
    Date.UTC(parts.year!, parts.month! - 1, parts.day!, parts.hour!, parts.minute!, parts.second!) /
      1000,
  );
  wallCache.set(cacheKey, wall);
  return wall;
}

/** YYYY-MM-DD of an instant in the exchange's zone. */
export function sessionDay(sec: number, timeZone: string): string {
  const d = new Date(wallClock(sec, timeZone) * 1000);
  return d.toISOString().slice(0, 10);
}

/**
 * Merge bars into candles of `bucketSecs`, anchored to each session's first
 * bar so a 3m candle on NSE starts 09:15, 09:18, … and a 4h candle 09:15, 13:15.
 */
export function aggregateBars(bars: Bar[], bucketSecs: number, timeZone: string): Bar[] {
  const out: Bar[] = [];
  let day = '';
  let anchor = 0;
  let current: Bar | null = null;
  let currentBucket = -1;
  for (const bar of bars) {
    const d = sessionDay(bar.time, timeZone);
    if (d !== day) {
      day = d;
      anchor = bar.time;
      current = null;
      currentBucket = -1;
    }
    const bucket = Math.floor((bar.time - anchor) / bucketSecs);
    if (current && bucket === currentBucket) {
      current.high = Math.max(current.high, bar.high);
      current.low = Math.min(current.low, bar.low);
      current.close = bar.close;
      current.volume += bar.volume;
    } else {
      current = { ...bar, time: anchor + bucket * bucketSecs };
      currentBucket = bucket;
      out.push(current);
    }
  }
  return out;
}

/** Merge freshly fetched bars over the tail of an existing series (replace same time, append newer). */
export function mergeBars(existing: Bar[], fresh: Bar[]): Bar[] {
  if (!fresh.length) return existing;
  if (!existing.length) return fresh;
  const firstFresh = fresh[0]!.time;
  const keep = existing.filter((b) => b.time < firstFresh);
  return [...keep, ...fresh];
}

export function heikinAshi(bars: Bar[]): Bar[] {
  const out: Bar[] = [];
  let prevOpen = 0;
  let prevClose = 0;
  bars.forEach((b, i) => {
    const close = (b.open + b.high + b.low + b.close) / 4;
    const open = i === 0 ? (b.open + b.close) / 2 : (prevOpen + prevClose) / 2;
    out.push({
      time: b.time,
      open,
      close,
      high: Math.max(b.high, open, close),
      low: Math.min(b.low, open, close),
      volume: b.volume,
    });
    prevOpen = open;
    prevClose = close;
  });
  return out;
}

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** TradingView's crosshair time label: "Thu 15 Oct '26  19:00". `wall` is already exchange wall-clock. */
export function formatCrosshairTime(wall: number, intraday: boolean): string {
  const d = new Date(wall * 1000);
  const date = `${DAY_NAMES[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, '0')} ${
    MONTH_NAMES[d.getUTCMonth()]
  } '${String(d.getUTCFullYear()).slice(2)}`;
  if (!intraday) return date;
  return `${date}  ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

/** Decimal places that show the instrument's tick: 2 for normal prices, more for sub-rupee/penny names. */
export function pricePrecision(price: number): number {
  if (!Number.isFinite(price) || price <= 0) return 2;
  if (price < 0.1) return 5;
  if (price < 1) return 4;
  return 2;
}
