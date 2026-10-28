import { INDUSTRIES, SECTORS } from './industries.ts';
import { MARKETS, universeFor } from './markets.ts';
import { POC_STAGES } from './poc-retest.ts';
import type { FilterState, ScreenerRow, SortState } from './types.ts';

/**
 * Where a field's value comes from, cheapest first:
 *  - `quote`    : on every row Yahoo's screener returns (free).
 *  - `profile`  : Yahoo `quoteSummary`, one call per name, cached for the day (float, sector, short %…).
 *  - `intraday` : 5-minute bars, one call per name, cached ~1 min (relative volume at this time of day).
 *  - `vprofile` : the same 5-minute bars, read as volume profiles (previous session's POC and today's retest of it).
 * Filtering on a `profile`/`intraday` field makes the server fetch it for every name that survived the
 * cheaper filters, so put the cheap filters in first.
 */
export type Tier = 'quote' | 'profile' | 'intraday' | 'vprofile';
export type Unit = 'usd' | 'pct' | 'x' | 'shares' | 'usdbig' | 'num' | 'days' | 'text';
export type Group =
  | 'Price & move'
  | 'Volume'
  | 'Supply'
  | 'Valuation'
  | 'Fundamentals'
  | 'Technicals'
  | 'Volume profile'
  | 'Profile';

export interface FieldDef {
  id: string;
  label: string;
  /** Column header when it differs from `label`. */
  short?: string;
  group: Group;
  unit: Unit;
  tier: Tier;
  kind: 'range' | 'multi' | 'toggle' | 'none';
  /** Yahoo screener operand. Present = the filter runs on Yahoo (before anything is downloaded). */
  yahoo?: string;
  /** Yahoo field to ORDER BY when this column is sorted (only some fields are sortable there). */
  sortYahoo?: string;
  /** No table column; only a filter. */
  filterOnly?: boolean;
  /** Served by the API for the chart's POC scanner panel, not offered in the screener's filters or columns. */
  scannerOnly?: boolean;
  options?: readonly string[];
  hint?: string;
  /** Column width in px. */
  w?: number;
}

const f = (d: FieldDef): FieldDef => d;

export const ANALYST_NOTE = 'Yahoo consensus: Strong Buy · Buy · Hold · Underperform · Sell';

