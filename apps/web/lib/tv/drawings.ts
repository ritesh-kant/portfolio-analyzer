export type DrawingKind =
  | 'trend'
  | 'ray'
  | 'hline'
  | 'hray'
  | 'vline'
  | 'rect'
  | 'fib'
  | 'measure'
  | 'vprofile'
  | 'long'
  | 'short'
  | 'text';

/** `t` is real unix seconds (may be fractional or in the future); `p` is price. */
export interface Pt {
  t: number;
  p: number;
}

export interface Drawing {
  id: string;
  kind: DrawingKind;
  pts: Pt[];
  color: string;
  width: number;
  text?: string;
  /** Fixed range volume profile: number of price rows. */
  rows?: number;
  /** Long / short position: account size, lot size and the share of the account risked (percent). */
  account?: number;
  lot?: number;
  riskPct?: number;
  locked?: boolean;
}

export const POINTS_NEEDED: Record<DrawingKind, number> = {
  trend: 2,
  ray: 2,
  hline: 1,
  hray: 1,
  vline: 1,
  rect: 2,
  fib: 2,
  measure: 2,
  vprofile: 2,
  long: 1,
  short: 1,
  text: 1,
};

export const KIND_LABEL: Record<DrawingKind, string> = {
  trend: 'Trend line',
  ray: 'Ray',
  hline: 'Horizontal line',
  hray: 'Horizontal ray',
  vline: 'Vertical line',
  rect: 'Rectangle',
  fib: 'Fib retracement',
  measure: 'Measure',
  vprofile: 'Fixed range volume profile',
  long: 'Long position',
  short: 'Short position',
  text: 'Text',
};

export const DEFAULT_COLOR: Record<DrawingKind, string> = {
  trend: '#2962ff',
  ray: '#2962ff',
  hline: '#2962ff',
  hray: '#2962ff',
  vline: '#2962ff',
  rect: '#2962ff',
  fib: '#787b86',
  measure: '#2962ff',
  vprofile: '#2962ff',
  long: '#089981',
  short: '#f23645',
  text: '#d1d4dc',
};

export const SWATCHES = [
  '#d1d4dc',
  '#f23645',
  '#ff9800',
  '#fbc02d',
  '#089981',
  '#00bcd4',
  '#2962ff',
  '#9c27b0',
  '#e91e63',
];

/** TradingView's default retracement levels and their colours. */
export const FIB_LEVELS: { v: number; color: string }[] = [
  { v: 0, color: '#787b86' },
  { v: 0.236, color: '#f23645' },
  { v: 0.382, color: '#ff9800' },
  { v: 0.5, color: '#4caf50' },
  { v: 0.618, color: '#089981' },
  { v: 0.786, color: '#00bcd4' },
  { v: 1, color: '#787b86' },
  { v: 1.618, color: '#2962ff' },
  { v: 2.618, color: '#f23645' },
];

export const newId = () => `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/**
 * Maps real timestamps to the chart's logical bar index and back, extrapolating
 * past either end so a line can be drawn into the future.
 */
export interface TimeAxis {
  toLogical(t: number): number;
  toTime(logical: number): number;
  count: number;
}

export function makeTimeAxis(times: number[], stepSecs: number): TimeAxis {
  const n = times.length;
  const step = stepSecs > 0 ? stepSecs : 60;
  return {
    count: n,
    toLogical(t) {
      if (!n) return 0;
      if (t <= times[0]!) return (t - times[0]!) / step;
      if (t >= times[n - 1]!) return n - 1 + (t - times[n - 1]!) / step;
      let lo = 0;
      let hi = n - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid]! <= t) lo = mid;
        else hi = mid;
      }
      return lo + (t - times[lo]!) / (times[hi]! - times[lo]!);
    },
    toTime(l) {
      if (!n) return 0;
      if (l <= 0) return times[0]! + l * step;
      if (l >= n - 1) return times[n - 1]! + (l - (n - 1)) * step;
      const i = Math.floor(l);
      return times[i]! + (l - i) * (times[i + 1]! - times[i]!);
    },
  };
}

export function formatDuration(secs: number): string {
  const s = Math.abs(secs);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m || !parts.length) parts.push(`${m}m`);
  return parts.join(' ');
}

export function translate(d: Drawing, dt: number, dp: number): Drawing {
  return { ...d, pts: d.pts.map((pt) => ({ t: pt.t + dt, p: pt.p + dp })) };
}
