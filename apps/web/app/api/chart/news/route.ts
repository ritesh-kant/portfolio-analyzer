import { NextResponse } from 'next/server';
import { edgarFilings, nseFilings } from '../../../../lib/tv/filings';
import { blsReleases, cotReports, fedReleases, metalRoot } from '../../../../lib/tv/macro';
import type { NewsResponse } from '../../../../lib/tv/news-types';

export const dynamic = 'force-dynamic';

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL = /^[A-Za-z0-9.^=\-_&]{1,24}$/;

const none = (reason: string): NewsResponse => ({
  source: 'none',
  supported: false,
  reason,
  items: [],
});

/** Company filings for a chart symbol over `from..to` (YYYY-MM-DD). `.NS` → NSE announcements; plain tickers → SEC EDGAR. */
export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const symbol = (sp.get('symbol') ?? '').trim().toUpperCase();
  const from = sp.get('from') ?? '';
  const to = sp.get('to') ?? '';
  if (!SYMBOL.test(symbol) || !DATE.test(from) || !DATE.test(to)) {
    return NextResponse.json({ error: 'bad symbol or dates' }, { status: 400 });
  }
  try {
    if (symbol.endsWith('.NS')) {
      const items = await nseFilings(symbol.slice(0, -3), from, to);
      return NextResponse.json({
        source: 'nse_filings',
        supported: true,
        items,
      } satisfies NewsResponse);
    }
    const metal = metalRoot(symbol);
    if (metal) {
      // Metals have no company filings: use the Fed, the BLS release calendar and CFTC positioning.
      const settled = await Promise.allSettled([fedReleases(), blsReleases(), cotReports(metal)]);
      const lo = Date.parse(`${from}T00:00:00Z`);
      const hi = Math.min(Date.parse(`${to}T00:00:00Z`) + 86_400_000, Date.now());
      const items = settled
        .flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
        .filter((i) => {
          const t = Date.parse(i.published_at);
          return t >= lo && t <= hi;
        })
        .sort((a, b) => a.published_at.localeCompare(b.published_at));
      if (!items.length && settled.every((r) => r.status === 'rejected')) {
        return NextResponse.json(none('Fed, BLS and CFTC could not be reached.'));
      }
      return NextResponse.json({ source: 'macro', supported: true, items } satisfies NewsResponse);
    }
    if (/^\^|=|-USD$|\.[A-Z]{1,3}$/.test(symbol)) {
      return NextResponse.json(
        none(
          'News exists for NSE (.NS) and US stocks (company filings) and for gold, silver, platinum and palladium futures (Fed, BLS, CFTC).',
        ),
      );
    }
    const ua = process.env.SEC_USER_AGENT;
    if (!ua) {
      return NextResponse.json(
        none(
          'US filings need SEC_USER_AGENT (a contact email) in apps/web/.env.local — SEC refuses requests without one.',
        ),
      );
    }
    const items = await edgarFilings(symbol, from, to, ua);
    return NextResponse.json({
      source: 'sec_edgar',
      supported: true,
      items,
    } satisfies NewsResponse);
  } catch (e) {
    return NextResponse.json(none(e instanceof Error ? e.message : 'news fetch failed'));
  }
}
