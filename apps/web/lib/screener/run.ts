import { FIELD } from './fields.ts';
import { marketOf } from './markets.ts';
import {
  postFilter,
  rowFromQuote,
  sortRows,
  tiersForFilters,
  tiersForView,
  yahooQuery,
  yahooSort,
} from './query.ts';
import type { Operand } from './query.ts';
import type { Profile, ScreenPage } from './yahoo.ts';
import type { FilterState, ScreenerRequest, ScreenerResponse, ScreenerRow } from './types.ts';

/** The three Yahoo calls the screen makes. Injected so the whole pipeline can be tested without a network. */
export interface Deps {
  screenPage: (
    query: Operand,
    sortField: string,
    sortType: 'ASC' | 'DESC',
    offset: number,
  ) => Promise<ScreenPage>;
  profiles: (symbols: readonly string[]) => Promise<Map<string, Profile>>;
  rvolsAtTime: (symbols: readonly string[], tz: string) => Promise<Map<string, number | null>>;
}

/** Yahoo's maximum page size. */
export const PAGE = 250;
/** How many pages of candidates one screen pulls from Yahoo (so at most 750 names enter the pipeline). */
export const MAX_PAGES = 3;
/** Names that get the per-name slow lookups (float, rel vol at time…). It is the request-time budget. */
export const ENRICH_CAP = 150;
export const DEFAULT_LIMIT = 100;
export const MAX_LIMIT = 250;

const PROFILE_KEYS = [
  'float',
  'sector',
  'industry',
  'shortFloat',
  'insiders',
  'institutions',
  'beta',
  'peg',
  'revGrowth',
  'roe',
] as const;

const label = (id: string) => FIELD.get(id)?.label ?? id;

interface Enriched {
  rows: ScreenerRow[];
  /** Names Yahoo did not answer for. They keep `undefined` and are reported, never silently dropped as "no data". */
  failed: number;
}

async function addProfile(rows: ScreenerRow[], deps: Deps): Promise<Enriched> {
  const got = await deps.profiles(rows.map((r) => r.symbol));
  let failed = 0;
  const out = rows.map((r) => {
    const p = got.get(r.symbol);
    if (!p) {
      failed++;
      return r;
    }
    const next: ScreenerRow = { ...r };
    for (const k of PROFILE_KEYS) (next as unknown as Record<string, unknown>)[k] = p[k];
    // Yahoo has no float for many small caps. Shares outstanding is the nearest figure it does have, and
    // float can never exceed it: a "float under X" screen stays safe, and the row says it is an estimate.
    if (next.float == null && r.sharesOut != null) {
      next.float = r.sharesOut;
      next.floatEstimated = true;
    }
    return next;
  });
  return { rows: out, failed };
}

async function addRvolTime(rows: ScreenerRow[], deps: Deps, tz: string): Promise<Enriched> {
  const got = await deps.rvolsAtTime(
    rows.map((r) => r.symbol),
    tz,
  );
  let failed = 0;
  const out = rows.map((r) => {
    if (!got.has(r.symbol)) {
      failed++;
      return r;
    }
    return { ...r, rvolTime: got.get(r.symbol) ?? null };
  });
  return { rows: out, failed };
}