export const FIELDS: readonly FieldDef[] = [
  // ── price & move ──────────────────────────────────────────────────────────
  f({
    id: 'price',
    label: 'Price',
    group: 'Price & move',
    unit: 'usd',
    tier: 'quote',
    kind: 'range',
    yahoo: 'intradayprice',
    sortYahoo: 'intradayprice',
    w: 90,
  }),
  f({
    id: 'changePct',
    label: 'Change %',
    short: 'Chg %',
    group: 'Price & move',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    yahoo: 'percentchange',
    sortYahoo: 'percentchange',
    w: 90,
    hint: "Regular-session change vs yesterday's close.",
  }),
  f({
    id: 'change',
    label: 'Change',
    short: 'Chg',
    group: 'Price & move',
    unit: 'usd',
    tier: 'quote',
    kind: 'none',
    w: 80,
  }),
  f({
    id: 'gapPct',
    label: 'Gap %',
    group: 'Price & move',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 80,
    hint: "Today's open vs yesterday's close.",
  }),
  f({
    id: 'fromOpenPct',
    label: 'Change from open %',
    short: 'From open',
    group: 'Price & move',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 96,
    hint: 'Price now vs today’s open: has it faded since the open?',
  }),
  f({
    id: 'rangePct',
    label: 'Day range %',
    short: 'Range %',
    group: 'Price & move',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 84,
    hint: "Today's (high − low) ÷ low: how much it has moved around.",
  }),
  // ── volume ────────────────────────────────────────────────────────────────
  f({
    id: 'volume',
    label: 'Volume',
    short: 'Vol',
    group: 'Volume',
    unit: 'shares',
    tier: 'quote',
    kind: 'range',
    yahoo: 'dayvolume',
    sortYahoo: 'dayvolume',
    w: 90,
  }),
  f({
    id: 'avgVolume',
    label: 'Avg volume (3M)',
    short: 'Avg vol 3M',
    group: 'Volume',
    unit: 'shares',
    tier: 'quote',
    kind: 'range',
    yahoo: 'avgdailyvol3m',
    sortYahoo: 'avgdailyvol3m',
    w: 100,
  }),
  f({
    id: 'rvol',
    label: 'Rel vol (vs 3M avg)',
    short: 'Rel vol',
    group: 'Volume',
    unit: 'x',
    tier: 'quote',
    kind: 'range',
    w: 84,
    hint: "Today's volume so far ÷ the 3-month average FULL-day volume. Understates a morning spike: use “Rel vol at time” for that.",
  }),
  f({
    id: 'rvolTime',
    label: 'Rel vol at time',
    short: 'Rel vol at time',
    group: 'Volume',
    unit: 'x',
    tier: 'intraday',
    kind: 'range',
    w: 104,
    hint: 'Volume so far today ÷ the average volume traded by this same time of day over the previous 10 sessions (5-minute bars, regular hours).',
  }),
  f({
    id: 'dollarVol',
    label: 'Dollar volume',
    short: '$ volume',
    group: 'Volume',
    unit: 'usdbig',
    tier: 'quote',
    kind: 'range',
    w: 100,
    hint: 'Price × volume: how much money changed hands.',
  }),
  // ── supply ────────────────────────────────────────────────────────────────
  f({
    id: 'float',
    label: 'Float',
    group: 'Supply',
    unit: 'shares',
    tier: 'profile',
    kind: 'range',
    w: 84,
    hint: 'Shares available to trade (Yahoo). Missing for some new listings: those names are left out when a float filter is on.',
  }),
  f({
    id: 'sharesOut',
    label: 'Shares outstanding',
    short: 'Shares out',
    group: 'Supply',
    unit: 'shares',
    tier: 'quote',
    kind: 'range',
    w: 96,
  }),
  f({
    id: 'marketCap',
    label: 'Market cap',
    short: 'Mkt cap',
    group: 'Supply',
    unit: 'usdbig',
    tier: 'quote',
    kind: 'range',
    yahoo: 'intradaymarketcap',
    sortYahoo: 'intradaymarketcap',
    w: 100,
  }),
  f({
    id: 'shortFloat',
    label: 'Short % of float',
    short: 'Short % float',
    group: 'Supply',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'short_percentage_of_float.value',
    w: 100,
    hint: 'Reported twice a month, so up to ~2 weeks old.',
  }),
  f({
    id: 'insiders',
    label: 'Held by insiders %',
    short: 'Insiders %',
    group: 'Supply',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'pctheldinsider',
    w: 90,
  }),
  f({
    id: 'institutions',
    label: 'Held by institutions %',
    short: 'Institutions %',
    group: 'Supply',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'pctheldinst',
    w: 100,
  }),
  // ── valuation ─────────────────────────────────────────────────────────────
  f({
    id: 'pe',
    label: 'P/E (trailing)',
    short: 'P/E',
    group: 'Valuation',
    unit: 'x',
    tier: 'quote',
    kind: 'range',
    yahoo: 'peratio.lasttwelvemonths',
    w: 72,
  }),
  f({
    id: 'forwardPe',
    label: 'Forward P/E',
    short: 'Fwd P/E',
    group: 'Valuation',
    unit: 'x',
    tier: 'quote',
    kind: 'none',
    w: 80,
  }),
  f({
    id: 'eps',
    label: 'EPS (TTM)',
    group: 'Valuation',
    unit: 'usd',
    tier: 'quote',
    kind: 'none',
    w: 80,
  }),
  f({
    id: 'priceBook',
    label: 'Price / book',
    short: 'P/B',
    group: 'Valuation',
    unit: 'x',
    tier: 'quote',
    kind: 'range',
    yahoo: 'pricebookratio.quarterly',
    w: 72,
  }),
  f({
    id: 'peg',
    label: 'PEG (5y expected)',
    short: 'PEG',
    group: 'Valuation',
    unit: 'x',
    tier: 'profile',
    kind: 'range',
    yahoo: 'pegratio_5y',
    w: 72,
  }),
  f({
    id: 'divYield',
    label: 'Dividend yield %',
    short: 'Div yield',
    group: 'Valuation',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    yahoo: 'forward_dividend_yield',
    w: 84,
  }),
  // ── fundamentals ──────────────────────────────────────────────────────────
  f({
    id: 'revGrowth',
    label: 'Revenue growth % (qtr YoY)',
    short: 'Rev growth',
    group: 'Fundamentals',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'quarterlyrevenuegrowth.quarterly',
    w: 92,
  }),
  f({
    id: 'epsGrowth',
    label: 'EPS growth % (TTM)',
    group: 'Fundamentals',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'epsgrowth.lasttwelvemonths',
    filterOnly: true,
  }),
  f({
    id: 'roe',
    label: 'Return on equity %',
    short: 'ROE',
    group: 'Fundamentals',
    unit: 'pct',
    tier: 'profile',
    kind: 'range',
    yahoo: 'returnonequity.lasttwelvemonths',
    w: 72,
  }),
  f({
    id: 'earningsDays',
    label: 'Upcoming earnings (days away)',
    short: 'Earnings in',
    group: 'Fundamentals',
    unit: 'days',
    tier: 'quote',
    kind: 'range',
    w: 90,
    hint: 'Days until the next report. Empty when Yahoo has no upcoming date.',
  }),
  // ── technicals ────────────────────────────────────────────────────────────
  f({
    id: 'from52High',
    label: 'From 52-week high %',
    short: 'From 52w high',
    group: 'Technicals',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 100,
    hint: '0 at the high, −50 = half off the high.',
  }),
  f({
    id: 'perf52w',
    label: '52-week change %',
    short: '52w chg',
    group: 'Technicals',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    yahoo: 'fiftytwowkpercentchange',
    w: 84,
  }),
  f({
    id: 'vs50d',
    label: 'Vs 50-day average %',
    short: 'vs 50D',
    group: 'Technicals',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 78,
  }),
  f({
    id: 'vs200d',
    label: 'Vs 200-day average %',
    short: 'vs 200D',
    group: 'Technicals',
    unit: 'pct',
    tier: 'quote',
    kind: 'range',
    w: 80,
  }),
  f({
    id: 'beta',
    label: 'Beta',
    group: 'Technicals',
    unit: 'num',
    tier: 'profile',
    kind: 'range',
    yahoo: 'beta',
    w: 64,
  }),
  // ── volume profile (untested POC of a recent session, first retest) ───────
  f({
    id: 'pocStage',
    label: 'POC retest stage',
    short: 'POC setup',
    group: 'Volume profile',
    unit: 'text',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'multi',
    options: POC_STAGES,
    w: 140,
    hint: "Where price stands against the untested point of control (POC) zone of one of the last 8 sessions. First touch = it moved ≥1% away (on any later day) and is now back at the zone's near edge for the first time. Pulling back = it has given back ≥30% of the move away.",
  }),
  f({
    id: 'pocEdgePct',
    label: 'Distance to POC entry edge %',
    short: 'To entry',
    group: 'Volume profile',
    unit: 'pct',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'range',
    w: 84,
    hint: 'Price vs the zone edge a trade enters at (top edge for a long, bottom edge for a short). 0 or below = in the zone.',
  }),
  f({
    id: 'pocDistPct',
    label: 'Distance from POC %',
    short: 'vs POC',
    group: 'Volume profile',
    unit: 'pct',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'range',
    w: 80,
  }),
  f({
    id: 'pocPrice',
    label: 'Untested POC',
    short: 'POC',
    group: 'Volume profile',
    unit: 'usd',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'none',
    w: 84,
    hint: "The point of control of a recent session that price has not come back to: the price with the most volume (the chart's session profile, 24 rows).",
  }),
  f({
    id: 'pocEntry',
    label: 'POC zone entry edge',
    short: 'Entry',
    group: 'Volume profile',
    unit: 'usd',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'none',
    w: 84,
  }),
  f({
    id: 'pocStop',
    label: 'POC zone stop (far edge)',
    short: 'Stop',
    group: 'Volume profile',
    unit: 'usd',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'none',
    w: 84,
  }),
  f({
    id: 'pocRiskPct',
    label: 'POC setup risk %',
    short: 'Risk %',
    group: 'Volume profile',
    unit: 'pct',
    tier: 'vprofile',
    scannerOnly: true,
    kind: 'range',
    w: 76,
    hint: 'Entry edge → stop (the zone height) as a % of the entry.',
  }),
  // ── profile ───────────────────────────────────────────────────────────────
  f({
    id: 'sector',
    label: 'Sector',
    group: 'Profile',
    unit: 'text',
    tier: 'profile',
    kind: 'multi',
    yahoo: 'sector',
    options: SECTORS,
    w: 150,
  }),
  f({
    id: 'industry',
    label: 'Industry',
    group: 'Profile',
    unit: 'text',
    tier: 'profile',
    kind: 'multi',
    yahoo: 'industry',
    options: INDUSTRIES,
    w: 190,
  }),
  f({
    id: 'listedDays',
    label: 'Days since listing',
    short: 'Listed (days)',
    group: 'Profile',
    unit: 'days',
    tier: 'quote',
    kind: 'range',
    w: 90,
    hint: 'Small = a recent IPO. Counted from Yahoo’s first-trade date.',
  }),
  f({
    id: 'analyst',
    label: 'Analyst rating',
    group: 'Profile',
    unit: 'text',
    tier: 'quote',
    kind: 'multi',
    options: ['Strong Buy', 'Buy', 'Hold', 'Underperform', 'Sell'],
    w: 110,
    hint: ANALYST_NOTE,
  }),
];

