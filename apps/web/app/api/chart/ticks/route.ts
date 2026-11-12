import { NextResponse } from 'next/server';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { classify, coverage, parseTicks, type Trade } from '../../../../lib/tv/bigtrades';
import { futureBySymbol, instrumentKey } from '../../../../lib/broker/upstox-instruments';

/**
 * Sampled trade tape for the chart's "Big Trades" bubbles, read from the files that
 * `apps/signal-engine/scripts/capture_ticks.py` writes (`data/ticks/<IST day>/<key>.jsonl`).
 *
 * Local only: it reads the repo's disk, so it returns nothing on Vercel. Putting the tape in Mongo behind
 * portfolio-service is the production step. See research/big-trades-bubbles-plan.md.
 */
export const dynamic = 'force-dynamic';

const DIR = process.env.TICKS_DIR ?? path.resolve(process.cwd(), '../signal-engine/data/ticks');
const DAY = /^\d{4}-\d{2}-\d{2}$/;
/** Most recent capture days returned, and a cap on trades so a busy stock cannot flood the browser. */
const MAX_DAYS = 3;
const MAX_TRADES = 60_000;
/** Upstox instrument keys look like NSE_EQ|INE002A01018. */
const KEY = /^[A-Z_]{3,10}\|[A-Za-z0-9_\-]{1,32}$/;

async function resolveKey(symbol: string, explicit: string | null): Promise<string | undefined> {
  if (explicit && KEY.test(explicit)) return explicit;
  const s = symbol.toUpperCase();
  if (s.endsWith('.NS')) return instrumentKey(s.slice(0, -3));
  if (s.endsWith('.NFO') || s.endsWith('1!')) return (await futureBySymbol(s))?.key;
  return undefined;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbol = (searchParams.get('symbol') ?? '').trim();
  const key = await resolveKey(symbol, searchParams.get('key')).catch(() => undefined);
  if (!key) return NextResponse.json({ key: null, days: [], trades: [], coverage: null });

  const file = `${key.replace('|', '_')}.jsonl`;
  let days: string[] = [];
  try {
    days = (await fs.readdir(DIR)).filter((d) => DAY.test(d)).sort().slice(-MAX_DAYS);
  } catch {
    /* no capture has ever run */
  }
  const trades: Trade[] = [];
  const have: string[] = [];
  const cov: number[] = [];
  for (const day of days) {
    try {
      const ticks = parseTicks(await fs.readFile(path.join(DIR, day, file), 'utf8'));
      if (!ticks.length) continue;
      have.push(day);
      const c = coverage(ticks);
      if (c != null) cov.push(c);
      trades.push(...classify(ticks));
    } catch {
      /* this instrument was not captured that day */
    }
  }
  return NextResponse.json({
    key,
    days: have,
    trades: trades.slice(-MAX_TRADES),
    /** Mean share of each day's volume the sampled ticks account for; null when unknown. */
    coverage: cov.length ? cov.reduce((a, b) => a + b, 0) / cov.length : null,
  });
}
