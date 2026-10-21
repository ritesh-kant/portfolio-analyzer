/** A completed or forming candle. `time` is the candle START, unix seconds (real UTC). */
export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SymbolMeta {
  symbol: string;
  name: string;
  currency: string;
  exchange: string;
  /** IANA zone the exchange's clock is drawn in. */
  timezone: string;
  /** Yahoo's instrumentType: EQUITY, ETF, FUTURE, CURRENCY, … */
  instrumentType: string;
  prevClose: number | null;
  /** Last regular-market price, for the header when bars are stale. */
  price: number | null;
}

export interface BarsResponse {
  meta: SymbolMeta;
  interval: string;
  bars: Bar[];
}

export interface SearchHit {
  /** Ticker as the chart loads it (Yahoo). */
  symbol: string;
  /** Ticker as people say it (GC for GC=F). Falls back to `symbol`. */
  display?: string;
  /** Avatar colour in the search list. */
  color?: string;
  name: string;
  exchange: string;
  type: string;
}

/** A price level in the footprint: volume traded at the bid (sells) and at the ask (buys). */
export interface FootprintLevel {
  price: number;
  bid: number;
  ask: number;
}

export interface FootprintCandle {
  /** Index of the point-of-control level (most total volume). */
  poc: number;
  levels: FootprintLevel[];
  /** ask − bid summed over every level. */
  delta: number;
  total: number;
  /** Row height in price units the levels were bucketed to. */
  step: number;
}
