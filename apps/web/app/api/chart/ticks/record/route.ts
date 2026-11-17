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
import {
  isTickRecordable,
  nseClock,
  RECORD_UNTIL,
  RECORDER_LIST,
  STALE_MS,
  type TickRecordStatus,
} from '../../../../../lib/tv/tick-recorder';

/**
 * The chart's "Ticks" chip for NSE futures and NSE stocks.
 *   GET    ?symbol=TCS.NS      → is the feed being recorded, how much today, which days exist
 *   POST   { symbol }          → add it to today's recording list; start the shared recorder if it is not running
 *   DELETE { symbol }          → take it off the list (the recorder exits once the list is empty)
 *
 * ONE recorder process (capture_ticks.py --watch data/ticks/<day>/recorder.json) records every symbol on one Upstox
 * websocket: a normal account gets two connections, so a process per symbol would cap recording at two symbols.
 * The recorder re-reads the list every 3 s and subscribes / unsubscribes on the same connection.
 *
 * Local only: it needs the repo, the signal-engine venv and an Upstox token in the repo .env (the recorder reads
 * it itself; nothing here touches it). On Vercel `available` is false and POST refuses.
 * The recorder runs detached, so it survives a dev-server reload; its pid is kept next to the data.
 */
export const dynamic = 'force-dynamic';

const NOT_RECORDABLE = 'not an NSE future or stock (e.g. NIFTY1!.NFO, TCS.NS)';
/** Per-symbol recorders from before the shared one: still honoured (status, Stop) if one is running. */
const pidFile = (day: string, key: string) => path.join(TICKS_DIR, day, `.${tickFileName(key)}.pid`);
const logFile = (day: string, key: string) => path.join(TICKS_DIR, day, `${tickFileName(key)}.log`);
const listFile = (day: string) => path.join(TICKS_DIR, day, RECORDER_LIST);
const sharedPidFile = (day: string) => path.join(TICKS_DIR, day, '.recorder.pid');
const sharedLogFile = (day: string) => path.join(TICKS_DIR, day, 'recorder.log');

async function readList(day: string): Promise<string[]> {
  try {
    const keys = (JSON.parse(await fs.readFile(listFile(day), 'utf8')) as { keys?: unknown }).keys;
    return Array.isArray(keys) ? keys.filter((k): k is string => typeof k === 'string') : [];
  } catch {
    return [];
  }
}

async function writeList(day: string, keys: string[]): Promise<void> {
  await fs.mkdir(path.join(TICKS_DIR, day), { recursive: true });
  // Write then rename, so the recorder never reads a half-written list.
  const tmp = `${listFile(day)}.tmp`;
  await fs.writeFile(tmp, JSON.stringify({ keys: [...new Set(keys)] }));
  await fs.rename(tmp, listFile(day));
}

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

/** `pid` if that process is alive and still the tick recorder (pids get reused). */
async function liveRecorder(raw: unknown): Promise<number | null> {
  const pid = Number(raw);
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

/** A per-symbol recorder (the old way) running for this key. */
const runningPid = async (day: string, key: string) => liveRecorder((await readPidFile(day, key)).pid);

/** The shared recorder for `day`, if it is running. */
async function sharedPid(day: string): Promise<number | null> {
  try {
    return await liveRecorder((JSON.parse(await fs.readFile(sharedPidFile(day), 'utf8')) as { pid?: number }).pid);
  } catch {
    return null;
  }
}

async function tail(file: string): Promise<string> {
  try {
    return (await fs.readFile(file, 'utf8')).slice(-600).trim();
  } catch {
    return '';
  }
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
  const listed = (await readList(clock.day)).includes(key);
  base.running =
    (await runningPid(clock.day, key)) != null || (listed && (await sharedPid(clock.day)) != null);
  base.log = (await tail(sharedLogFile(clock.day))) || (await tail(logFile(clock.day, key)));
  return base;
}

async function readKey(raw: unknown): Promise<string | null> {
  const s = typeof raw === 'string' ? raw.trim() : '';
  if (!s || !isTickRecordable(s)) return null;
  return (await resolveTickKey(s).catch(() => undefined)) ?? null;
}

export async function GET(req: Request) {
  const symbol = new URL(req.url).searchParams.get('symbol') ?? '';
  if (!isTickRecordable(symbol)) return NextResponse.json({ error: NOT_RECORDABLE }, { status: 400 });
  return NextResponse.json(await status(await readKey(symbol)), { headers: { 'Cache-Control': 'no-store' } });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { symbol?: unknown };
  const key = await readKey(body.symbol);
  if (!key) return NextResponse.json({ error: NOT_RECORDABLE }, { status: 400 });
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
  // The running recorder picks the new key up within 3 s; otherwise start it with the whole list.
  await writeList(clock.day, [...(await readList(clock.day)), key]);
  if (await sharedPid(clock.day)) return NextResponse.json(await status(key));
  const out = openSync(sharedLogFile(clock.day), 'a');
  try {
    // Fixed script, arguments as an array (no shell), keys validated by resolveTickKey.
    const child = spawn(ENGINE_PYTHON, [TICK_RECORDER, '--watch', listFile(clock.day), '--until', RECORD_UNTIL], {
      cwd: ENGINE_DIR,
      detached: true,
      stdio: ['ignore', out, out],
    });
    if (!child.pid) throw new Error('recorder did not start');
    await fs.writeFile(
      sharedPidFile(clock.day),
      JSON.stringify({ pid: child.pid, until: RECORD_UNTIL, startedAt: new Date().toISOString() }),
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
  if (!key) return NextResponse.json({ error: NOT_RECORDABLE }, { status: 400 });
  const day = nseClock(Date.now()).day;
  const pid = await runningPid(day, key);
  // SIGINT: the recorder treats it like Ctrl-C and closes the stream and its files cleanly.
  if (pid) process.kill(pid, 'SIGINT');
  // The shared recorder unsubscribes within 3 s, and exits when nothing is left on the list.
  await writeList(day, (await readList(day)).filter((k) => k !== key));
  await fs.writeFile(pidFile(day, key), JSON.stringify({ stoppedAt: Date.now() }));
  return NextResponse.json(await status(key));
}