export const FIELD = new Map(FIELDS.map((d) => [d.id, d]));

export const GROUP_ORDER: readonly Group[] = [
  'Price & move',
  'Volume',
  'Supply',
  'Valuation',
  'Fundamentals',
  'Technicals',
  'Profile',
];

/** Every numeric/multi filter the user can add (filter-only fields included). */
export const FILTERABLE = FIELDS.filter(
  (d) => !d.scannerOnly && (d.kind === 'range' || d.kind === 'multi'),
);
export const COLUMNABLE = FIELDS.filter((d) => !d.filterOnly && !d.scannerOnly);

/** Table column sets: the "Custom ▾" menu in the screenshot. */
export const VIEWS: Record<string, readonly string[]> = {
  Momentum: ['price', 'changePct', 'volume', 'rvol', 'rvolTime', 'float', 'marketCap', 'gapPct'],
  Overview: ['price', 'changePct', 'volume', 'marketCap', 'pe', 'divYield', 'sector', 'analyst'],
  Technicals: [
    'price',
    'changePct',
    'gapPct',
    'fromOpenPct',
    'rangePct',
    'vs50d',
    'vs200d',
    'from52High',
    'perf52w',
  ],
  Valuation: ['price', 'marketCap', 'pe', 'forwardPe', 'eps', 'priceBook', 'peg', 'divYield'],
  Supply: ['price', 'float', 'sharesOut', 'marketCap', 'shortFloat', 'insiders', 'institutions'],
};
export const DEFAULT_COLUMNS: readonly string[] = VIEWS.Momentum!;
export const DEFAULT_SORT: SortState = { key: 'changePct', dir: 'desc' };

