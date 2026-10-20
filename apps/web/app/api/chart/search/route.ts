import { NextResponse } from 'next/server';
import { aliasHits } from '../../../../lib/tv/aliases';
import { rankHits, searchQueries } from '../../../../lib/tv/search-rank';
import type { SearchHit } from '../../../../lib/tv/types';

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

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get('q') ?? '').trim().slice(0, 40);
  if (!q) return NextResponse.json({ hits: [] satisfies SearchHit[] });
  const alias = aliasHits(q);
  // Yahoo matches words against names, so "gold comex" finds nothing; ask for the phrase and
  // for each word, then rank by how many words each result matches.
  const settled = await Promise.allSettled(searchQueries(q).map(yahooSearch));
  const lists = settled.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []));
  if (!lists.length && !alias.length) {
    return NextResponse.json({ hits: [], error: 'search unavailable' }, { status: 502 });
  }
  const ranked = rankHits(q, lists).filter((h) => !alias.some((a) => a.symbol === h.symbol));
  return NextResponse.json({ hits: [...alias, ...ranked] });
}
