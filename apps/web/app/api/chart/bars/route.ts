import { NextResponse } from 'next/server';
import type { Bar, BarsResponse } from '../../../../lib/tv/types';

export const dynamic = 'force-dynamic';

const HOSTS = ['query1.finance.yahoo.com', 'query2.finance.yahoo.com'];
const INTERVALS = new Set(['1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo']);
const RANGES = new Set(['1d', '5d', '7d', '1mo', '3mo', '6mo', '60d', '1y', '2y', '5y', '10y', 'ytd', 'max']);
const SYMBOL = /^[A-Za-z0-9.^=\-_&]{1,24}$/;

interface YahooResult {
  meta: Record<string, unknown>;
  timestamp?: number[];
  indicators: { quote: Array<Record<string, Array<number | null>>> };
}

async function fetchYahoo(symbol: string, interval: string, range: string): Promise<YahooResult> {
  let lastError = 'upstream unavailable';
  for (const host of HOSTS) {
    const url = `https://${host}/v8/finance/chart/${encodeURIComponent(symbol)}?interval=${interval}&range=${range}&includePrePost=false`;
    try {
      const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' });
      const body = (await res.json()) as {
        chart: { result: YahooResult[] | null; error: { description?: string } | null };
      };
      const result = body.chart.result?.[0];
      if (result) return result;
      lastError = body.chart.error?.description ?? lastError;
      if (res.status === 404) break;
    } catch (e) {
      lastError = e instanceof Error ? e.message : String(e);
    }
  }
  throw new Error(lastError);
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbol = (searchParams.get('symbol') ?? '').trim();
  const interval = searchParams.get('interval') ?? '5m';
  const range = searchParams.get('range') ?? '5d';
  if (!SYMBOL.test(symbol)) return NextResponse.json({ error: 'bad symbol' }, { status: 400 });
  if (!INTERVALS.has(interval) || !RANGES.has(range)) {
    return NextResponse.json({ error: 'bad interval or range' }, { status: 400 });
  }
  try {
    const result = await fetchYahoo(symbol, interval, range);
    const quote = result.indicators.quote[0] ?? {};
    const bars: Bar[] = [];
    (result.timestamp ?? []).forEach((time, i) => {
      const open = quote.open?.[i];
      const high = quote.high?.[i];
      const low = quote.low?.[i];
      const close = quote.close?.[i];
      if (open == null || high == null || low == null || close == null) return;
      bars.push({ time, open, high, low, close, volume: quote.volume?.[i] ?? 0 });
    });
    const m = result.meta;
    const body: BarsResponse = {
      interval,
      bars,
      meta: {
        symbol: String(m.symbol ?? symbol),
        name: String(m.longName ?? m.shortName ?? m.symbol ?? symbol),
        currency: String(m.currency ?? ''),
        exchange: String(m.fullExchangeName ?? m.exchangeName ?? ''),
        timezone: String(m.exchangeTimezoneName ?? 'UTC'),
        prevClose: typeof m.chartPreviousClose === 'number' ? m.chartPreviousClose : null,
        price: typeof m.regularMarketPrice === 'number' ? m.regularMarketPrice : null,
      },
    };
    return NextResponse.json(body, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : 'fetch failed' }, { status: 502 });
  }
}