export const DEFAULT_UNIVERSE: FilterState = universeFor(MARKETS.us!);

export interface ScreenPreset {
  name: string;
  filters: FilterState;
  sort: SortState;
  columns: readonly string[];
}

const universe = DEFAULT_UNIVERSE;
export const PRESETS: readonly ScreenPreset[] = [
  {
    // The screen in the TradingView screenshot this feature was modelled on.
    name: '5 pillar',
    filters: {
      ...universe,
      price: { min: 2, max: 20 },
      changePct: { min: 10 },
      rvolTime: { min: 4 },
      float: { min: 1e6, max: 15e6 },
    },
    sort: DEFAULT_SORT,
    columns: VIEWS.Momentum!,
  },
  {
    name: 'Low-float runners',
    filters: {
      ...universe,
      price: { min: 1, max: 30 },
      changePct: { min: 5 },
      volume: { min: 500_000 },
      float: { max: 20e6 },
    },
    sort: DEFAULT_SORT,
    columns: VIEWS.Momentum!,
  },
  {
    name: 'Gap up',
    filters: {
      ...universe,
      price: { min: 1, max: 50 },
      gapPct: { min: 4 },
      volume: { min: 300_000 },
    },
    sort: { key: 'gapPct', dir: 'desc' },
    columns: [
      'price',
      'changePct',
      'gapPct',
      'fromOpenPct',
      'volume',
      'rvol',
      'float',
      'marketCap',
    ],
  },
  {
    name: 'High short interest',
    filters: { ...universe, price: { min: 1 }, shortFloat: { min: 20 }, volume: { min: 500_000 } },
    sort: { key: 'shortFloat', dir: 'desc' },
    columns: VIEWS.Supply!,
  },
  {
    name: 'Day gainers',
    filters: { ...universe, price: { min: 1 }, changePct: { min: 3 }, avgVolume: { min: 500_000 } },
    sort: DEFAULT_SORT,
    columns: VIEWS.Overview!,
  },
  {
    name: 'Recent IPOs',
    filters: { ...universe, price: { min: 1 }, listedDays: { max: 120 }, volume: { min: 200_000 } },
    sort: { key: 'volume', dir: 'desc' },
    columns: ['price', 'changePct', 'volume', 'listedDays', 'float', 'marketCap', 'from52High'],
  },
];

/** Keys of `ScreenerRow` that hold a number: what the table can sort and range-filter. */
export type NumericRowKey = {
  [K in keyof ScreenerRow]: ScreenerRow[K] extends number | null | undefined ? K : never;
}[keyof ScreenerRow];
