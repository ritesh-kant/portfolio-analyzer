import { futureBySymbol, instrumentKey } from '../broker/upstox-instruments.ts';
import { computeLevels, type GexConvention, type GexResponse, type OptionRow } from './gex.ts';

/**
 * NSE gamma levels from Upstox's option chain, as a snapshot of *current* open interest (Upstox updates it during
 * the session). Server only: needs `UPSTOX_ANALYTICS_TOKEN` (read-only). Shared by `/api/chart/gex` and the
 * snapshot recorder (`pnpm bt:gex --symbol NIFTY1!.NFO`, research/backtests/gex_snapshot.mjs), so the chart and the
 * recorded history use the same maths. One call for the expiry list plus one per expiry.
 */

const UPSTOX = 'https://api.upstox.com';
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

export const isNseSymbol = (symbol: string) =>
  symbol in YAHOO_INDEX || symbol.endsWith('.NS') || symbol.endsWith('.NFO') || symbol.endsWith('1!');

const none = (reason: string): GexResponse => ({ supported: false, reason });

async function lastPrice(key: string, token: string): Promise<number | undefined> {
  const res = await fetch(`${UPSTOX}/v2/market-quote/ltp?instrument_key=${encodeURIComponent(key)}`, {
    headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (!res.ok) return undefined;
  const body = (await res.json()) as { data?: Record<string, { last_price?: number }> };
  return Object.values(body.data ?? {})[0]?.last_price;
}

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

/**
 * Levels for an NSE chart symbol. `symbol` in the response is the underlying (NIFTY); on a futures chart
 * `futurePrice`/`scale` (futures ÷ spot) move the strike levels onto the futures candles.
 */
export async function nseLevels(symbol: string, days: number, convention: GexConvention, token: string): Promise<GexResponse> {
  const und = await resolve(symbol);
  if (!und) return none('GEX needs an NSE index or F&O stock (e.g. ^NSEI, RELIANCE.NS, RELIANCE1!.NFO)');
  const now = Date.now() / 1000;
  const today = new Date((now + IST_OFFSET) * 1000).toISOString().slice(0, 10);
  const contracts = await upstox<{ expiry: string }>(`/v2/option/contract?instrument_key=${encodeURIComponent(und.key)}`, token);
  if (!contracts.length) return none(`${und.name} has no listed options`);
  const horizon = now + days * 86_400;
  const expiries = [...new Set(contracts.map((c) => c.expiry))]
    .filter((d) => d >= today && expiryInstant(d) <= horizon && expiryInstant(d) > now)
    .sort();
  if (!expiries.length) return none('no live expiries in the window');

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
  if (!rows.length || !spot) return none('empty option chain');

  // Options settle on the cash price; a futures chart trades at a premium (carry). Scale the strike
  // levels by futures ÷ spot so they sit where they belong on the futures candles.
  const futPrice = und.futKey ? await lastPrice(und.futKey, token) : undefined;

  return {
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
}
