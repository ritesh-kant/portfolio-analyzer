import { NextResponse } from 'next/server';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { classify, coverage, parseTicks, type Trade } from '../../../../lib/tv/bigtrades';
import { DAY_DIR, resolveTickKey, tickFileName, TICKS_DIR as DIR } from '../../../../lib/tv/ticks.server';
import { packSteps, volumeSteps, type PackedStep } from '../../../../lib/tv/tick-footprint';

/**
 * Sampled trade tape for the chart's "Big Trades" bubbles, read from the files that
 * `apps/signal-engine/scripts/capture_ticks.py` writes (`data/ticks/<IST day>/<key>.jsonl`).
 *
 * `?view=steps` returns the volume between consecutive feed updates instead (lib/tv/tick-footprint.ts), which the
 * footprint uses for NSE futures.
 *
 * Local only: it reads the repo's disk, so it returns nothing on Vercel. Putting the tape in Mongo behind
 * portfolio-service is the production step. See research/big-trades-bubbles-plan.md.
 */
export const dynamic = 'force-dynamic';

/** Most recent capture days returned, and a cap on trades so a busy stock cannot flood the browser. */
const MAX_DAYS = 3;
const MAX_TRADES = 60_000;

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const symbol = (searchParams.get('symbol') ?? '').trim();
  const key = await resolveTickKey(symbol, searchParams.get('key')).catch(() => undefined);
  if (!key) return NextResponse.json({ key: null, days: [], trades: [], coverage: null, steps: [], segments: [] });

  const file = tickFileName(key);
  let days: string[] = [];
  try {
    days = (await fs.readdir(DIR)).filter((d) => DAY_DIR.test(d)).sort().slice(-MAX_DAYS);
  } catch {
    /* no capture has ever run */
  }
  const stepsView = searchParams.get('view') === 'steps';
  const trades: Trade[] = [];
  const steps: PackedStep[] = [];
  const segments: [number, number][] = [];
  const have: string[] = [];
  const cov: number[] = [];
  for (const day of days) {
    try {
      const ticks = parseTicks(await fs.readFile(path.join(DIR, day, file), 'utf8'));
      if (!ticks.length) continue;
      have.push(day);
      if (stepsView) {
        // Each day file on its own: the day-volume counter restarts every session.
        const series = volumeSteps(ticks);
        steps.push(...packSteps(series.steps));
        segments.push(...series.segments);
        continue;
      }
      const c = coverage(ticks);
      if (c != null) cov.push(c);
      trades.push(...classify(ticks));
    } catch {
      /* this instrument was not captured that day */
    }
  }
  if (stepsView) return NextResponse.json({ key, days: have, steps, segments });
  return NextResponse.json({
    key,
    days: have,
    trades: trades.slice(-MAX_TRADES),
    /** Mean share of each day's volume the sampled ticks account for; null when unknown. */
    coverage: cov.length ? cov.reduce((a, b) => a + b, 0) / cov.length : null,
  });
}
