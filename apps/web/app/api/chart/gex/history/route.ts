import { promises as fs } from 'node:fs';
import path from 'node:path';
import { NextResponse } from 'next/server';
import { GAMMA_SNAPSHOTS } from '../../../../../lib/tv/futures-cache.server';
import { parseSnapshots, snapshotFileName } from '../../../../../lib/tv/gamma-history';

/**
 * Recorded gamma snapshots for a symbol (`pnpm bt:gex` → research/backtests/gamma_snapshots/<SYMBOL>.jsonl), oldest
 * first, for the AR strategy's gamma regime on past candles. Empty when nothing was recorded (or on a deploy, where
 * the folder does not exist).
 */
export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  const symbol = (new URL(req.url).searchParams.get('symbol') ?? '').trim();
  const file = snapshotFileName(symbol);
  if (!file) return NextResponse.json({ snaps: [] });
  try {
    const text = await fs.readFile(path.join(GAMMA_SNAPSHOTS, file), 'utf8');
    return NextResponse.json({ snaps: parseSnapshots(text) });
  } catch {
    return NextResponse.json({ snaps: [] });
  }
}
