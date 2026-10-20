import type { SearchHit } from './types';

/** Nicknames traders type that Yahoo spells out in its exchange names. */
const EXCHANGE_WORDS: Record<string, string[]> = {
  comex: ['commodity', 'new york commodity exchange'],
  nymex: ['nymex', 'mercantile'],
  cme: ['chicago mercantile', 'cme'],
  nasdaq: ['nasdaq', 'nasdaqgs', 'nasdaqcm', 'nasdaqgm'],
  nyse: ['nyse', 'new york stock exchange'],
  nse: ['nse', 'nsi'],
  bse: ['bse', 'bombay'],
  lse: ['london'],
  futures: ['future'],
  crypto: ['cryptocurrency', 'ccc'],
  fx: ['currency', 'ccy'],
  forex: ['currency', 'ccy'],
};

const words = (q: string) =>
  q
    .toLowerCase()
    .split(/[\s,/:]+/)
    .filter(Boolean);

/** How many of the query's words a hit matches (name, symbol, exchange, type). */
export function matchScore(query: string, hit: SearchHit): number {
  const hay = `${hit.symbol} ${hit.name} ${hit.exchange} ${hit.type}`.toLowerCase();
  return words(query).filter(
    (w) => hay.includes(w) || (EXCHANGE_WORDS[w] ?? []).some((alt) => hay.includes(alt)),
  ).length;
}

/** The queries to send upstream: the whole phrase first, then each word on its own. */
export function searchQueries(query: string): string[] {
  const ws = words(query);
  return ws.length > 1 ? [query.trim(), ...ws.slice(0, 3)] : [query.trim()];
}

/**
 * Merge hit lists, dropping repeats. With a multi-word query, hits matching more
 * words come first, and hits matching none of the extra words stay (ranked last)
 * rather than vanishing, so "gold comex" still finds gold on COMEX at the top.
 */
export function rankHits(query: string, lists: SearchHit[][]): SearchHit[] {
  const seen = new Set<string>();
  const merged: { hit: SearchHit; order: number }[] = [];
  lists.flat().forEach((hit) => {
    if (seen.has(hit.symbol)) return;
    seen.add(hit.symbol);
    merged.push({ hit, order: merged.length });
  });
  return merged
    .sort((a, b) => matchScore(query, b.hit) - matchScore(query, a.hit) || a.order - b.order)
    .map((m) => m.hit);
}
