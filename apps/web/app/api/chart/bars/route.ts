import { NextResponse } from 'next/server';
import { backfillOpeningVolume, dropSpikeWicks, intervalSecs } from '../../../../lib/tv/clean-bars';
import { hasExtendedBars, sessionFor } from '../../../../lib/tv/sessions';
import { sessionDay, wallClock } from '../../../../lib/tv/timeframes';
import type { Bar, BarsResponse } from '../../../../lib/tv/types';

export const dynamic = 'force-dynamic';

const HOSTS = ['query1.finance.yahoo.com', 'query2.finance.yahoo.com'];
const INTERVALS = new Set([
  '1m',
  '2m',
  '5m',
  '15m',
  '30m',
  '60m',
  '90m',
  '1h',
  '1d',
  '5d',
  '1wk',
  '1mo',
]);
const RANGES = new Set([
  '1d',
  '5d',
  '7d',
  '1mo',
  '3mo',
  '6mo',
  '60d',
  '1y',
  '2y',
  '5y',
  '10y',
  'ytd',
  'max',
]);
const SYMBOL = /^[A-Za-z0-9.^=\-_&]{1,24}$/;

interface YahooResult {
  meta: Record<string, unknown>;
  timestamp?: number[];
  indicators: { quote: Array<Record<string, Array<number | null>>> };
}

/** A date window (unix seconds) instead of a named range: Yahoo serves 1-minute bars ~4 weeks back, 7 days at a time. */
interface Window {
  from: number;
  to: number;
}
const MAX_WINDOW_SECS = 8 * 86400;

async function fetchYahoo(
  symbol: string,
  interval: string,
  range: string,
  prepost: boolean,
  window?: Window,
): Promise<YahooResult> {
  let lastError = 'upstream unavailable';
  const span = window ? `period1=${window.from}&period2=${window.to}` : `range=${range}`;
  for (const host of HOSTS) {
    const url = `https://${host}/v8/finance/chart/${encodeURIComponent(symbol)}?interval=${interval}&${span}&includePrePost=${prepost}`;
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

/** Daily bars covering the same span, used to backfill a missing opening-bar volume. Best effort: [] on failure. */
async function fetchDailyBars(symbol: string, range: string, window?: Window): Promise<Bar[]> {
  try {
    const r = await fetchYahoo(symbol, '1d', range, false, window);
    const q = r.indicators.quote[0] ?? {};
    const out: Bar[] = [];
    (r.timestamp ?? []).forEach((time, i) => {
      const v = q.volume?.[i];
      if (v) out.push({ time, open: 0, high: 0, low: 0, close: 0, volume: v });
    });
    return out;
  } catch {
    return [];
  }
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbol = (searchParams.get('symbol') ?? '').trim();
  const interval = searchParams.get('interval') ?? '5m';
  const range = searchParams.get('range') ?? '5d';
  const prepost = searchParams.get('prepost') === '1';
  if (!SYMBOL.test(symbol)) return NextResponse.json({ error: 'bad symbol' }, { status: 400 });
  if (!INTERVALS.has(interval) || !RANGES.has(range)) {
    return NextResponse.json({ error: 'bad interval or range' }, { status: 400 });
  }
  let window: Window | undefined;
  if (searchParams.has('period1') || searchParams.has('period2')) {
    const from = Number(searchParams.get('period1'));
    const to = Number(searchParams.get('period2'));
    if (
      !Number.isInteger(from) ||
      !Number.isInteger(to) ||
      from <= 0 ||
      to <= from ||
      to - from > MAX_WINDOW_SECS
    ) {
      return NextResponse.json({ error: 'bad period' }, { status: 400 });
    }
    window = { from, to };
  }
  try {
    const result = await fetchYahoo(symbol, interval, range, prepost, window);
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
    const meta: BarsResponse['meta'] = {
      symbol: String(m.symbol ?? symbol),
      name: String(m.longName ?? m.shortName ?? m.symbol ?? symbol),
      currency: String(m.currency ?? ''),
      exchange: String(m.fullExchangeName ?? m.exchangeName ?? ''),
      timezone: String(m.exchangeTimezoneName ?? 'UTC'),
      instrumentType: String(m.instrumentType ?? ''),
      prevClose: typeof m.chartPreviousClose === 'number' ? m.chartPreviousClose : null,
      price: typeof m.regularMarketPrice === 'number' ? m.regularMarketPrice : null,
    };
    // US stocks: stray quotes in the no-volume pre/post-market bars draw as wicks off the scale
    const def = hasExtendedBars({ ...meta, symbol }) ? sessionFor({ ...meta, symbol }) : null;
    // NSE: the 09:15 bar often has volume 0 (see backfillOpeningVolume)
    const secs = intervalSecs(interval);
    const isNse = /\.(NS|BO)$/.test(symbol) || meta.exchange.includes('NSE');
    const filled =
      isNse && secs && secs <= 3600 && bars.length > 0 && bars.some((b) => b.volume === 0)
        ? backfillOpeningVolume(bars, await fetchDailyBars(symbol, range, window), (t) =>
            sessionDay(t, meta.timezone),
          )
        : bars;
    const body: BarsResponse = {
      interval,
      bars: def
        ? dropSpikeWicks(bars, secs, (t) => {
            const minute = Math.floor((wallClock(t, meta.timezone) % 86400) / 60);
            return minute >= def.start && minute < def.end;
          })
        : filled,
      meta,
    };
    return NextResponse.json(body, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : 'fetch failed' },
      { status: 502 },
    );
  }
}
