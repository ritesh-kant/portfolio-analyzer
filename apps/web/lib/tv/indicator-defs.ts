import type { ArStrategyCfg } from './ar-strategy';
import { AVP_DEFAULTS, VP_DEFAULTS, type VpSettings } from './drawings';
import type { GexConvention, GexStyle } from './gex';
import { DEFAULT_ROWS } from './volume-profile';

export type IndicatorType =
  | 'volume'
  | 'rvol'
  | 'ema'
  | 'sma'
  | 'vwap'
  | 'bb'
  | 'macd'
  | 'rsi'
  | 'atr'
  | 'svp'
  | 'avp'
  | 'cvd'
  | 'gex'
  // Auction Resolution (AR) model — research/specs/orderflow-course-ar-model.md
  | 'arsess'
  | 'avr'
  | 'arfib'
  | 'arstruct'
  | 'arenv'
  | 'arstrat';

export interface IndicatorCfg {
  id: string;
  type: IndicatorType;
  /** Main length: EMA/SMA/BB/RVOL/RSI length, MACD fast. */
  length: number;
  /** MACD slow. */
  length2?: number;
  /** MACD signal. */
  length3?: number;
  /** Session / anchored volume profile: look and inputs on top of `SVP_DEFAULTS` / `AVP_VP` (rows = `length`). */
  vp?: Partial<VpSettings>;
  /** Anchored volume profile: the anchor candle's time (unix seconds) per symbol; absent = the latest session's start. */
  anchors?: Record<string, number>;
  /** Cumulative volume delta: the period the running total restarts in. */
  anchor?: 'session' | 'week' | 'month';
  /** CVD: mark price/CVD divergences. Absent = regular ones. */
  divMode?: 'off' | 'regular' | 'all';
  /** CVD divergence: candles each side a swing must clear (absent = 3). */
  divPivot?: number;
  /** CVD divergence: the two swings are at most this many candles apart (absent = 30). */
  divGap?: number;
  /** Volume: growing / falling bar colours (hex; drawn at 50% opacity). Absent = green / red. */
  volUp?: string;
  volDown?: string;
  /** Volume: colour by close vs the previous close instead of close vs open. */
  volPrevClose?: boolean;
  /** Volume: draw the moving average of volume (length = `length`) in `volMaColor`. */
  volMa?: boolean;
  volMaColor?: string;
  /** Volume: show the last value on the price scale / the values in the status line (absent = off / on). */
  volLabels?: boolean;
  volStatus?: boolean;
  /** Gamma levels: who is assumed short the options (absent = dealers long calls, short puts). */
  gexConvention?: GexConvention;
  /** Gamma levels: draw net GEX by strike in a column right of the prices (absent = on). */
  gexProfile?: boolean;
  /** Gamma levels: tint the pane green above the gamma flip (HVL) and red below it (absent = on). */
  gexZones?: boolean;
  /** Gamma levels: background strength, % of the default tint (absent = 50). */
  gexZoneOpacity?: number;
  /** Gamma levels: the read-out panel at the chart's top right (absent = on). */
  gexPanel?: boolean;
  /** Gamma levels: per-level show / colour overrides, by `GEX_LINES` key. */
  gexStyle?: GexStyle;
  /** AR strategy: overrides of `AR_STRATEGY_DEFAULTS`. */
  arStrat?: Partial<ArStrategyCfg>;
  /** Bollinger std-dev multiplier. */
  mult?: number;
  color: string;
  /** Hidden from the chart (still listed in the legend so it can be shown again). */
  hidden?: boolean;
  /** Own-pane indicators: pane shrunk to its legend row. */
  collapsed?: boolean;
}

export interface CatalogEntry {
  type: IndicatorType;
  name: string;
  description: string;
  /** Where it draws: on the price pane, as a volume overlay, or in its own pane. */
  where: 'overlay' | 'volume' | 'pane';
  defaults: Omit<IndicatorCfg, 'id' | 'color'>;
  /** Listed under this heading in the picker (absent = the general list). */
  group?: 'AR Model';
}

