/** A numeric filter: either bound may be absent (`> min`, `< max`, or both for a range). */
export interface RangeFilter {
  min?: number;
  max?: number;
}
export interface MultiFilter {
  in: string[];
}
export interface ToggleFilter {
  on: boolean;
}
export type FilterValue = RangeFilter | MultiFilter | ToggleFilter;

/** Active filters keyed by field id (see `FIELDS`). A field absent from the map is not filtering. */
export type FilterState = Record<string, FilterValue>;

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

export interface ScreenerRequest {
  /** Market id (see `MARKETS`); missing = USA. */
  market?: string;
  filters: FilterState;
  sort: SortState;
  /** Column ids currently shown: the server fetches slow data (float, rel vol at time…) only if one is visible. */
  columns: string[];
  limit?: number;
}

/**
 * One row of results. Quote-derived values are always present when Yahoo has them;
 * `undefined` means "not fetched" (nothing asked for it), `null` means "fetched, Yahoo has no value".
 */
export interface ScreenerRow {
  symbol: string;
  name: string;
  exchange: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  volume: number | null;
  avgVolume: number | null;
  rvol: number | null;
  dollarVol: number | null;
  sharesOut: number | null;
  marketCap: number | null;
  gapPct: number | null;
  fromOpenPct: number | null;
  rangePct: number | null;
  from52High: number | null;
  perf52w: number | null;
  vs50d: number | null;
  vs200d: number | null;
  pe: number | null;
  forwardPe: number | null;
  eps: number | null;
  divYield: number | null;
  priceBook: number | null;
  analyst: string | null;
  listedDays: number | null;
  earningsDays: number | null;
  /** Needs the 5-minute bars (`intraday` tier). */
  rvolTime?: number | null;
  /** Needs `quoteSummary` (`profile` tier). */
  float?: number | null;
  /** True when Yahoo has no float and `float` is the shares outstanding instead (an upper bound on the real float). */
  floatEstimated?: boolean;
  sector?: string | null;
  industry?: string | null;
  shortFloat?: number | null;
  insiders?: number | null;
  institutions?: number | null;
  beta?: number | null;
  epsGrowth?: number | null;
  revGrowth?: number | null;
  roe?: number | null;
  peg?: number | null;
  /** Needs the 5-minute bars of the last few sessions (`vprofile` tier): see `poc-retest.ts`. */
  pocStage?: string | null;
  pocPrice?: number | null;
  pocEntry?: number | null;
  pocStop?: number | null;
  pocRiskPct?: number | null;
  pocEdgePct?: number | null;
  pocDistPct?: number | null;
  pocZoneLo?: number | null;
  pocZoneHi?: number | null;
  /** Unix seconds (see `PocSetup`): where the chart draws the zone, the departure and the first touch. */
  pocFrom?: number | null;
  pocDepartAt?: number | null;
  pocTouchAt?: number | null;
  /** Sessions back the POC comes from (1 = yesterday); the furthest price after moving away, its time, and how
   *  much of that move price has given back (%). */
  pocSessionsAgo?: number | null;
  pocExtreme?: number | null;
  pocExtremeAt?: number | null;
  pocPullbackPct?: number | null;
}

export interface ScreenerResponse {
  rows: ScreenerRow[];
  /** Names that satisfied every filter, before the row limit. */
  total: number;
  /** ISO time the data was pulled. */
  asOf: string;
  /** Things the user should know: truncated candidate pools, names dropped for missing data. */
  notes: string[];
}
