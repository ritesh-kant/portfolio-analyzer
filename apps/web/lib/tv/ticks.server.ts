import path from 'node:path';
import { futureBySymbol, instrumentKey } from '../broker/upstox-instruments';

/**
 * Where `apps/signal-engine/scripts/capture_ticks.py` writes the feed (`data/ticks/<IST day>/<key>.jsonl`) and how
 * a chart symbol maps to its file. The web app runs from apps/web. Local only: nothing of this exists on Vercel.
 */
export const ENGINE_DIR = path.resolve(process.cwd(), '../signal-engine');
export const TICKS_DIR = process.env.TICKS_DIR ?? path.join(ENGINE_DIR, 'data/ticks');
export const TICK_RECORDER = path.join(ENGINE_DIR, 'scripts/capture_ticks.py');
export const ENGINE_PYTHON = process.env.TICK_RECORDER_PYTHON ?? path.join(ENGINE_DIR, '.venv/bin/python');
export const DAY_DIR = /^\d{4}-\d{2}-\d{2}$/;

/** Upstox instrument keys look like NSE_EQ|INE002A01018 or NSE_FO|61471. */
export const INSTRUMENT_KEY = /^[A-Z_]{3,10}\|[A-Za-z0-9_-]{1,32}$/;

/** NSE stock (RELIANCE.NS) or NSE future (NIFTY1!.NFO, NIFTY26NOVFUT.NFO) → Upstox instrument key. */
export async function resolveTickKey(symbol: string, explicit?: string | null): Promise<string | undefined> {
  if (explicit && INSTRUMENT_KEY.test(explicit)) return explicit;
  const s = symbol.toUpperCase();
  if (s.endsWith('.NS')) return instrumentKey(s.slice(0, -3));
  if (s.endsWith('.NFO') || s.endsWith('1!')) return (await futureBySymbol(s))?.key;
  return undefined;
}

export const tickFileName = (key: string) => `${key.replace('|', '_')}.jsonl`;
