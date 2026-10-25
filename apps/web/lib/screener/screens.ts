import { DEFAULT_COLUMNS, DEFAULT_SORT, FIELDS, PRESETS } from './fields.ts';
import { DEFAULT_MARKET, MONEY_FILTERS, marketOf, universeFor } from './markets.ts';
import { isActive } from './query.ts';
import type { FilterState, FilterValue, RangeFilter, SortState } from './types.ts';

/** A saved screen: the filters, the table's sort and columns, and which filter pills sit in the bar. */
export interface Screen {
  id: string;
  /** Market id (see `MARKETS`). Screens saved before markets existed have none: that means USA. */
  market: string;
  name: string;
  filters: FilterState;
  sort: SortState;
  columns: string[];
  pills: string[];
}

/** Pills always on the bar (inactive ones are an invitation to filter): what a momentum screen reaches for first. */
export const QUICK_PILLS: readonly string[] = [
  'price',
  'changePct',
  'volume',
  'rvolTime',
  'rvol',
  'float',
  'marketCap',
  'gapPct',
  'shortFloat',
  'sector',
  'pe',
  'from52High',
];

const order = new Map(FIELDS.map((d, i) => [d.id, i]));

/** Quick pills, then any other active filter, so a loaded screen never hides one of its own filters. */
export function pillsFor(filters: FilterState, current: readonly string[] = QUICK_PILLS): string[] {
  const out = [...current];
  for (const d of FIELDS) {
    if (isActive(filters[d.id]) && !out.includes(d.id)) out.push(d.id);
  }
  return out;
}

export const newId = () => Math.random().toString(36).slice(2, 10);

export function seedScreens(): Screen[] {
  return PRESETS.map((p) => ({
    market: DEFAULT_MARKET,
    id: `preset-${p.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`,
    name: p.name,
    filters: structuredClone(p.filters),
    sort: { ...p.sort },
    columns: [...p.columns],
    pills: pillsFor(p.filters),
  }));
}

export function blankScreen(name: string, market = DEFAULT_MARKET): Screen {
  return {
    id: newId(),
    market,
    name,
    filters: universeFor(marketOf(market)),
    sort: { ...DEFAULT_SORT },
    columns: [...DEFAULT_COLUMNS],
    pills: [...QUICK_PILLS],
  };
}

/** Sets or clears one filter. An empty range / empty selection removes the key, so "no filter" is always "absent". */
export function withFilter(
  filters: FilterState,
  id: string,
  value: FilterValue | null,
): FilterState {
  const next = { ...filters };
  if (value && isActive(value)) next[id] = value;
  else delete next[id];
  return next;
}

export function withRange(filters: FilterState, id: string, r: RangeFilter): FilterState {
  const clean: RangeFilter = {};
  if (r.min != null) clean.min = r.min;
  if (r.max != null) clean.max = r.max;
  return withFilter(filters, id, clean);
}

export function toggleIn<T>(list: readonly T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

/** Adds a pill next to the others, in the registry's order, so the bar groups price/volume/supply like the menu. */
export function withPill(pills: readonly string[], id: string): string[] {
  if (pills.includes(id)) return [...pills];
  const rank = order.get(id) ?? 0;
  const at = pills.findIndex((p) => (order.get(p) ?? 0) > rank);
  const out = [...pills];
  out.splice(at === -1 ? out.length : at, 0, id);
  return out;
}

export function sameFilters(a: FilterState, b: FilterState): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Undo/redo over the filters: every edit pushes; going back and editing drops the redo branch. */
export interface History<T> {
  past: T[];
  present: T;
  future: T[];
}
export const startHistory = <T>(present: T): History<T> => ({ past: [], present, future: [] });
export function pushHistory<T>(
  h: History<T>,
  next: T,
  same: (a: T, b: T) => boolean,
  cap = 50,
): History<T> {
  if (same(h.present, next)) return h;
  return { past: [...h.past, h.present].slice(-cap), present: next, future: [] };
}
export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  return {
    past: h.past.slice(0, -1),
    present: h.past[h.past.length - 1]!,
    future: [h.present, ...h.future],
  };
}
export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0]!, future: h.future.slice(1) };
}

/**
 * The filters after switching market: the new market's own universe (its exchanges), every filter that is not
 * a currency amount carried over, and the currency amounts (price, market cap, $ volume) dropped. "Price 2 to 20"
 * means something very different in rupees, so keeping it would silently return the wrong names.
 */
export function filtersForMarket(filters: FilterState, marketId: string): FilterState {
  const next = universeFor(marketOf(marketId));
  for (const [id, v] of Object.entries(filters)) {
    if (id === 'exchange' || id === 'commonOnly' || MONEY_FILTERS.includes(id)) continue;
    next[id] = v;
  }
  return next;
}
