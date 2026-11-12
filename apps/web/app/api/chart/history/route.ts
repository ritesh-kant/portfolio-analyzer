import { NextResponse } from 'next/server';
import { execFile } from 'node:child_process';
import { promises as fs } from 'node:fs';
import { cacheSymbolDir, historySummary, isRecordableFuture } from '../../../../lib/tv/bar-cache';
import { FUTURES_CACHE, RECORDER_SCRIPT, readManifest } from '../../../../lib/tv/futures-cache.server';

/**
 * The chart's "History" chip for Yahoo futures (NQ=F …).
 *   GET  ?symbol=NQ=F → what the local bar cache holds and when it was last saved
 *   POST { symbol }   → run the recorder (research/backtests/yahoo_futures_history.py) for that symbol, then GET
 *
 * Local only: it needs the repo on disk and python3, so on Vercel `available` is false and POST refuses.
 * Behind the app's sign-in like every /api route (middleware.ts).
 */
export const dynamic = 'force-dynamic';

const PYTHON = process.env.FUTURES_RECORDER_PYTHON ?? 'python3';
const RUN_TIMEOUT_MS = 180_000;
/** One save at a time: a second click while one runs waits for the same result. */
let running: Promise<{ ok: boolean; output: string }> | null = null;

async function available(): Promise<boolean> {
  if (process.env.VERCEL) return false;
  return fs.access(RECORDER_SCRIPT).then(
    () => true,
    () => false,
  );
}

async function status(symbol: string) {
  const dir = cacheSymbolDir(symbol)!;
  return { symbol, available: await available(), ...historySummary(await readManifest(dir), Date.now()) };
}

function readSymbol(raw: unknown): string | null {
  const s = typeof raw === 'string' ? raw.trim().toUpperCase() : '';
  return isRecordableFuture(s) && cacheSymbolDir(s) ? s : null;
}

export async function GET(req: Request) {
  const symbol = readSymbol(new URL(req.url).searchParams.get('symbol'));
  if (!symbol) return NextResponse.json({ error: 'not a Yahoo futures symbol (e.g. NQ=F)' }, { status: 400 });
  return NextResponse.json(await status(symbol), { headers: { 'Cache-Control': 'no-store' } });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { symbol?: unknown };
  const symbol = readSymbol(body.symbol);
  if (!symbol) return NextResponse.json({ error: 'not a Yahoo futures symbol (e.g. NQ=F)' }, { status: 400 });
  if (!(await available())) {
    return NextResponse.json({ error: 'saving history only works when the app runs locally from the repo' }, { status: 501 });
  }
  running ??= new Promise((resolve) => {
    // Fixed script, arguments as an array (no shell), symbol already validated.
    execFile(
      PYTHON,
      ['-I', RECORDER_SCRIPT, '--symbols', symbol, '--cache', FUTURES_CACHE],
      { timeout: RUN_TIMEOUT_MS, maxBuffer: 1 << 20 },
      (err, stdout, stderr) => resolve({ ok: !err, output: `${stdout}${stderr}`.trim() }),
    );
  }).finally(() => {
    running = null;
  }) as Promise<{ ok: boolean; output: string }>;
  const run = await running;
  return NextResponse.json({ ...(await status(symbol)), run }, { status: run.ok ? 200 : 502 });
}
