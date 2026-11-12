import { NextResponse } from 'next/server';
import { execFile, spawn } from 'node:child_process';
import { promises as fs, openSync, closeSync } from 'node:fs';
import path from 'node:path';
import {
  DAY_DIR,
  ENGINE_DIR,
  ENGINE_PYTHON,
  TICK_RECORDER,
  TICKS_DIR,
  resolveTickKey,
  tickFileName,
} from '../../../../../lib/tv/ticks.server';
import { nseClock, RECORD_UNTIL, STALE_MS, type TickRecordStatus } from '../../../../../lib/tv/tick-recorder';

/**
 * The chart's "Ticks" chip for NSE futures.
 *   GET    ?symbol=NIFTY1!.NFO → is the feed being recorded, how much today, which days exist
 *   POST   { symbol }          → start capture_ticks.py for that contract in the background, until 15:30 IST
 *   DELETE { symbol }          → stop a recorder this app started
 *
 * Local only: it needs the repo, the signal-engine venv and an Upstox token in the repo .env (the recorder reads
 * it itself; nothing here touches it). On Vercel `available` is false and POST refuses.
 * The recorder runs detached, so it survives a dev-server reload; its pid is kept next to the data.
 */
export const dynamic = 'force-dynamic';

const isFuture = (s: string) => /\.NFO$/i.test(s) || /1!$/.test(s);
const pidFile = (day: string, key: string) => path.join(TICKS_DIR, day, `.${tickFileName(key)}.pid`);
const logFile = (day: string, key: string) => path.join(TICKS_DIR, day, `${tickFileName(key)}.log`);

async function available(): Promise<boolean> {
  if (process.env.VERCEL) return false;
  try {
    await fs.access(TICK_RECORDER);
    await fs.access(ENGINE_PYTHON);
    return true;
  } catch {
    return false;
  }
}

async function readPidFile(day: string, key: string): Promise<{ pid?: number; stoppedAt?: number }> {
  try {
    return JSON.parse(await fs.readFile(pidFile(day, key), 'utf8')) as { pid?: number; stoppedAt?: number };
  } catch {
    return {};
  }
}

/** The pid in the pid file, if that process is alive and still the tick recorder (pids get reused). */
async function runningPid(day: string, key: string): Promise<number | null> {
  const pid = Number((await readPidFile(day, key)).pid);
  if (!Number.isInteger(pid) || pid <= 1) return null;
  try {
    process.kill(pid, 0);
  } catch {
    return null;
  }
  const cmd = await new Promise<string>((resolve) =>
    execFile('ps', ['-p', String(pid), '-o', 'command='], (err, out) => resolve(err ? '' : out)),
  );
  return cmd.includes('capture_ticks.py') ? pid : null;
}

async function status(key: string | null): Promise<TickRecordStatus> {
  const clock = nseClock(Date.now());
  const base: TickRecordStatus = {
    available: await available(),
    key,
    clock,
    running: false,
    fresh: false,
    updatesToday: 0,
    lastTickMs: null,
    days: [],
    log: '',
  };
  if (!key) return base;
  const file = tickFileName(key);
  try {
    const dirs = (await fs.readdir(TICKS_DIR)).filter((d) => DAY_DIR.test(d)).sort();
    for (const d of dirs) {
      if (await fs.stat(path.join(TICKS_DIR, d, file)).then((s) => s.size > 0, () => false)) base.days.push(d);
    }
  } catch {
    /* nothing recorded yet */
  }
  try {
    const text = await fs.readFile(path.join(TICKS_DIR, clock.day, file), 'utf8');
    const lines = text.split('\n').filter(Boolean);
    base.updatesToday = lines.length;
    const last = lines.length ? (JSON.parse(lines[lines.length - 1]!) as { r?: number; t?: number }) : null;
    base.lastTickMs = last?.r ?? last?.t ?? null;
    const mtime = (await fs.stat(path.join(TICKS_DIR, clock.day, file))).mtimeMs;
    // Written to in the last minute, and not before a Stop from the chip (its last lines land just before).
    const stoppedAt = (await readPidFile(clock.day, key)).stoppedAt ?? 0;
    base.fresh = Date.now() - mtime < STALE_MS && mtime > stoppedAt + 2_000;
  } catch {
    /* not recorded today */
  }
  base.running = (await runningPid(clock.day, key)) != null;
  try {
    base.log = (await fs.readFile(logFile(clock.day, key), 'utf8')).slice(-600).trim();
  } catch {
    /* never started from here today */
  }
  return base;
}

async function readKey(raw: unknown): Promise<string | null> {
  const s = typeof raw === 'string' ? raw.trim() : '';
  if (!s || !isFuture(s)) return null;
  return (await resolveTickKey(s).catch(() => undefined)) ?? null;
}

export async function GET(req: Request) {
  const symbol = new URL(req.url).searchParams.get('symbol') ?? '';
  if (!isFuture(symbol)) return NextResponse.json({ error: 'not an NSE future (e.g. NIFTY1!.NFO)' }, { status: 400 });
  return NextResponse.json(await status(await readKey(symbol)), { headers: { 'Cache-Control': 'no-store' } });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { symbol?: unknown };
  const key = await readKey(body.symbol);
  if (!key) return NextResponse.json({ error: 'not an NSE future (e.g. NIFTY1!.NFO)' }, { status: 400 });
  if (!(await available())) {
    return NextResponse.json(
      { error: 'recording only works when the app runs locally from the repo, with the signal-engine venv' },
      { status: 501 },
    );
  }
  const clock = nseClock(Date.now());
  if (!clock.canRecord) {
    return NextResponse.json({ ...(await status(key)), error: 'the NSE session is over for today' }, { status: 409 });
  }
  if (await runningPid(clock.day, key)) return NextResponse.json(await status(key));
  await fs.mkdir(path.join(TICKS_DIR, clock.day), { recursive: true });
  const out = openSync(logFile(clock.day, key), 'a');
  try {
    // Fixed script, arguments as an array (no shell), key validated by resolveTickKey.
    const child = spawn(ENGINE_PYTHON, [TICK_RECORDER, '--keys', key, '--until', RECORD_UNTIL], {
      cwd: ENGINE_DIR,
      detached: true,
      stdio: ['ignore', out, out],
    });
    if (!child.pid) throw new Error('recorder did not start');
    await fs.writeFile(
      pidFile(clock.day, key),
      JSON.stringify({ pid: child.pid, key, until: RECORD_UNTIL, startedAt: new Date().toISOString() }),
    );
    child.unref();
  } catch (e) {
    return NextResponse.json(
      { ...(await status(key)), error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  } finally {
    closeSync(out);
  }
  return NextResponse.json(await status(key));
}

export async function DELETE(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { symbol?: unknown };
  const key = await readKey(body.symbol);
  if (!key) return NextResponse.json({ error: 'not an NSE future (e.g. NIFTY1!.NFO)' }, { status: 400 });
  const day = nseClock(Date.now()).day;
  const pid = await runningPid(day, key);
  // SIGINT: the recorder treats it like Ctrl-C and closes the stream and its files cleanly.
  if (pid) process.kill(pid, 'SIGINT');
  await fs.mkdir(path.join(TICKS_DIR, day), { recursive: true });
  await fs.writeFile(pidFile(day, key), JSON.stringify({ stoppedAt: Date.now() }));
  return NextResponse.json(await status(key));
}
