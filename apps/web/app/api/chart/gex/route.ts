import { readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextResponse } from 'next/server';
import type { GexConvention, GexResponse } from '../../../../lib/tv/gex';
import { NO_OPEN_INTEREST, noGex as none, usLevels } from '../../../../lib/tv/gex-us';
import { isNseSymbol, nseLevels } from '../../../../lib/tv/gex-nse';

/**
 * Gamma-exposure levels for a chart symbol, as a snapshot of *current* open interest: it says nothing
 * about where the levels were on earlier candles. Cached a minute so chart refreshes do not hammer the
 * source.
 *  - NSE (indices, F&O stocks, futures): Upstox's option chain, with `UPSTOX_ANALYTICS_TOKEN` (read-only,
 *    server-side only), computed in lib/tv/gex-nse.ts (shared with the snapshot recorder `pnpm bt:gex`).
 *  - US (stocks, ETFs, ^SPX/^NDX/^RUT, and futures via an index/ETF proxy): Yahoo's option chain, ~15 minutes
 *    delayed, computed in lib/tv/gex-us.ts (shared with the snapshot recorder `pnpm bt:gex`).
 */
export const dynamic = 'force-dynamic';

const TTL_MS = 60_000;

const CONVENTIONS: GexConvention[] = ['dealer_long_calls', 'dealer_short_all'];

const cache = new Map<string, { at: number; body: GexResponse }>();

/**
 * The last full US snapshot per symbol, on disk so it survives a restart: served (marked `stale`) while Yahoo
 * has blanked open interest before the US open, instead of levels built from a few leftover strikes.
 */
const LAST_GOOD_MAX_AGE_S = 4 * 86_400;
const lastGoodPath = (key: string) => join(tmpdir(), `gex-last-${key.replace(/[^A-Za-z0-9|_-]/g, '_').replace(/\|/g, '-')}.json`);
async function saveLastGood(key: string, body: GexResponse) {
  try {
    await writeFile(lastGoodPath(key), JSON.stringify(body));
  } catch {
    /* read-only disk: no fallback, nothing else lost */
  }
}
async function loadLastGood(key: string): Promise<GexResponse | null> {
  try {
    const body = JSON.parse(await readFile(lastGoodPath(key), 'utf8')) as GexResponse;
    const age = Date.now() / 1000 - (body.asOf ?? 0);
    return body.supported && age < LAST_GOOD_MAX_AGE_S ? body : null;
  } catch {
    return null;
  }
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
      let body = await usLevels(symbol, days, convention);
      if (body.supported) await saveLastGood(cacheKey, body);
      else if (body.reason === NO_OPEN_INTEREST) {
        const old = await loadLastGood(cacheKey);
        if (old) body = { ...old, stale: NO_OPEN_INTEREST };
      }
      if (body.supported) cache.set(cacheKey, { at: Date.now(), body });
      return NextResponse.json(body);
    } catch (err) {
      return NextResponse.json(none(err instanceof Error ? err.message : 'option chain request failed'));
    }
  }

  const token = process.env.UPSTOX_ANALYTICS_TOKEN;
  if (!token) return NextResponse.json(none('UPSTOX_ANALYTICS_TOKEN is not set for the web app'));
  try {
    const body = await nseLevels(symbol, days, convention, token);
    if (body.supported) cache.set(cacheKey, { at: Date.now(), body });
    return NextResponse.json(body);
  } catch (err) {
    return NextResponse.json(none(err instanceof Error ? err.message : 'option chain request failed'));
  }
}
