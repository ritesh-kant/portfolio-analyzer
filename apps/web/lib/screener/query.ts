import { FIELD, FIELDS, type FieldDef, type Tier } from './fields.ts';
import { MARKETS, marketOf, venueOf, type Market } from './markets.ts';
import type {
  FilterState,
  FilterValue,
  MultiFilter,
  RangeFilter,
  ScreenerRow,
  SortState,
  ToggleFilter,
} from './types.ts';

export interface Operand {
  operator: string;
  operands: Array<string | number | Operand>;
}

const eq = (field: string, value: string): Operand => ({
  operator: 'eq',
  operands: [field, value],
});
const anyOf = (field: string, values: string[]): Operand =>
  values.length === 1
    ? eq(field, values[0]!)
    : { operator: 'or', operands: values.map((v) => eq(field, v)) };

export const isRange = (v: FilterValue | undefined): v is RangeFilter =>
  !!v && ('min' in v || 'max' in v);
export const isMulti = (v: FilterValue | undefined): v is MultiFilter => !!v && 'in' in v;
export const isToggle = (v: FilterValue | undefined): v is ToggleFilter => !!v && 'on' in v;

/** A filter with no bound / no selection is not filtering: the UI may leave one behind while it is being edited. */
export function isActive(v: FilterValue | undefined): boolean {
  if (!v) return false;
  if (isRange(v)) return v.min != null || v.max != null;
  if (isMulti(v)) return v.in.length > 0;
  return v.on;
}

const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);

/**
 * The Yahoo screener query for every filter Yahoo can run itself (they cut the pool BEFORE anything is
 * downloaded), plus the universe: US, and the chosen exchanges.
 */
export function yahooQuery(
  filters: FilterState,
  nowMs = Date.now(),
  market: Market = MARKETS.us!,
): Operand {
  const ops: Operand[] = [eq('region', market.region)];

  const ex = filters.exchange;
  if (isMulti(ex) && ex.in.length) {
    const codes = ex.in.flatMap((venue) => market.venues[venue] ?? []);
    if (codes.length) ops.push(anyOf('exchange', codes));
  }

  for (const def of FIELDS) {
    const v = filters[def.id];
    if (!def.yahoo || !isActive(v)) continue;
    if (isRange(v)) {
      if (v.min != null && v.max != null)
        ops.push({ operator: 'btwn', operands: [def.yahoo, v.min, v.max] });
      else if (v.min != null) ops.push({ operator: 'gte', operands: [def.yahoo, v.min] });
      else if (v.max != null) ops.push({ operator: 'lte', operands: [def.yahoo, v.max] });
    } else if (isMulti(v)) {
      ops.push(anyOf(def.yahoo, v.in));
    }
  }

  // Listing age has no Yahoo operand of its own: a listing DATE bound narrows the pool, and the exact
  // day-count is re-checked on the rows (see `postFilter`).
  const listed = filters.listedDays;
  if (isRange(listed)) {
    if (listed.max != null)
      ops.push({
        operator: 'gte',
        operands: ['new_listing_date', isoDay(nowMs - listed.max * 864e5)],
      });
    if (listed.min != null)
      ops.push({
        operator: 'lte',
        operands: ['new_listing_date', isoDay(nowMs - listed.min * 864e5)],
      });
  }

  return { operator: 'and', operands: ops };
}

/** Which Yahoo field to order the candidate pool by, so a truncated pool keeps the names the user cares about. */
export function yahooSort(sort: SortState): { field: string; type: 'ASC' | 'DESC' } {
  const field = FIELD.get(sort.key)?.sortYahoo;
  if (field) return { field, type: sort.dir === 'asc' ? 'ASC' : 'DESC' };
  // Anything we compute ourselves cannot be ordered by Yahoo: take the biggest movers, the usual suspects.
  return { field: 'percentchange', type: 'DESC' };
}

// ── quote → row ─────────────────────────────────────────────────────────────

