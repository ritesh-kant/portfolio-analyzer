'use client';

import { useCallback, useEffect, useState } from 'react';
import { isRecordableFuture, ONE_MINUTE_KEEP_DAYS, type HistorySummary } from '../../lib/tv/bar-cache';

/**
 * Bottom-bar chip for Yahoo futures (NQ=F …): how much history is saved on this computer, and an Update button
 * that runs the recorder (POST /api/chart/history). Hidden for other symbols and when the app is not running
 * locally (the API says `available: false`).
 */
interface Status extends HistorySummary {
  available: boolean;
  run?: { ok: boolean; output: string };
}

const COLORS = { ok: '#089981', soon: '#ffb74d', late: '#f23645' } as const;

function day(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(d);
}

function agoText(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 2) return 'just now';
  if (m < 90) return `${m} min ago`;
  const h = m / 60;
  if (h < 36) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/** "NQ=F     1m  fetched  28669  +7 …" → { '1m': 7, … } */
function newBars(output: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const m of output.matchAll(/^\S+\s+(\w+)\s+fetched\s+\d+\s+\+(\d+)/gm)) out[m[1]!] = Number(m[2]);
  return out;
}

export function HistoryChip({ symbol }: { symbol: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean; detail?: string } | null>(null);
  const eligible = isRecordableFuture(symbol);

  useEffect(() => {
    setStatus(null);
    setNote(null);
    if (!eligible) return;
    const ctl = new AbortController();
    fetch(`/api/chart/history?symbol=${encodeURIComponent(symbol)}`, { signal: ctl.signal, cache: 'no-store' })
      .then((r) => (r.ok ? (r.json() as Promise<Status>) : null))
      .then((s) => s && setStatus(s))
      .catch(() => {});
    return () => ctl.abort();
  }, [symbol, eligible]);

  // Clear the "Saved" note after a while so the chip goes back to the status line.
  useEffect(() => {
    if (!note?.ok) return;
    const t = setTimeout(() => setNote(null), 8000);
    return () => clearTimeout(t);
  }, [note]);

  const update = useCallback(async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await fetch('/api/chart/history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      });
      const s = (await r.json()) as Status & { error?: string };
      if (s.available !== undefined) setStatus(s);
      if (!r.ok) {
        setNote({ text: 'Save failed', ok: false, detail: s.error ?? s.run?.output ?? `HTTP ${r.status}` });
      } else {
        const added = newBars(s.run?.output ?? '');
        const oneMin = added['1m'] ?? 0;
        const total = Object.values(added).reduce((a, b) => a + b, 0);
        setNote({
          text: total ? `Saved · ${oneMin.toLocaleString()} new 1-min bars` : 'Saved · already up to date',
          ok: true,
          detail: s.run?.output,
        });
      }
    } catch (e) {
      setNote({ text: 'Save failed', ok: false, detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }, [symbol]);

  if (!eligible || !status?.available) return null;

  const level = status.level;
  const since = status.since['1m'];
  const label = status.empty
    ? 'History: nothing saved yet'
    : `History: 1-min since ${since ? day(since) : '—'} · saved ${status.lastRun ? agoText(Date.now() - status.lastRun) : 'never'}`;
  const warn =
    level === 'late'
      ? ' · save now, older 1-min bars are being lost'
      : level === 'soon'
        ? ' · save soon'
        : '';
  const title = [
    `Yahoo keeps 1-minute ${symbol} bars for only ${ONE_MINUTE_KEEP_DAYS} days (5-minute for ~70 days).`,
    'Update copies everything Yahoo still has to this computer, so the chart and backtests keep it after Yahoo drops it.',
    `Save at least every ${ONE_MINUTE_KEEP_DAYS} days; weekly is safe. Same as running "pnpm bt:nq".`,
    status.empty ? '' : `Saved from: ${Object.entries(status.since).map(([iv, d]) => `${iv} ${d}`).join(' · ')}`,
  ]
    .filter(Boolean)
    .join('\n');

  return (
    <span className="mr-3 flex items-center gap-1.5 text-[12px] tabular-nums" title={note?.detail ?? title}>
      <span style={{ color: busy ? '#2962ff' : note ? (note.ok ? COLORS.ok : COLORS.late) : COLORS[level] }}>●</span>
      <span className={`whitespace-nowrap ${level === 'ok' || note ? 'text-[#b2b5be]' : ''}`} style={!note && level !== 'ok' ? { color: COLORS[level] } : undefined}>
        {busy ? 'Saving history…' : note ? note.text : `${label}${warn}`}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={update}
        className="rounded border border-[#363a45] px-1.5 py-0.5 text-[11px] font-medium text-[#b2b5be] transition-colors hover:bg-[#2a2e39] hover:text-white disabled:opacity-50"
        title={`Save ${symbol} history now`}
      >
        {busy ? '…' : status.empty ? 'Save' : 'Update'}
      </button>
    </span>
  );
}
