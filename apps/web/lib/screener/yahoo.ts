import type { Operand, YahooQuote } from './query.ts';
import { PAGE } from './run.ts';
import type { Bar } from '../tv/types.ts';
import { pocSetup, type PocSetup } from './poc-retest.ts';
import { relVolAtTime, zoneClock } from './relvol.ts';

/**
 * Server-side Yahoo Finance client for the screener. Server only: it holds a session cookie + crumb,
 * which Yahoo requires for the screener and quoteSummary endpoints (the chart endpoint does not).
 */

// Yahoo answers 429 to a long browser UA on the crumb call; the plain token works (as in /api/chart/*).
const UA = 'Mozilla/5.0';
const HOSTS = ['query1.finance.yahoo.com', 'query2.finance.yahoo.com'];
const TIMEOUT_MS = 8000;

class AuthError extends Error {}

// ── cookie + crumb ──────────────────────────────────────────────────────────

interface Session {
  cookie: string;
  crumb: string;
  at: number;
}
let session: Session | null = null;
let pending: Promise<Session> | null = null;
const SESSION_TTL_MS = 25 * 60_000;

async function newSession(): Promise<Session> {
  // fc.yahoo.com answers 404 but sets the consent cookie we need.
  const first = await fetch('https://fc.yahoo.com', {
    headers: { 'User-Agent': UA },
    redirect: 'manual',
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const cookie = first.headers
    .getSetCookie()
    .map((c) => c.split(';')[0])
    .join('; ');
  const res = await fetch(`https://${HOSTS[0]}/v1/test/getcrumb`, {
    headers: { 'User-Agent': UA, Cookie: cookie },
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const crumb = (await res.text()).trim();
  if (!res.ok || !crumb || crumb.includes('<') || /too many/i.test(crumb)) {
    throw new Error(`Yahoo crumb unavailable (HTTP ${res.status})`);
  }
  return { cookie, crumb, at: Date.now() };
}

/** After a failed handshake, wait before trying again: Yahoo answers a burst of new sessions with 429. */
const RETRY_AFTER_FAILURE_MS = 20_000;
let failure: { at: number; error: Error } | null = null;

async function getSession(force = false): Promise<Session> {
  if (!force && session && Date.now() - session.at < SESSION_TTL_MS) return session;
  if (failure && Date.now() - failure.at < RETRY_AFTER_FAILURE_MS) throw failure.error;
  // One refresh at a time: a burst of parallel requests must not each open their own session.
  pending ??= newSession()
    .then((s) => {
      failure = null;
      return (session = s);
    })
    .catch((e: unknown) => {
      failure = { at: Date.now(), error: e instanceof Error ? e : new Error(String(e)) };
      throw failure.error;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** Runs `fn` with a session; if Yahoo says the crumb is stale (401/403) opens a fresh one and retries once. */
async function withSession<T>(fn: (s: Session) => Promise<T>): Promise<T> {
  try {
    return await fn(await getSession());
  } catch (e) {
    if (!(e instanceof AuthError)) throw e;
    return fn(await getSession(true));
  }
}

async function call(url: string, s: Session, init?: RequestInit): Promise<unknown> {
  const res = await fetch(url, {
    ...init,
    headers: { 'User-Agent': UA, Cookie: s.cookie, ...(init?.headers as Record<string, string>) },
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (res.status === 401 || res.status === 403) throw new AuthError(`Yahoo ${res.status}`);
  if (!res.ok) throw new Error(`Yahoo HTTP ${res.status}`);
  return res.json();
}

// ── screener ────────────────────────────────────────────────────────────────

export interface ScreenPage {
  quotes: YahooQuote[];
  /** How many names matched in total, across all pages. */
  total: number;
}

export async function screenPage(
  query: Operand,
  sortField: string,
  sortType: 'ASC' | 'DESC',
  offset: number,
): Promise<ScreenPage> {
  const body = JSON.stringify({
    size: PAGE,
    offset,
    sortField,
    sortType,
    quoteType: 'EQUITY',
    topOperator: 'AND',
    query,
    userId: '',
    userIdType: 'guid',
  });
  return withSession(async (s) => {
    const url = `https://${HOSTS[0]}/v1/finance/screener?crumb=${encodeURIComponent(s.crumb)}&lang=en-US&region=US&formatted=false`;
    const json = (await call(url, s, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })) as {
      finance?: {
        result?: Array<{ quotes?: YahooQuote[]; total?: number }> | null;
        error?: { description?: string } | null;
      };
    };
    const result = json.finance?.result?.[0];
    if (!result)
      throw new Error(json.finance?.error?.description ?? 'Yahoo screener returned nothing');
    return { quotes: result.quotes ?? [], total: result.total ?? 0 };
  });
}

// ── profile (float, sector, short interest …) ───────────────────────────────

export interface Profile {
  float: number | null;
  sector: string | null;
  industry: string | null;
  shortFloat: number | null;
  insiders: number | null;
  institutions: number | null;
  beta: number | null;
  peg: number | null;
  revGrowth: number | null;
  roe: number | null;
}

const raw = (o: unknown): number | null => {
  const v = (o as { raw?: unknown } | undefined)?.raw;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
};
const pct = (n: number | null) => (n == null ? null : n * 100);
const str = (o: unknown): string | null => (typeof o === 'string' && o ? o : null);

export function parseProfile(result: Record<string, Record<string, unknown>> | undefined): Profile {
  const ks = result?.defaultKeyStatistics ?? {};
  const fd = result?.financialData ?? {};
  const ap = result?.assetProfile ?? {};
  return {
    float: raw(ks.floatShares),
    sector: str(ap.sector),
    industry: str(ap.industry),
    shortFloat: pct(raw(ks.shortPercentOfFloat)),
    insiders: pct(raw(ks.heldPercentInsiders)),
    institutions: pct(raw(ks.heldPercentInstitutions)),
    beta: raw(ks.beta),
    peg: raw(ks.pegRatio),
    revGrowth: pct(raw(fd.revenueGrowth)),
    roe: pct(raw(fd.returnOnEquity)),
  };
}

const profileCache = new Map<string, { day: string; p: Profile }>();
const today = () => new Date().toISOString().slice(0, 10);

/** Cached for the rest of the UTC day: float and sector do not change intraday. Failed lookups are not cached. */
async function fetchProfile(symbol: string): Promise<Profile | null> {
  const hit = profileCache.get(symbol);
  if (hit && hit.day === today()) return hit.p;
  try {
    const p = await withSession(async (s) => {
      const url = `https://${HOSTS[0]}/v10/finance/quoteSummary/${encodeURIComponent(symbol)}?modules=defaultKeyStatistics,assetProfile,financialData&crumb=${encodeURIComponent(s.crumb)}`;
      const json = (await call(url, s)) as {
        quoteSummary?: { result?: Array<Record<string, Record<string, unknown>>> | null };
      };
      return parseProfile(json.quoteSummary?.result?.[0]);
    });
    profileCache.set(symbol, { day: today(), p });
    return p;
  } catch {
    return null;
  }
}

// ── intraday: 5-minute bars (rel vol at time, volume profile) ───────────────

const barsCache = new Map<string, { at: number; bars: Bar[] }>();
const BARS_TTL_MS = 60_000;
const clocks = new Map<string, ReturnType<typeof zoneClock>>();
const clockFor = (tz: string) => {
  let c = clocks.get(tz);
  if (!c) clocks.set(tz, (c = zoneClock(tz)));
  return c;
};

/**
 * One month of 5-minute regular-hours bars. Shared by every intraday column, so asking for rel vol at time
 * and the POC setup on the same name downloads it once. `null` = both hosts failed.
 */
const fetchBars5m = (symbol: string) => fetchIntraday(symbol, '5m', '1mo');

/**
 * A week of 1-minute regular-hours bars: what the chart's session volume profile is built from where it has them,
 * so the POC setup's profiles use them too. Older sessions fall back to the 5-minute bars, as on the chart.
 */
const fetchBars1m = (symbol: string) => fetchIntraday(symbol, '1m', '7d');

async function fetchIntraday(
  symbol: string,
  interval: '1m' | '5m',
  range: string,
): Promise<Bar[] | null> {
  const key = `${symbol}|${interval}`;
  const hit = barsCache.get(key);
  if (hit && Date.now() - hit.at < BARS_TTL_MS) return hit.bars;
  for (const host of HOSTS) {
    try {
      const res = await fetch(
        `https://${host}/v8/finance/chart/${encodeURIComponent(symbol)}?interval=${interval}&range=${range}&includePrePost=false`,
        {
          headers: { 'User-Agent': UA },
          cache: 'no-store',
          signal: AbortSignal.timeout(TIMEOUT_MS),
        },
      );
      if (!res.ok) continue;
      const body = (await res.json()) as {
        chart?: {
          result?: Array<{
            timestamp?: number[];
            indicators?: {
              quote?: Array<{
                open?: Array<number | null>;
                high?: Array<number | null>;
                low?: Array<number | null>;
                close?: Array<number | null>;
                volume?: Array<number | null>;
              }>;
            };
          }> | null;
        };
      };
      const r = body.chart?.result?.[0];
      const ts = r?.timestamp ?? [];
      const q = r?.indicators?.quote?.[0] ?? {};
      const bars: Bar[] = [];
      ts.forEach((t, i) => {
        const v = q.volume?.[i];
        if (typeof v !== 'number') return;
        const close = q.close?.[i];
        const open = q.open?.[i] ?? close;
        const high = q.high?.[i] ?? close;
        const low = q.low?.[i] ?? close;
        // A volume-only row (no prices) still counts toward rel vol; it is dropped by the profile (NaN range).
        bars.push({
          time: t,
          open: open ?? NaN,
          high: high ?? NaN,
          low: low ?? NaN,
          close: close ?? NaN,
          volume: v,
        });
      });
      barsCache.set(key, { at: Date.now(), bars });
      return bars;
    } catch {
      // try the next host
    }
  }
  return null;
}

async function fetchRvolTime(symbol: string, tz: string): Promise<number | null | undefined> {
  const bars = await fetchBars5m(symbol);
  if (!bars) return undefined; // unreachable: leave it unfetched rather than claiming "no data"
  return relVolAtTime(
    bars.map((b) => ({ t: b.time, v: b.volume })),
    clockFor(tz),
  );
}

async function fetchPocSetup(symbol: string, tz: string): Promise<PocSetup | null | undefined> {
  const [bars, fine] = await Promise.all([fetchBars5m(symbol), fetchBars1m(symbol)]);
  if (!bars) return undefined;
  // without 1-minute bars every profile comes from the 5-minute ones (coarser, but still a setup)
  return pocSetup(bars, clockFor(tz), null, fine);
}

// ── enrichment ──────────────────────────────────────────────────────────────

/** Runs `fn` over `items` with at most `n` in flight. */
export async function mapPool<T, R>(
  items: readonly T[],
  n: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(n, items.length) }, async () => {
      while (next < items.length) {
        const i = next++;
        out[i] = await fn(items[i]!);
      }
    }),
  );
  return out;
}

export const POOL = 8;

export async function profiles(symbols: readonly string[]): Promise<Map<string, Profile>> {
  const got = await mapPool(symbols, POOL, fetchProfile);
  const out = new Map<string, Profile>();
  symbols.forEach((s, i) => {
    const p = got[i];
    if (p) out.set(s, p);
  });
  return out;
}

/** `undefined` for a name = the fetch failed (not "Yahoo has no volume history"). */
export async function rvolsAtTime(
  symbols: readonly string[],
  tz: string,
): Promise<Map<string, number | null>> {
  const got = await mapPool(symbols, POOL, (s) => fetchRvolTime(s, tz));
  const out = new Map<string, number | null>();
  symbols.forEach((s, i) => {
    const v = got[i];
    if (v !== undefined) out.set(s, v);
  });
  return out;
}

/** `undefined` for a name = the fetch failed; `null` = no usable previous session. */
export async function pocSetups(
  symbols: readonly string[],
  tz: string,
): Promise<Map<string, PocSetup | null>> {
  const got = await mapPool(symbols, POOL, (s) => fetchPocSetup(s, tz));
  const out = new Map<string, PocSetup | null>();
  symbols.forEach((s, i) => {
    const v = got[i];
    if (v !== undefined) out.set(s, v);
  });
  return out;
}
