import { NextResponse } from 'next/server';
import { backfillOpeningVolume, dropSpikeWicks, intervalSecs } from '../../../../lib/tv/clean-bars';
import { hasExtendedBars, sessionFor } from '../../../../lib/tv/sessions';
import { sessionDay, wallClock } from '../../../../lib/tv/timeframes';
import type { Bar, BarsResponse } from '../../../../lib/tv/types';
import { futureBySymbol } from '../../../../lib/broker/upstox-instruments';

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
const SYMBOL = /^[A-Za-z0-9.^=\-_&!]{1,24}$/;

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

const UPSTOX = 'https://api.upstox.com';
/** Chart interval → Upstox [unit, n]. */
const UPSTOX_UNITS: Record<string, [string, number]> = {
  '1m': ['minutes', 1],
  '5m': ['minutes', 5],
  '15m': ['minutes', 15],
  '30m': ['minutes', 30],
  '60m': ['hours', 1],
  '1h': ['hours', 1],
  '1d': ['days', 1],
  '1wk': ['weeks', 1],
  '1mo': ['months', 1],
};
const RANGE_DAYS: Record<string, number> = {
  '1d': 1, '5d': 5, '7d': 7, '1mo': 31, '3mo': 92, '6mo': 183, '60d': 60, '1y': 366, '2y': 731, '5y': 1827, '10y': 3653, ytd: 366, max: 3653,
};
const istDay = (unix: number) => new Date((unix + 19_800) * 1000).toISOString().slice(0, 10);

/** NSE futures (`NIFTY1!.NFO`, `NIFTY26NOVFUT.NFO`): Yahoo has none, so candles come from Upstox's public API. */
async function futuresBars(symbol: string, interval: string, range: string, window?: Window) {
  const fut = await futureBySymbol(symbol);
  if (!fut) return NextResponse.json({ error: `no NSE future for ${symbol}` }, { status: 404 });
  const unit = UPSTOX_UNITS[interval];
  if (!unit) return NextResponse.json({ error: `interval ${interval} not available for futures` }, { status: 400 });
  const [u, n] = unit;
  const nowS = Math.floor(Date.now() / 1000);
  const to = window?.to ?? nowS;
  const from = window?.from ?? to - (RANGE_DAYS[range] ?? 7) * 86400;
  const enc = encodeURIComponent(fut.key);
  const paths: string[] = [];
  const today = istDay(nowS);
  // Upstox rejects a historical request longer than its per-interval cap ("Invalid date range"), which
  // left a 60-day 15m chart with only today's intraday candles. Ask in chunks under the cap instead.
  const chunkDays = u === 'minutes' && n <= 15 ? 28 : u === 'minutes' || u === 'hours' ? 88 : 3600;
  const histTo = Math.min(to, nowS - 86400);
  for (let end = histTo; istDay(from) < today && end >= from; end -= chunkDays * 86400) {
    const start = Math.max(from, end - (chunkDays - 1) * 86400);
    paths.push(`/v3/historical-candle/${enc}/${u}/${n}/${istDay(end)}/${istDay(start)}`);
  }
  if (istDay(to) >= today && (u === 'minutes' || u === 'hours')) paths.push(`/v3/historical-candle/intraday/${enc}/${u}/${n}`);
  type Raw = [string, number, number, number, number, number, number?][];
  const byTime = new Map<number, Bar>();
  let ok = 0;
  const responses = await Promise.all(
    paths.map((path) =>
      fetch(`${UPSTOX}${path}`, { headers: { Accept: 'application/json' }, cache: 'no-store' }).catch(() => null),
    ),
  );
  for (const res of responses) {
    if (!res?.ok) continue;
    ok += 1;
    const body = (await res.json()) as { data?: { candles?: Raw } };
    for (const [t, open, high, low, close, volume] of body.data?.candles ?? []) {
      const time = Math.floor(Date.parse(t) / 1000);
      if (Number.isFinite(time) && time >= from && time <= to) byTime.set(time, { time, open, high, low, close, volume });
    }
  }
  if (!ok) return NextResponse.json({ error: 'upstox unavailable' }, { status: 502 });
  const bars = [...byTime.values()].sort((a, b) => a.time - b.time);
  const last = bars[bars.length - 1];
  const meta: BarsResponse['meta'] = {
    symbol,
    name: `${fut.name} Futures · ${new Date(fut.expiry + 19_800_000).toISOString().slice(0, 10)}`,
    currency: 'INR',
    exchange: 'NSE',
    timezone: 'Asia/Kolkata',
    instrumentType: 'FUTURE',
    prevClose: null,
    price: last?.close ?? null,
  };
  return NextResponse.json({ interval, bars, meta } satisfies BarsResponse, { headers: { 'Cache-Control': 'no-store' } });
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
  if (/\.NFO$/i.test(symbol) || /1!$/.test(symbol)) {
    try {
      return await futuresBars(symbol, interval, range, window);
    } catch (e) {
      return NextResponse.json({ error: e instanceof Error ? e.message : 'fetch failed' }, { status: 502 });
    }
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
