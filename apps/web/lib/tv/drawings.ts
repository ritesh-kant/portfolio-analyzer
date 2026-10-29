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
  | 'text'
  | 'brush'
  | 'highlighter'
  | 'arrow'
  | 'arrowmarker'
  | 'arrowup'
  | 'arrowdown'
  | 'circle'
  | 'ellipse'
  | 'triangle';

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
  /** Fixed range volume profile: look and inputs (see `vpOf`). */
  vp?: VpSettings;
  /** Long / short position: account size, lot size and the share of the account risked (percent). */
  account?: number;
  lot?: number;
  riskPct?: number;
  /** Long / short position: box width in candles (a duration in seconds collapses across overnight gaps). */
  span?: number;
  locked?: boolean;
  /** Fib retracement settings; absent = TradingView defaults. */
  fib?: FibSettings;
  /** Id of the indicator pane the drawing lives in (its `p` values are on that pane's scale); absent = price pane. */
  pane?: string;
}

/** Kinds that only make sense against price: they read the candles' prices, so they stay on the price pane. */
export const PRICE_ONLY: ReadonlySet<DrawingKind> = new Set<DrawingKind>([
  'long',
  'short',
  'vprofile',
]);

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
  brush: 2,
  highlighter: 2,
  arrow: 2,
  arrowmarker: 2,
  arrowup: 1,
  arrowdown: 1,
  circle: 2,
  ellipse: 2,
  triangle: 2,
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
  brush: 'Brush',
  highlighter: 'Highlighter',
  arrow: 'Arrow',
  arrowmarker: 'Arrow marker',
  arrowup: 'Arrow mark up',
  arrowdown: 'Arrow mark down',
  circle: 'Circle',
  ellipse: 'Ellipse',
  triangle: 'Triangle',
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
  brush: '#2962ff',
  highlighter: '#fbc02d',
  arrow: '#2962ff',
  arrowmarker: '#2962ff',
  arrowup: '#089981',
  arrowdown: '#f23645',
  circle: '#2962ff',
  ellipse: '#2962ff',
  triangle: '#2962ff',
};

/** Kinds drawn by dragging: every mouse-move adds a point instead of moving the second anchor. */
export const FREEHAND: ReadonlySet<DrawingKind> = new Set<DrawingKind>(['brush', 'highlighter']);

/** Stroke width a new drawing of this kind starts with. */
export const DEFAULT_WIDTH: Partial<Record<DrawingKind, number>> = { highlighter: 12 };

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

export type LineStyle = 'solid' | 'dashed' | 'dotted';

export interface FibLevel {
  v: number;
  on: boolean;
  color: string;
}

export interface FibSettings {
  levels: FibLevel[];
  trend: { on: boolean; color: string; width: number; style: LineStyle };
  lines: { width: number; style: LineStyle };
  background: { on: boolean; opacity: number };
  labels: { show: boolean; price: boolean; percent: boolean; side: 'left' | 'right' };
  extendLeft: boolean;
  extendRight: boolean;
  reverse: boolean;
}

/** TradingView's out-of-the-box fib: first nine levels on, the rest listed but hidden. */
const EXTRA_FIB_LEVELS: FibLevel[] = [
  { v: 3.618, on: false, color: '#9c27b0' },
  { v: 4.236, on: false, color: '#e91e63' },
  { v: 1.272, on: false, color: '#fbc02d' },
  { v: 1.414, on: false, color: '#ff9800' },
  { v: 2.272, on: false, color: '#4caf50' },
  { v: 2.414, on: false, color: '#089981' },
  { v: 2, on: false, color: '#00bcd4' },
  { v: 3, on: false, color: '#2962ff' },
  { v: 3.272, on: false, color: '#9c27b0' },
  { v: 3.414, on: false, color: '#e91e63' },
  { v: 4, on: false, color: '#f23645' },
  { v: 4.272, on: false, color: '#ff9800' },
  { v: 4.414, on: false, color: '#fbc02d' },
  { v: 4.618, on: false, color: '#4caf50' },
  { v: 4.764, on: false, color: '#089981' },
];

export function defaultFib(): FibSettings {
  return {
    levels: [...FIB_LEVELS.map((l) => ({ ...l, on: true })), ...EXTRA_FIB_LEVELS.map((l) => ({ ...l }))],
    trend: { on: true, color: '#787b86', width: 1, style: 'dashed' },
    lines: { width: 1, style: 'solid' },
    background: { on: true, opacity: 0.09 },
    labels: { show: true, price: true, percent: false, side: 'left' },
    extendLeft: false,
    extendRight: false,
    reverse: false,
  };
}

export const fibOf = (d: Drawing): FibSettings => d.fib ?? defaultFib();

/** Fixed range volume profile settings, laid out like TradingView's Inputs / Style tabs. */
export interface VpSettings {
  /** Inputs */
  volume: 'updown' | 'total';
  /** Value area as a percent of the profile's volume. */
  valueArea: number;
  placement: 'left' | 'right';
  /** Longest bar as a percent of the box width. */
  width: number;
  /** Style */
  upColor: string;
  downColor: string;
  vaUpColor: string;
  vaDownColor: string;
  /** Bar opacity, 0–1. */
  opacity: number;
  showPoc: boolean;
  pocColor: string;
  pocWidth: number;
  showVa: boolean;
  vaLineColor: string;
  showLabels: boolean;
  showBackground: boolean;
  backgroundColor: string;
  backgroundOpacity: number;
}

/** TradingView's look: width 30% of the box, rows blue (up) / yellow (down), value area brighter, red POC and blue VAH/VAL. */
export const VP_DEFAULTS: VpSettings = {
  volume: 'updown',
  valueArea: 70,
  placement: 'left',
  width: 30,
  upColor: '#3b66e8',
  downColor: '#e6d24f',
  vaUpColor: '#3b66e8',
  vaDownColor: '#e6d24f',
  opacity: 0.85,
  showPoc: true,
  pocColor: '#f23645',
  pocWidth: 2,
  showVa: true,
  vaLineColor: '#2962ff',
  showLabels: false,
  showBackground: true,
  backgroundColor: '#3b66e8',
  backgroundOpacity: 0.06,
};

export const vpOf = (d: Drawing): VpSettings => ({ ...VP_DEFAULTS, ...d.vp });

/** `#rrggbb` + alpha → `rgba()`. */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return hex;
  return `rgba(${parseInt(m[1]!, 16)}, ${parseInt(m[2]!, 16)}, ${parseInt(m[3]!, 16)}, ${alpha})`;
}

export function lineDash(style: LineStyle, width: number): number[] {
  if (style === 'dashed') return [width * 4 + 2, width * 3 + 2];
  if (style === 'dotted') return [width, width * 2 + 1];
  return [];
}

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
