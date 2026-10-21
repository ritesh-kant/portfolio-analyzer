import { gunzipSync } from 'node:zlib';
import { NextResponse } from 'next/server';
import type { Bar } from '../../../../lib/tv/types';

/**
 * Exchange candles for NSE stocks from Upstox's public candle API (no token). It reaches back years at 1-minute
 * resolution, where Yahoo stops at ~4 weeks, but takes at most about a month per request, so the client asks for
 * windows. Today's session is not in the historical endpoint; it comes from the intraday one.
 */
export const dynamic = 'force-dynamic';

const UPSTOX = 'https://api.upstox.com';
const INSTRUMENTS_URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz';
const DAY_MS = 86_400_000;
const MAX_WINDOW_DAYS = 30;
/** Chart interval → Upstox minutes. */
const MINUTES: Record<string, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30 };
const SYMBOL = /^[A-Za-z0-9&\-_]{1,24}$/;

let instruments: { at: number; bySymbol: Map<string, string> } | null = null;

async function instrumentKey(symbol: string): Promise<string | undefined> {
  if (!instruments || Date.now() - instruments.at > DAY_MS) {
    const res = await fetch(INSTRUMENTS_URL);
    if (!res.ok) throw new Error(`instrument master HTTP ${res.status}`);
    const rows = JSON.parse(gunzipSync(Buffer.from(await res.arrayBuffer())).toString('utf8')) as {
      segment?: string;
      instrument_type?: string;
      trading_symbol?: string;
      instrument_key?: string;
    }[];
    const bySymbol = new Map<string, string>();
    for (const r of rows) {
      if (r.segment === 'NSE_EQ' && r.instrument_type === 'EQ' && r.trading_symbol && r.instrument_key) {
        bySymbol.set(r.trading_symbol, r.instrument_key);
      }
    }
    instruments = { at: Date.now(), bySymbol };
  }
  return instruments.bySymbol.get(symbol);
}

/** YYYY-MM-DD of an instant in India (UTC+5:30, no DST). */
function istDay(unix: number): string {
  return new Date((unix + 19_800) * 1000).toISOString().slice(0, 10);
}

type Raw = [string, number, number, number, number, number, number?][];

async function candles(path: string): Promise<Raw> {
  const res = await fetch(`${UPSTOX}${path}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!res.ok) throw new Error(`upstox candles HTTP ${res.status}`);
  const body = (await res.json()) as { data?: { candles?: Raw } };
  return body.data?.candles ?? [];
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const raw = (searchParams.get('symbol') ?? '').trim().toUpperCase();
  const interval = searchParams.get('interval') ?? '';
  const from = Number(searchParams.get('from'));
  const to = Number(searchParams.get('to'));
  const symbol = raw.replace(/\.NS$/, '');
  const minutes = MINUTES[interval];
  if (!raw.endsWith('.NS') || !SYMBOL.test(symbol)) {
    return NextResponse.json({ error: 'NSE symbols only (…NS)' }, { status: 400 });
  }
  if (!minutes) return NextResponse.json({ error: 'bad interval' }, { status: 400 });
  if (!Number.isInteger(from) || !Number.isInteger(to) || from <= 0 || to <= from) {
    return NextResponse.json({ error: 'bad period' }, { status: 400 });
  }
  if (to - from > MAX_WINDOW_DAYS * 86400) {
    return NextResponse.json({ error: `window over ${MAX_WINDOW_DAYS} days` }, { status: 400 });
  }
  try {
    const key = await instrumentKey(symbol);
    if (!key) return NextResponse.json({ error: `no NSE equity instrument for ${symbol}` }, { status: 404 });
    const enc = encodeURIComponent(key);
    const today = istDay(Math.floor(Date.now() / 1000));
    const fromDay = istDay(from);
    const toDay = istDay(to);
    const rows: Raw = [];
    if (fromDay < today) {
      const histTo = toDay < today ? toDay : istDay(Math.floor(Date.now() / 1000) - 86400);
      rows.push(...(await candles(`/v3/historical-candle/${enc}/minutes/${minutes}/${histTo}/${fromDay}`)));
    }
    if (toDay >= today) {
      rows.push(...(await candles(`/v3/historical-candle/intraday/${enc}/minutes/${minutes}`)));
    }
    const byTime = new Map<number, Bar>();
    for (const [t, open, high, low, close, volume] of rows) {
      const time = Math.floor(Date.parse(t) / 1000);
      if (!Number.isFinite(time) || time < from || time > to) continue;
      byTime.set(time, { time, open, high, low, close, volume });
    }
    const bars = [...byTime.values()].sort((a, b) => a.time - b.time);
    return NextResponse.json(
      { interval, bars, source: 'upstox' },
      { headers: { 'Cache-Control': toDay >= today ? 'no-store' : 'private, max-age=3600' } },
    );
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : 'fetch failed' }, { status: 502 });
  }
}