/** The slice of Yahoo's screener quote this screener reads. Every key may be missing. */
export interface YahooQuote {
  symbol?: string;
  shortName?: string;
  longName?: string;
  displayName?: string;
  exchange?: string;
  regularMarketPrice?: number;
  regularMarketChange?: number;
  regularMarketChangePercent?: number;
  regularMarketVolume?: number;
  regularMarketOpen?: number;
  regularMarketDayHigh?: number;
  regularMarketDayLow?: number;
  regularMarketPreviousClose?: number;
  averageDailyVolume3Month?: number;
  sharesOutstanding?: number;
  impliedSharesOutstanding?: number;
  marketCap?: number;
  fiftyTwoWeekHigh?: number;
  fiftyTwoWeekChangePercent?: number;
  fiftyDayAverageChangePercent?: number;
  twoHundredDayAverageChangePercent?: number;
  trailingPE?: number;
  forwardPE?: number;
  epsTrailingTwelveMonths?: number;
  dividendYield?: number;
  priceToBook?: number;
  averageAnalystRating?: string;
  firstTradeDateMilliseconds?: number;
  earningsTimestamp?: number;
}

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);
/** a ÷ b − 1 as a percent, or null when either side is missing / b is 0. */
const pctOver = (a: number | null, b: number | null): number | null =>
  a != null && b != null && b !== 0 ? (a / b - 1) * 100 : null;

export function rowFromQuote(
  q: YahooQuote,
  nowMs = Date.now(),
  market: Market = MARKETS.us!,
): ScreenerRow | null {
  const symbol = q.symbol?.toUpperCase();
  const price = num(q.regularMarketPrice);
  if (!symbol || price == null) return null;

  const volume = num(q.regularMarketVolume);
  const avgVolume = num(q.averageDailyVolume3Month);
  const open = num(q.regularMarketOpen);
  const high = num(q.regularMarketDayHigh);
  const low = num(q.regularMarketDayLow);
  const first = num(q.firstTradeDateMilliseconds);
  const earnings = num(q.earningsTimestamp);
  const ma50 = num(q.fiftyDayAverageChangePercent);
  const ma200 = num(q.twoHundredDayAverageChangePercent);
  const rating = q.averageAnalystRating;

  return {
    symbol,
    name: q.longName ?? q.shortName ?? q.displayName ?? symbol,
    exchange: venueOf(q.exchange, market),
    price,
    change: num(q.regularMarketChange),
    changePct: num(q.regularMarketChangePercent),
    volume,
    avgVolume,
    rvol: volume != null && avgVolume ? volume / avgVolume : null,
    dollarVol: volume != null ? volume * price : null,
    sharesOut: num(q.sharesOutstanding) ?? num(q.impliedSharesOutstanding),
    marketCap: num(q.marketCap),
    gapPct: pctOver(open, num(q.regularMarketPreviousClose)),
    fromOpenPct: pctOver(price, open),
    rangePct: low != null && low > 0 ? pctOver(high, low) : null,
    from52High: pctOver(price, num(q.fiftyTwoWeekHigh)),
    perf52w: num(q.fiftyTwoWeekChangePercent),
    // Yahoo reports the distance from a moving average as a fraction (1.1 = 110%): the table speaks percent.
    vs50d: ma50 != null ? ma50 * 100 : null,
    vs200d: ma200 != null ? ma200 * 100 : null,
    pe: num(q.trailingPE),
    forwardPe: num(q.forwardPE),
    eps: num(q.epsTrailingTwelveMonths),
    divYield: num(q.dividendYield),
    priceBook: num(q.priceToBook),
    analyst: rating ? (rating.split(' - ')[1] ?? rating) : null,
    listedDays: first != null ? Math.floor((nowMs - first) / 864e5) : null,
    earningsDays:
      earnings != null && earnings * 1000 > nowMs
        ? Math.ceil((earnings * 1000 - nowMs) / 864e5)
        : null,
  };
}

// ── common stock only ───────────────────────────────────────────────────────

const NOT_COMMON_NAME =
  /\b(warrants?|rights?|units?|preferred|depositary|notes? due|subordinated)\b/i;
const NOT_COMMON_SYMBOL = /[-.](W|WS|WT|WI|R|RT|U|UN|P[A-Z]?)$/i;

/** Warrants, rights, units and preferred shares all pass Yahoo's EQUITY type but are not what a momentum screen means. */
export function isCommonStock(row: Pick<ScreenerRow, 'symbol' | 'name'>): boolean {
  return !NOT_COMMON_NAME.test(row.name) && !NOT_COMMON_SYMBOL.test(row.symbol);
}

// ── post-filtering ──────────────────────────────────────────────────────────

