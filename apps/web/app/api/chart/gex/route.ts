import { NextResponse } from 'next/server';
import {
  computeLevels,
  type GexConvention,
  type GexResponse,
  type OptionRow,
  paritySpot,
  usExpiryInstant,
  usRows,
} from '../../../../lib/tv/gex';
import { futureBySymbol, instrumentKey } from '../../../../lib/broker/upstox-instruments';
import { mapPool, optionChain } from '../../../../lib/screener/yahoo';

/**
 * Gamma-exposure levels for a chart symbol, as a snapshot of *current* open interest: it says nothing
 * about where the levels were on earlier candles. Cached a minute so chart refreshes do not hammer the
 * source.
 *  - NSE (indices, F&O stocks, futures): Upstox's option chain, with `UPSTOX_ANALYTICS_TOKEN` (read-only,
 *    server-side only). One call for the expiry list plus one per expiry.
 *  - US (stocks, ETFs, ^SPX/^NDX/^RUT): Yahoo's option chain, ~15 minutes delayed. Futures have no options
 *    on Yahoo, so ES/NQ/RTY/GC/SI use their index or ETF (`US_PROXY`) and the levels are scaled onto the
 *    future's price, the way MenthorQ "converts" SPX levels to ES.
 */
export const dynamic = 'force-dynamic';

const UPSTOX = 'https://api.upstox.com';
const TTL_MS = 60_000;
const IST_OFFSET = 19_800;

/** NSE index options by underlying name (as futures and the instrument master spell it). */
const INDEX_KEYS: Record<string, string> = {
  NIFTY: 'NSE_INDEX|Nifty 50',
  BANKNIFTY: 'NSE_INDEX|Nifty Bank',
  FINNIFTY: 'NSE_INDEX|Nifty Fin Service',
  MIDCPNIFTY: 'NSE_INDEX|NIFTY MID SELECT',
};
/** Yahoo index symbols the chart uses → underlying name. */
const YAHOO_INDEX: Record<string, string> = { '^NSEI': 'NIFTY', '^NSEBANK': 'BANKNIFTY' };

interface Underlying {
  name: string;
  /** Upstox key the option chain hangs off (index or NSE_EQ stock). */
  key: string;
  /** Set when the chart shows a future: its key, to shift strike levels by the futures basis. */
  futKey?: string;
}

/** `^NSEI`, `RELIANCE.NS`, `RELIANCE1!.NFO`, `NIFTY26NOVFUT.NFO` → the options underlying. */
async function resolve(symbol: string): Promise<Underlying | undefined> {
  const keyOf = async (name: string) => INDEX_KEYS[name] ?? (await instrumentKey(name));
  const yahoo = YAHOO_INDEX[symbol];
  if (yahoo) return { name: yahoo, key: INDEX_KEYS[yahoo]! };
  if (symbol.endsWith('.NS')) {
    const name = symbol.slice(0, -3);
    const key = await keyOf(name);
    return key ? { name, key } : undefined;
  }
  if (symbol.endsWith('.NFO') || symbol.endsWith('1!')) {
    const fut = await futureBySymbol(symbol);
    if (!fut) return undefined;
    const key = await keyOf(fut.underlying);
    return key ? { name: fut.underlying, key, futKey: fut.key } : undefined;
  }
  return undefined;
}

const isNseSymbol = (symbol: string) =>
  symbol in YAHOO_INDEX || symbol.endsWith('.NS') || symbol.endsWith('.NFO') || symbol.endsWith('1!');

// ── US (Yahoo) ───────────────────────────────────────────────────────────────

/** Chart symbol → the Yahoo chain its levels come from, when it is not its own. */
const US_PROXY: Record<string, string> = {
  '^GSPC': '^SPX',
  'ES=F': '^SPX',
  'MES=F': '^SPX',
  // NDX / RUT options barely have bid/ask quotes on Yahoo; QQQ and IWM are liquid and track them.
  'NQ=F': 'QQQ',
  'MNQ=F': 'QQQ',
  'RTY=F': 'IWM',
  'M2K=F': 'IWM',
  'YM=F': 'DIA',
  'MYM=F': 'DIA',
  'GC=F': 'GLD',
  'MGC=F': 'GLD',
  'SI=F': 'SLV',
};
/** At most this many expiries per chain: SPX lists one every trading day, and each is a request. */
const MAX_US_EXPIRIES = 20;

