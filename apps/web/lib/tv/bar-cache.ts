import type { Bar } from './types';

/**
 * Pure helpers for the local futures bar cache that `research/backtests/yahoo_futures_history.py` writes
 * (`.cache_yahoo_futures/<SYMBOL>/<interval>/<YYYY-MM>.csv`, columns time,open,high,low,close,volume).
 * The file reading lives in the bars route; everything here is testable without a disk.
 */

/** Chart interval → the folder the recorder writes it to (`1h` is stored as `60m`). */
const CACHE_INTERVALS: Record<string, string> = { '1m': '1m', '5m': '5m', '15m': '15m', '60m': '60m', '1h': '60m', '1d': '1d' };

export function cacheInterval(interval: string): string | null {
  return CACHE_INTERVALS[interval] ?? null;
}

/** `NQ=F` → `NQ_F`, the recorder's folder name. Null for anything that is not a plain ticker. */
export function cacheSymbolDir(symbol: string): string | null {
  return /^[A-Z0-9^.=-]{1,16}$/i.test(symbol) && !symbol.includes('..') ? symbol.toUpperCase().replace(/=/g, '_') : null;
}

/** The `YYYY-MM` (UTC) month files that can hold bars between `from` and `to` (unix seconds), oldest first. */
export function monthsBetween(from: number, to: number): string[] {
  const out: string[] = [];
  const d = new Date(Math.max(0, from) * 1000);
  let y = d.getUTCFullYear();
  let m = d.getUTCMonth();
  const end = new Date(to * 1000);
  const endKey = end.getUTCFullYear() * 12 + end.getUTCMonth();
  while (y * 12 + m <= endKey && out.length < 240) {
    out.push(`${y}-${String(m + 1).padStart(2, '0')}`);
    m += 1;
    if (m === 12) {
      m = 0;
      y += 1;
    }
  }
  return out;
}

/** One month file → bars, skipping malformed rows. */
export function parseCacheCsv(text: string): Bar[] {
  const lines = text.split(/\r?\n/);
  const head = (lines.shift() ?? '').split(',');
  const at = (name: string) => head.indexOf(name);
  const [it, io, ih, il, ic, iv] = ['time', 'open', 'high', 'low', 'close', 'volume'].map(at);
  if ([it, io, ih, il, ic].some((i) => i! < 0)) return [];
  const bars: Bar[] = [];
  for (const line of lines) {
    if (!line) continue;
    const c = line.split(',');
    const bar: Bar = {
      time: Number(c[it!]),
      open: Number(c[io!]),
      high: Number(c[ih!]),
      low: Number(c[il!]),
      close: Number(c[ic!]),
      volume: iv! >= 0 ? Number(c[iv!]) || 0 : 0,
    };
    if ([bar.time, bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)) bars.push(bar);
  }
  return bars;
}

/** A Yahoo futures ticker the recorder may be asked to save (`NQ=F`, `MNQ=F`, `GC=F` …). */
export function isRecordableFuture(symbol: string): boolean {
  return /^[A-Z0-9]{1,6}=F$/i.test(symbol);
}

/** Yahoo keeps 1-minute futures bars this long; a gap between two saves longer than this loses minutes for good. */
export const ONE_MINUTE_KEEP_DAYS = 28;

interface ManifestInterval {
  bars?: number;
  first?: string;
  last?: string;
  last_run?: string;
}
export interface CacheManifest {
  symbol?: string;
  intervals?: Record<string, ManifestInterval>;
}

export interface HistorySummary {
  /** Nothing saved yet for this symbol. */
  empty: boolean;
  /** ISO dates (YYYY-MM-DD) of the oldest saved bar, per interval. */
  since: Record<string, string>;
  /** ms epoch of the last save, null if never. */
  lastRun: number | null;
  /** Days since the last save, null if never. */
  ageDays: number | null;
  /** ok: fine · soon: save within a week or two · late: 1-minute bars are falling out of Yahoo's window unsaved. */
  level: 'ok' | 'soon' | 'late';
}

/** What the chart's History chip shows, from the recorder's manifest.json. */
export function historySummary(manifest: CacheManifest | null, nowMs: number): HistorySummary {
  const ivs = manifest?.intervals ?? {};
  const since: Record<string, string> = {};
  let lastRun: number | null = null;
  for (const [iv, info] of Object.entries(ivs)) {
    if (info.first) since[iv] = info.first.slice(0, 10);
    const run = info.last_run ? Date.parse(info.last_run) : NaN;
    if (Number.isFinite(run) && (lastRun == null || run > lastRun)) lastRun = run;
  }
  const ageDays = lastRun == null ? null : (nowMs - lastRun) / 86_400_000;
  const level =
    ageDays == null || ageDays > ONE_MINUTE_KEEP_DAYS - 3 ? 'late' : ageDays > ONE_MINUTE_KEEP_DAYS / 2 ? 'soon' : 'ok';
  return { empty: Object.keys(since).length === 0, since, lastRun, ageDays, level };
}

/**
 * Cached bars + freshly fetched bars as one ascending series. A live bar replaces the cached bar at the same
 * time (Yahoo's newest copy wins, so a candle that was still forming when it was cached is corrected). Unlike
 * `timeframes.mergeBars`, cached bars inside the live span are KEPT, so a hole in Yahoo's answer stays filled.
 */
export function unionBars(cached: Bar[], live: Bar[]): Bar[] {
  if (!cached.length) return live;
  const byTime = new Map<number, Bar>();
  for (const b of cached) byTime.set(b.time, b);
  for (const b of live) byTime.set(b.time, b);
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
