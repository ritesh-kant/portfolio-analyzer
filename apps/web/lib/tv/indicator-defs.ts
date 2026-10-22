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
  | 'cvd';

export interface IndicatorCfg {
  id: string;
  type: IndicatorType;
  /** Main length: EMA/SMA/BB/RVOL/RSI length, MACD fast. */
  length: number;
  /** MACD slow. */
  length2?: number;
  /** MACD signal. */
  length3?: number;
  /** Session volume profile: widest bar as a percentage of the session's width. */
  width?: number;
  /** Session volume profile colouring: up/down volume split, or total with a grey value area. */
  style?: 'split' | 'total';
  /** Cumulative volume delta: the period the running total restarts in. */
  anchor?: 'session' | 'week' | 'month';
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
    defaults: { type: 'svp', length: 0, width: 30, style: 'total' },
  },
  {
    type: 'cvd',
    name: 'Cumulative Volume Delta',
    description: 'Running total of buying minus selling volume, drawn as candles',
    where: 'pane',
    defaults: { type: 'cvd', length: 0, anchor: 'session' },
  },
];

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
      return `SVP ${i.length > 0 ? i.length : 'auto'}`;
    case 'cvd':
      return 'CVD';
  }
}