export const CATALOG: CatalogEntry[] = [
  {
    type: 'volume',
    name: 'Volume',
    description: 'Shares traded per candle',
    where: 'volume',
    defaults: { type: 'volume', length: 20 },
  },
  {
    type: 'rvol',
    name: 'Relative Volume',
    description: 'Volume ÷ average of previous N candles',
    where: 'pane',
    defaults: { type: 'rvol', length: 20 },
  },
  {
    type: 'ema',
    name: 'Moving Average Exponential',
    description: 'EMA of close',
    where: 'overlay',
    defaults: { type: 'ema', length: 9 },
  },
  {
    type: 'sma',
    name: 'Moving Average',
    description: 'Simple moving average of close',
    where: 'overlay',
    defaults: { type: 'sma', length: 20 },
  },
  {
    type: 'vwap',
    name: 'VWAP',
    description: 'Session volume-weighted average price',
    where: 'overlay',
    defaults: { type: 'vwap', length: 0 },
  },
  {
    type: 'bb',
    name: 'Bollinger Bands',
    description: 'SMA ± N standard deviations',
    where: 'overlay',
    defaults: { type: 'bb', length: 20, mult: 2 },
  },
  {
    type: 'macd',
    name: 'MACD',
    description: 'Moving Average Convergence Divergence',
    where: 'pane',
    defaults: { type: 'macd', length: 12, length2: 26, length3: 9 },
  },
  {
    type: 'atr',
    name: 'Average True Range',
    description: 'Wilder-smoothed average of true range',
    where: 'pane',
    defaults: { type: 'atr', length: 14 },
  },
  {
    type: 'rsi',
    name: 'Relative Strength Index',
    description: 'Wilder RSI of close',
    where: 'pane',
    defaults: { type: 'rsi', length: 14 },
  },
  {
    type: 'svp',
    name: 'Session Volume Profile',
    description: 'Volume by price for every trading day, with POC and value area',
    where: 'overlay',
    defaults: { type: 'svp', length: DEFAULT_ROWS },
  },
  {
    type: 'avp',
    name: 'Anchored Volume Profile',
    description: 'Volume by price from an anchor candle to the latest bar, with POC and value area',
    where: 'overlay',
    defaults: { type: 'avp', length: DEFAULT_ROWS },
  },
  {
    type: 'cvd',
    name: 'Cumulative Volume Delta',
    description: 'Running total of buying minus selling volume, drawn as candles',
    where: 'pane',
    defaults: { type: 'cvd', length: 0, anchor: 'session' },
  },
  {
    type: 'gex',
    name: 'Gamma Levels (GEX)',
    description: 'Option walls, gamma flip and 1-day range from the option chain (NSE and US)',
    where: 'overlay',
    defaults: { type: 'gex', length: 45 },
  },
  {
    type: 'arsess',
    name: 'Sessions: Asia · London · New York',
    description: 'A box per session (New York time): Asia sets the range, London fakes, New York resolves',
    where: 'overlay',
    defaults: { type: 'arsess', length: 0 },
    group: 'AR Model',
  },
  {
    type: 'avr',
    name: 'Asia Range Levels (AVR)',
    description: 'Asia high/low, midpoint, quarters and the expected expansion levels from the 20-day ATR',
    where: 'overlay',
    defaults: { type: 'avr', length: 20 },
    group: 'AR Model',
  },
  {
    type: 'arfib',
    name: 'Range Fib Zone',
    description: 'Latest swing leg: 0.705–0.886 zone, value area, premium/discount, target and invalidation',
    where: 'overlay',
    defaults: { type: 'arfib', length: 30, mult: 2 },
    group: 'AR Model',
  },
  {
    type: 'arstruct',
    name: 'Market Structure',
    description: 'Swing highs and lows labelled HH / HL / LH / LL, and the trend on 4h · 1h · 15m · 5m',
    where: 'overlay',
    defaults: { type: 'arstruct', length: 14, mult: 2 },
    group: 'AR Model',
  },
  {
    type: 'arenv',
    name: 'Market Environment',
    description: 'Trending (expansion), ranging (rotation) or choppy (compression), and which setups fit',
    where: 'overlay',
    defaults: { type: 'arenv', length: 24 },
    group: 'AR Model',
  },
  {
    type: 'arstrat',
    name: 'AR Strategy',
    description: 'His 4-step checklist on every candle, with entries, stops, targets and skipped setups marked',
    where: 'overlay',
    defaults: { type: 'arstrat', length: 0 },
    group: 'AR Model',
  },
];

