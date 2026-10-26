import { gunzipSync } from 'node:zlib';

const INSTRUMENTS_URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz';
const DAY_MS = 86_400_000;

let instruments: { at: number; bySymbol: Map<string, string> } | null = null;

/** Upstox addresses a stock by instrument key, not symbol; this is the NSE equity master, refreshed daily. */
export async function instrumentKey(symbol: string): Promise<string | undefined> {
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