export async function runScreen(
  req: ScreenerRequest,
  deps: Deps,
  nowMs = Date.now(),
): Promise<ScreenerResponse> {
  const notes: string[] = [];
  const filters: FilterState = req.filters;
  const limit = Math.min(MAX_LIMIT, Math.max(1, req.limit ?? DEFAULT_LIMIT));

  // 1. Yahoo narrows the universe with every filter it can run itself.
  const market = marketOf(req.market);
  const query = yahooQuery(filters, nowMs, market);
  const { field, type } = yahooSort(req.sort);
  const first = await deps.screenPage(query, field, type, 0);
  const pages = [first];
  const wanted = Math.min(MAX_PAGES, Math.ceil(first.total / PAGE));
  if (first.quotes.length >= PAGE && wanted > 1) {
    const more = await Promise.all(
      Array.from({ length: wanted - 1 }, (_, i) =>
        deps.screenPage(query, field, type, (i + 1) * PAGE),
      ),
    );
    pages.push(...more);
  }
  const seen = new Set<string>();
  const quotes = pages
    .flatMap((p) => p.quotes)
    .filter((q) => {
      const s = q.symbol?.toUpperCase();
      if (!s || seen.has(s)) return false;
      seen.add(s);
      return true;
    });
  if (first.total > quotes.length) {
    notes.push(
      `Yahoo matched ${first.total.toLocaleString('en-US')} names; the first ${quotes.length.toLocaleString('en-US')} (by ${label(req.sort.key).toLowerCase()}) were screened. Add filters to see the rest.`,
    );
  }

  let rows = quotes
    .map((q) => rowFromQuote(q, nowMs, market))
    .filter((r): r is ScreenerRow => r !== null);
  const missing: Record<string, number> = {};
  const addMissing = (m: Record<string, number>) => {
    for (const [id, n] of Object.entries(m)) missing[id] = (missing[id] ?? 0) + n;
  };

  // 2. Filters that only need the quote.
  const q1 = postFilter(rows, filters, new Set(['quote']));
  rows = q1.rows;
  addMissing(q1.missing);

  // 3. Filters that need a per-name lookup, cheapest first. Only names that survived so far are looked up.
  const filterTiers = tiersForFilters(filters);
  const enriched = new Set<string>();
  const stage = async (tier: 'profile' | 'intraday') => {
    if (rows.length > ENRICH_CAP) {
      notes.push(
        `${label(tier === 'profile' ? 'float' : 'rvolTime')} needs a lookup per name: only the first ${ENRICH_CAP} of ${rows.length} matches were checked. Tighten the other filters to cover them all.`,
      );
      rows = rows.slice(0, ENRICH_CAP);
    }
    const e = await (tier === 'profile'
      ? addProfile(rows, deps)
      : addRvolTime(rows, deps, market.tz));
    if (e.failed)
      notes.push(
        `Yahoo did not answer the ${tier} lookup for ${e.failed} name(s): they are left out.`,
      );
    const est = e.rows.filter((r) => r.floatEstimated).length;
    if (est)
      notes.push(
        `${est} name(s) have no float on Yahoo: shares outstanding (≈) is used instead. The true float is lower, so “float under X” is safe but “float over X” can over-include.`,
      );
    enriched.add(tier);
    const f = postFilter(e.rows, filters, new Set([tier]));
    rows = f.rows;
    addMissing(f.missing);
  };
  if (filterTiers.has('profile')) await stage('profile');
  if (filterTiers.has('intraday')) await stage('intraday');

  for (const [id, n] of Object.entries(missing)) {
    if (n > 0)
      notes.push(`${n} name(s) had no ${label(id)} on Yahoo and were left out by that filter.`);
  }

  // 4. Columns / sort that need data the filters did not already fetch.
  const viewTiers = tiersForView(req.columns, req.sort);
  const late = ([...viewTiers] as string[]).filter((t) => !enriched.has(t));
  if (late.length) {
    const head = rows.slice(0, ENRICH_CAP);
    if (rows.length > ENRICH_CAP) {
      notes.push(
        `${late.join(' and ')} data was loaded for the first ${ENRICH_CAP} of ${rows.length} matches.`,
      );
    }
    let work = head;
    if (late.includes('profile')) work = (await addProfile(work, deps)).rows;
    if (late.includes('intraday')) work = (await addRvolTime(work, deps, market.tz)).rows;
    rows = [...work, ...rows.slice(ENRICH_CAP)];
  }

  const total = rows.length;
  const sorted = sortRows(rows, req.sort).slice(0, limit);
  return { rows: sorted, total, asOf: new Date(nowMs).toISOString(), notes };
}
