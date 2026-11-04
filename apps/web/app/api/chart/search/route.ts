import { NextResponse } from 'next/server';
import { aliasHits } from '../../../../lib/tv/aliases';
import { rankHits, searchQueries } from '../../../../lib/tv/search-rank';
import type { SearchHit } from '../../../../lib/tv/types';
import { futureContracts } from '../../../../lib/broker/upstox-instruments';

export const dynamic = 'force-dynamic';

interface YahooQuote {
  symbol?: string;
  shortname?: string;
  longname?: string;
  exchDisp?: string;
  quoteType?: string;
}

/** One Yahoo search; tries the second host if the first fails. Throws only if both do. */
async function yahooSearch(q: string): Promise<SearchHit[]> {
  let last: unknown;
  for (const host of ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']) {
    try {
      const res = await fetch(
        `https://${host}/v1/finance/search?q=${encodeURIComponent(q)}&quotesCount=12&newsCount=0`,
        { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' },
      );
      const body = (await res.json()) as { quotes?: YahooQuote[] };
      return (body.quotes ?? [])
        .filter((x) => x.symbol)
        .map((x) => ({
          symbol: x.symbol!,
          name: x.longname ?? x.shortname ?? x.symbol!,
          exchange: x.exchDisp ?? '',
          type: x.quoteType ?? '',
        }));
    } catch (e) {
      last = e;
    }
  }
  throw last;
}

/** NSE futures whose underlying or name starts with / contains the query; each underlying's front month first, then later expiries. */
async function futuresHits(q: string): Promise<SearchHit[]> {
  const needle = q.toUpperCase().replace(/\s+/g, ' ').replace(/ ?(FUT|FUTURES?)$/, '').trim();
  if (needle.length < 2) return [];
  const all = await futureContracts();
  const seen = new Set<string>();
  const out: SearchHit[] = [];
  const matches = all.filter((c) => c.underlying.startsWith(needle) || c.name.toUpperCase().includes(needle));
  matches.sort((a, b) => Number(b.underlying === needle) - Number(a.underlying === needle) || a.expiry - b.expiry);
  for (const c of matches) {
    if (!seen.has(c.underlying)) {
      seen.add(c.underlying);
      out.push({
        symbol: `${c.underlying}1!.NFO`,
        display: `${c.underlying}1!`,
        name: `${c.name} Futures — front month (lot ${c.lotSize})`,
        exchange: 'NSE',
        type: 'futures',
      });
    }
    if (out.length >= 14) break;
  }
  const top = matches[0]?.underlying;
  if (top) {
    for (const c of matches.filter((m) => m.underlying === top)) {
      out.push({
        symbol: c.symbol,
        display: c.symbol.replace(/\.NFO$/, ''),
        name: `${c.name} Futures — exp ${new Date(c.expiry + 19_800_000).toISOString().slice(0, 10)}`,
        exchange: 'NSE',
        type: 'futures',
      });
    }
  }
  return out;
}

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get('q') ?? '').trim().slice(0, 40);
  if (!q) return NextResponse.json({ hits: [] satisfies SearchHit[] });
  const alias = aliasHits(q);
  const futures = await futuresHits(q).catch(() => [] as SearchHit[]);
  // Yahoo matches words against names, so "gold comex" finds nothing; ask for the phrase and
  // for each word, then rank by how many words each result matches.
  const settled = await Promise.allSettled(searchQueries(q).map(yahooSearch));
  const lists = settled.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []));
  if (!lists.length && !alias.length && !futures.length) {
    return NextResponse.json({ hits: [], error: 'search unavailable' }, { status: 502 });
  }
  const ranked = rankHits(q, lists).filter((h) => !alias.some((a) => a.symbol === h.symbol));
  return NextResponse.json({ hits: [...alias, ...ranked.slice(0, 6), ...futures, ...ranked.slice(6)] });
}