/** Last price of a chart symbol (the chart endpoint needs no session). */
async function yahooPrice(symbol: string): Promise<number | undefined> {
  const res = await fetch(
    `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1d`,
    { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' },
  );
  if (!res.ok) return undefined;
  const body = (await res.json()) as {
    chart?: { result?: { meta?: { regularMarketPrice?: number } }[] };
  };
  return body.chart?.result?.[0]?.meta?.regularMarketPrice;
}

async function usLevels(symbol: string, days: number, convention: GexConvention): Promise<GexResponse> {
  if (symbol.endsWith('=F') && !US_PROXY[symbol]) {
    return none(`no options source for ${symbol}: Yahoo has no options on futures`);
  }
  const chainSymbol = US_PROXY[symbol] ?? symbol;
  const first = await optionChain(chainSymbol);
  if (!first || !first.expirationDates?.length) return none(`${chainSymbol} has no listed options`);
  const quoteSpot = first.quote?.regularMarketPrice;
  if (!quoteSpot) return none(`no price for ${chainSymbol}`);

  const now = Date.now() / 1000;
  // The quote can be yesterday's close while the options already trade off pre-market: take the
  // price the nearest expiry's quotes imply (put–call parity), falling back to the quote.
  const front = first.options[0];
  const spot =
    (front &&
      paritySpot(front.calls, front.puts, usExpiryInstant(front.expirationDate), quoteSpot, now)) ||
    quoteSpot;
  const horizon = now + days * 86_400;
  const dates = first.expirationDates
    .filter((d) => usExpiryInstant(d) > now && usExpiryInstant(d) <= horizon)
    .slice(0, MAX_US_EXPIRIES);
  if (!dates.length) return none('no live expiries in the window');

  const tally = { mid: 0, last: 0, yahoo: 0 };
  const chunks = await mapPool(dates, 4, async (d) => {
    const chain = first.options[0]?.expirationDate === d ? first : await optionChain(chainSymbol, d);
    const leg = chain?.options[0];
    return leg ? usRows(leg.calls, leg.puts, usExpiryInstant(d), spot, now, tally) : [];
  });
  const rows = chunks.flat();
  if (!rows.length) return none('empty option chain');

  const chartPrice = chainSymbol === symbol ? undefined : await yahooPrice(symbol);
  return {
    supported: true,
    source: 'yahoo',
    currency: 'USD',
    symbol,
    asOf: Math.floor(now),
    spot,
    ...(chainSymbol !== symbol ? { proxy: chainSymbol } : {}),
    ...(chartPrice ? { futurePrice: chartPrice, scale: chartPrice / spot } : {}),
    convention,
    expiries: dates.map((d) => new Date(d * 1000).toISOString().slice(0, 10)),
    ivSources: tally,
    all: computeLevels(rows, spot, now, convention),
    nearest: computeLevels(chunks[0] ?? [], spot, now, convention),
  };
}

async function lastPrice(key: string, token: string): Promise<number | undefined> {
  const res = await fetch(`${UPSTOX}/v2/market-quote/ltp?instrument_key=${encodeURIComponent(key)}`, {
    headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (!res.ok) return undefined;
  const body = (await res.json()) as { data?: Record<string, { last_price?: number }> };
  return Object.values(body.data ?? {})[0]?.last_price;
}

const CONVENTIONS: GexConvention[] = ['dealer_long_calls', 'dealer_short_all'];

const cache = new Map<string, { at: number; body: GexResponse }>();

const none = (reason: string): GexResponse => ({ supported: false, reason });

interface ChainItem {
  strike_price: number;
  underlying_spot_price: number;
  call_options?: { market_data?: { oi?: number; volume?: number }; option_greeks?: { iv?: number } };
  put_options?: { market_data?: { oi?: number; volume?: number }; option_greeks?: { iv?: number } };
}

async function upstox<T>(path: string, token: string): Promise<T[]> {
  const res = await fetch(`${UPSTOX}${path}`, {
    headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (res.status === 401) throw new Error('Upstox rejected the token: it needs refreshing');
  if (!res.ok) throw new Error(`Upstox HTTP ${res.status}`);
  const body = (await res.json()) as { status?: string; data?: T[] };
  return Array.isArray(body.data) ? body.data : [];
}

/** 15:30 IST on an expiry day, unix seconds. */
const expiryInstant = (day: string) => Date.parse(`${day}T15:30:00Z`) / 1000 - IST_OFFSET;

function toRows(items: ChainItem[], expiry: number): OptionRow[] {
  const rows: OptionRow[] = [];
  for (const it of items) {
    for (const [side, kind] of [
      [it.call_options, 'CE'],
      [it.put_options, 'PE'],
    ] as const) {
      rows.push({
        expiry,
        strike: it.strike_price,
        kind,
        oi: side?.market_data?.oi ?? 0,
        iv: (side?.option_greeks?.iv ?? 0) / 100,
        volume: side?.market_data?.volume ?? 0,
      });
    }
  }
  return rows;
}

export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const symbol = (sp.get('symbol') ?? '').trim().toUpperCase();
  const days = Math.min(Math.max(Number(sp.get('days')) || 45, 1), 120);
  const convention = (sp.get('convention') ?? 'dealer_long_calls') as GexConvention;
  if (!CONVENTIONS.includes(convention)) {
    return NextResponse.json({ error: 'bad convention' }, { status: 400 });
  }
  const cacheKey = `${symbol}|${days}|${convention}`;
  const hit = cache.get(cacheKey);
  if (hit && Date.now() - hit.at < TTL_MS) return NextResponse.json(hit.body);

  if (!isNseSymbol(symbol)) {
    try {
      const body = await usLevels(symbol, days, convention);
      if (body.supported) cache.set(cacheKey, { at: Date.now(), body });
      return NextResponse.json(body);
    } catch (err) {
      return NextResponse.json(none(err instanceof Error ? err.message : 'option chain request failed'));
    }
  }

  const token = process.env.UPSTOX_ANALYTICS_TOKEN;
  if (!token) return NextResponse.json(none('UPSTOX_ANALYTICS_TOKEN is not set for the web app'));

  try {
    const und = await resolve(symbol);
    if (!und) {
      return NextResponse.json(
        none('GEX needs an NSE index or F&O stock (e.g. ^NSEI, RELIANCE.NS, RELIANCE1!.NFO)'),
      );
    }
    const now = Date.now() / 1000;
    const today = new Date((now + IST_OFFSET) * 1000).toISOString().slice(0, 10);
    const contracts = await upstox<{ expiry: string }>(
      `/v2/option/contract?instrument_key=${encodeURIComponent(und.key)}`,
      token,
    );
    if (!contracts.length) return NextResponse.json(none(`${und.name} has no listed options`));
    const horizon = now + days * 86_400;
    const expiries = [...new Set(contracts.map((c) => c.expiry))]
      .filter((d) => d >= today && expiryInstant(d) <= horizon && expiryInstant(d) > now)
      .sort();
    if (!expiries.length) return NextResponse.json(none('no live expiries in the window'));

    let spot = 0;
    const rows: OptionRow[] = [];
    const nearestRows: OptionRow[] = [];
    for (const day of expiries) {
      const items = await upstox<ChainItem>(
        `/v2/option/chain?instrument_key=${encodeURIComponent(und.key)}&expiry_date=${day}`,
        token,
      );
      if (items[0]) spot = items[0].underlying_spot_price;
      const chunk = toRows(items, expiryInstant(day));
      rows.push(...chunk);
      if (day === expiries[0]) nearestRows.push(...chunk);
    }
    if (!rows.length || !spot) return NextResponse.json(none('empty option chain'));

    // Options settle on the cash price; a futures chart trades at a premium (carry). Scale the strike
    // levels by futures ÷ spot so they sit where they belong on the futures candles.
    const futPrice = und.futKey ? await lastPrice(und.futKey, token) : undefined;

    const body: GexResponse = {
      supported: true,
      source: 'upstox',
      currency: 'INR',
      symbol: und.name,
      asOf: Math.floor(now),
      spot,
      ...(futPrice ? { futurePrice: futPrice, scale: futPrice / spot } : {}),
      convention,
      expiries,
      all: computeLevels(rows, spot, now, convention),
      nearest: computeLevels(nearestRows, spot, now, convention),
    };
    cache.set(cacheKey, { at: Date.now(), body });
    return NextResponse.json(body);
  } catch (err) {
    return NextResponse.json(none(err instanceof Error ? err.message : 'option chain request failed'));
  }
}