/** Filters Yahoo cannot run, so the rows are checked here. Listing age is also re-checked: Yahoo only narrows it by date. */
const isPost = (d: FieldDef) =>
  (!d.yahoo || d.id === 'listedDays') && (d.kind === 'range' || d.kind === 'multi');

export function activePostFields(filters: FilterState): FieldDef[] {
  return FIELDS.filter((d) => isPost(d) && isActive(filters[d.id]));
}

/** The slow data the active filters need (profile / intraday) before they can be applied. */
export function tiersForFilters(filters: FilterState): Set<Tier> {
  return new Set(
    activePostFields(filters)
      .map((d) => d.tier)
      .filter((t) => t !== 'quote'),
  );
}

/** The slow data the visible columns and the sort key need. */
export function tiersForView(columns: readonly string[], sort: SortState): Set<Tier> {
  const out = new Set<Tier>();
  for (const id of [...columns, sort.key]) {
    const t = FIELD.get(id)?.tier;
    if (t && t !== 'quote') out.add(t);
  }
  return out;
}

const valueOf = (row: ScreenerRow, id: string): number | string | null | undefined =>
  (row as unknown as Record<string, number | string | null | undefined>)[id];

export interface PostFilterResult {
  rows: ScreenerRow[];
  /** Per field: how many names were dropped only because Yahoo had no value for them. */
  missing: Record<string, number>;
}

/** Applies the post-filters whose data tier is in `tiers`. A name with no value for an active filter cannot pass it. */
export function postFilter(
  rows: ScreenerRow[],
  filters: FilterState,
  tiers: ReadonlySet<Tier>,
): PostFilterResult {
  const fields = activePostFields(filters).filter((d) => tiers.has(d.tier));
  const missing: Record<string, number> = {};
  const common = filters.commonOnly;
  const out = rows.filter((row) => {
    if (isToggle(common) && common.on && tiers.has('quote') && !isCommonStock(row)) return false;
    for (const d of fields) {
      const want = filters[d.id]!;
      const have = valueOf(row, d.id);
      if (have == null) {
        missing[d.id] = (missing[d.id] ?? 0) + 1;
        return false;
      }
      if (isRange(want) && typeof have === 'number') {
        if (want.min != null && have < want.min) return false;
        if (want.max != null && have > want.max) return false;
      } else if (isMulti(want) && !want.in.includes(String(have))) return false;
    }
    return true;
  });
  return { rows: out, missing };
}

// ── sorting ─────────────────────────────────────────────────────────────────

/** Sorts a copy by the chosen column; names with no value go last in either direction. */
export function sortRows(rows: readonly ScreenerRow[], sort: SortState): ScreenerRow[] {
  const dir = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = valueOf(a, sort.key);
    const y = valueOf(b, sort.key);
    if (x == null && y == null) return a.symbol.localeCompare(b.symbol);
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === 'number' && typeof y === 'number')
      return (x - y) * dir || a.symbol.localeCompare(b.symbol);
    return String(x).localeCompare(String(y)) * dir;
  });
}

// ── request hygiene (server side) ───────────────────────────────────────────

/** Drops anything in a client-sent filter map that is not a known field or a well-formed value. */
export function sanitizeFilters(raw: unknown, marketId?: string): FilterState {
  const market = marketOf(marketId);
  const out: FilterState = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [id, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== 'object') continue;
    if (id === 'commonOnly') {
      if (typeof (v as ToggleFilter).on === 'boolean') out[id] = { on: (v as ToggleFilter).on };
      continue;
    }
    if (id === 'exchange') {
      const list = (v as MultiFilter).in;
      if (Array.isArray(list))
        out[id] = {
          in: list.filter((x): x is string => typeof x === 'string' && x in market.venues),
        };
      continue;
    }
    const def = FIELD.get(id);
    if (!def) continue;
    if (def.kind === 'range') {
      const { min, max } = v as RangeFilter;
      const r: RangeFilter = {};
      if (typeof min === 'number' && Number.isFinite(min)) r.min = min;
      if (typeof max === 'number' && Number.isFinite(max)) r.max = max;
      if (isActive(r)) out[id] = r;
    } else if (def.kind === 'multi') {
      const list = (v as MultiFilter).in;
      if (Array.isArray(list)) {
        const allowed = new Set(def.options ?? []);
        const keep = list.filter((x): x is string => typeof x === 'string' && allowed.has(x));
        if (keep.length) out[id] = { in: keep };
      }
    }
  }
  return out;
}
