import { gunzipSync } from 'node:zlib';

const INSTRUMENTS_URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz';
const DAY_MS = 86_400_000;

interface MasterRow {
  segment?: string;
  instrument_type?: string;
  trading_symbol?: string;
  instrument_key?: string;
  underlying_symbol?: string;
  name?: string;
  expiry?: number;
  lot_size?: number;
}

interface Master {
  at: number;
  rows: MasterRow[];
  bySymbol: Map<string, string>;
}
let master: Master | null = null;
let inflight: Promise<Master> | null = null;

async function loadMaster(): Promise<Master> {
  if (master && Date.now() - master.at <= DAY_MS) return master;
  inflight ??= (async () => {
    const res = await fetch(INSTRUMENTS_URL);
    if (!res.ok) throw new Error(`instrument master HTTP ${res.status}`);
    const all = JSON.parse(gunzipSync(Buffer.from(await res.arrayBuffer())).toString('utf8')) as MasterRow[];
    const rows = all.filter(
      (r) => (r.segment === 'NSE_EQ' && r.instrument_type === 'EQ') || (r.segment === 'NSE_FO' && r.instrument_type === 'FUT'),
    );
    const bySymbol = new Map<string, string>();
    for (const r of rows) {
      if (r.segment === 'NSE_EQ' && r.trading_symbol && r.instrument_key) bySymbol.set(r.trading_symbol, r.instrument_key);
    }
    const fresh: Master = { at: Date.now(), rows, bySymbol };
    master = fresh;
    return fresh;
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

/** Upstox addresses a stock by instrument key, not symbol; this is the NSE equity master, refreshed daily. */
export async function instrumentKey(symbol: string): Promise<string | undefined> {
  return (await loadMaster()).bySymbol.get(symbol);
}

export interface FutureContract {
  /** Chart symbol, e.g. NIFTY26NOVFUT.NFO */
  symbol: string;
  underlying: string;
  name: string;
  key: string;
  expiry: number;
  lotSize: number;
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/** Unexpired NSE futures, nearest expiry first. */
export async function futureContracts(): Promise<FutureContract[]> {
  const now = Date.now();
  const out: FutureContract[] = [];
  for (const r of (await loadMaster()).rows) {
    if (r.segment !== 'NSE_FO' || !r.underlying_symbol || !r.instrument_key || !r.expiry || r.expiry < now) continue;
    const d = new Date(r.expiry + 19_800_000);
    const yy = String(d.getUTCFullYear()).slice(2);
    out.push({
      symbol: `${r.underlying_symbol}${yy}${MONTHS[d.getUTCMonth()]}FUT.NFO`,
      underlying: r.underlying_symbol,
      name: r.name ?? r.underlying_symbol,
      key: r.instrument_key,
      expiry: r.expiry,
      lotSize: r.lot_size ?? 0,
    });
  }
  return out.sort((a, b) => a.expiry - b.expiry);
}

/** Resolve a `.NFO` chart symbol: `NIFTY26NOVFUT.NFO` exactly, or `NIFTY1!.NFO` / `NIFTY1!` for the front month. */
export async function futureBySymbol(symbol: string): Promise<FutureContract | undefined> {
  const all = await futureContracts();
  const s = symbol.toUpperCase().replace(/\.NFO$/, '');
  const front = /^(.+?)1!$/.exec(s);
  if (front) return all.find((c) => c.underlying === front[1]);
  return all.find((c) => c.symbol === `${s}.NFO`);
}
