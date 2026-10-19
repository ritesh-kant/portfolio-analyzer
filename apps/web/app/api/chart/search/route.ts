import { NextResponse } from 'next/server';
import type { SearchHit } from '../../../../lib/tv/types';

export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get('q') ?? '').trim().slice(0, 40);
  if (!q) return NextResponse.json({ hits: [] satisfies SearchHit[] });
  for (const host of ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']) {
    try {
      const res = await fetch(
        `https://${host}/v1/finance/search?q=${encodeURIComponent(q)}&quotesCount=12&newsCount=0`,
        { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' },
      );
      const body = (await res.json()) as {
        quotes?: Array<{ symbol?: string; shortname?: string; longname?: string; exchDisp?: string; quoteType?: string }>;
      };
      const hits: SearchHit[] = (body.quotes ?? [])
        .filter((x) => x.symbol)
        .map((x) => ({
          symbol: x.symbol!,
          name: x.longname ?? x.shortname ?? x.symbol!,
          exchange: x.exchDisp ?? '',
          type: x.quoteType ?? '',
        }));
      return NextResponse.json({ hits });
    } catch {
      // try the next host
    }
  }
  return NextResponse.json({ hits: [], error: 'search unavailable' }, { status: 502 });
}