/** The "AR Model" template: everything the course uses that can be drawn from candles alone. */
export const AR_TEMPLATE: IndicatorType[] = ['arsess', 'avr', 'arfib', 'arstruct', 'arenv', 'arstrat'];
export const isArType = (t: IndicatorType) => AR_TEMPLATE.includes(t);

export const catalogOf = (type: IndicatorType): CatalogEntry =>
  CATALOG.find((c) => c.type === type)!;

export const PALETTE = [
  '#2962ff',
  '#ff9800',
  '#ab47bc',
  '#089981',
  '#f23645',
  '#00bcd4',
  '#fbc02d',
  '#e91e63',
];

export const indicatorId = () =>
  `i${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

export function makeIndicator(type: IndicatorType, existing: IndicatorCfg[]): IndicatorCfg {
  const entry = catalogOf(type);
  const sameType = existing.filter((i) => i.type === type).length;
  const length =
    type === 'ema' && sameType === 1
      ? 20
      : type === 'ema' && sameType === 2
        ? 50
        : type === 'ema' && sameType >= 3
          ? 200
          : entry.defaults.length;
  return {
    id: indicatorId(),
    ...entry.defaults,
    length,
    color: PALETTE[existing.length % PALETTE.length]!,
  };
}

export const DEFAULT_INDICATORS: IndicatorCfg[] = [
  { id: 'i-vol', type: 'volume', length: 20, color: '#26a69a' },
  { id: 'i-ema9', type: 'ema', length: 9, color: '#2962ff' },
  { id: 'i-ema20', type: 'ema', length: 20, color: '#ff9800' },
  { id: 'i-macd', type: 'macd', length: 12, length2: 26, length3: 9, color: '#2962ff' },
];

/** Human label as TradingView prints it in the legend. */
export function indicatorLabel(i: IndicatorCfg): string {
  switch (i.type) {
    case 'volume':
      return 'Vol';
    case 'rvol':
      return `RVol ${i.length}`;
    case 'ema':
      return `EMA ${i.length} close`;
    case 'sma':
      return `MA ${i.length} close`;
    case 'vwap':
      return 'VWAP';
    case 'bb':
      return `BB ${i.length} ${i.mult ?? 2}`;
    case 'macd':
      return `MACD close ${i.length} ${i.length2} ${i.length3}`;
    case 'atr':
      return `ATR ${i.length}`;
    case 'rsi':
      return `RSI ${i.length} close`;
    case 'svp':
      return `SVP ${i.length}`;
    case 'avp':
      return `AVP ${i.length}`;
    case 'cvd':
      return 'CVD';
    case 'gex':
      return `GEX ${i.length}d`;
    case 'arsess':
      return 'Sessions';
    case 'avr':
      return `AVR ${i.length}`;
    case 'arfib':
      return `Range Fib ${i.mult ?? 2}×ATR`;
    case 'arstruct':
      return `Structure ${i.mult ?? 2}×ATR`;
    case 'arenv':
      return `Environment ${i.length}`;
    case 'arstrat':
      return 'AR Strategy';
  }
}

/** TradingView's session volume profile: the fixed-range look, but no box behind the histogram. */
export const SVP_DEFAULTS: VpSettings = { ...VP_DEFAULTS, showBackground: false };
export const svpOf = (cfg: IndicatorCfg): VpSettings => ({ ...SVP_DEFAULTS, ...cfg.vp });

/** TradingView's anchored volume profile: histogram on the pane's right edge (see `AVP_DEFAULTS`). */
export const AVP_VP: VpSettings = { ...VP_DEFAULTS, ...AVP_DEFAULTS };
export const avpOf = (cfg: IndicatorCfg): VpSettings => ({ ...AVP_VP, ...cfg.vp });

export const VOL_UP_DEFAULT = '#089981';
export const VOL_DOWN_DEFAULT = '#f23645';
export const VOL_MA_DEFAULT = '#2962ff';
