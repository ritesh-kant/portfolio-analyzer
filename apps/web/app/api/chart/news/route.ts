import { NextResponse } from 'next/server';
import { edgarFilings, nseFilings } from '../../../../lib/tv/filings';
import type { NewsResponse } from '../../../../lib/tv/news-types';

export const dynamic = 'force-dynamic';

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL = /^[A-Za-z0-9.^=\-_&]{1,24}$/;

const none = (reason: string): NewsResponse => ({ source: 'none', supported: false, reason, items: [] });

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
      return NextResponse.json({ source: 'nse_filings', supported: true, items } satisfies NewsResponse);
    }
    if (/^\^|=|-USD$|\.[A-Z]{1,3}$/.test(symbol)) {
      return NextResponse.json(none('Company filings exist only for NSE (.NS) and US-listed symbols.'));
    }
    const ua = process.env.SEC_USER_AGENT;
    if (!ua) {
      return NextResponse.json(
        none('US filings need SEC_USER_AGENT (a contact email) in apps/web/.env.local — SEC refuses requests without one.'),
      );
    }
    const items = await edgarFilings(symbol, from, to, ua);
    return NextResponse.json({ source: 'sec_edgar', supported: true, items } satisfies NewsResponse);
  } catch (e) {
    return NextResponse.json(none(e instanceof Error ? e.message : 'news fetch failed'));
  }
}
