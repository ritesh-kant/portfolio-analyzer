'use client';

import { useCallback, useEffect, useState } from 'react';
import { chipState, RECORD_UNTIL, type ChipState, type TickRecordStatus } from '../../lib/tv/tick-recorder';
import { isNseFuture } from './use-tick-steps';

/**
 * Bottom-bar chip for NSE futures: is the live feed being recorded (it feeds the footprint, see
 * lib/tv/tick-footprint.ts), with a button that starts or stops the recorder (/api/chart/ticks/record). Hidden for
 * other symbols and when the app is not running locally.
 */

const COLOR: Record<ChipState, string> = {
  recording: '#089981',
  waiting: '#2962ff',
  stalled: '#f23645',
  idle: '#ffb74d',
  closed: '#787b86',
};

function dayLabel(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${iso}T00:00:00Z`),
  );
}

export function TicksChip({ symbol }: { symbol: string }) {
  const [status, setStatus] = useState<TickRecordStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eligible = isNseFuture(symbol);

  useEffect(() => {
    setStatus(null);
    setError(null);
    if (!eligible) return;
    let dead = false;
    const pull = () =>
      fetch(`/api/chart/ticks/record?symbol=${encodeURIComponent(symbol)}`, { cache: 'no-store' })
        .then((r) => (r.ok ? (r.json() as Promise<TickRecordStatus>) : null))
        .then((s) => {
          if (s && !dead) setStatus(s);
        })
        .catch(() => {});
    void pull();
    const id = window.setInterval(pull, 15_000);
    return () => {
      dead = true;
      window.clearInterval(id);
    };
  }, [symbol, eligible]);

  const act = useCallback(
    async (method: 'POST' | 'DELETE') => {
      setBusy(true);
      setError(null);
      try {
        const r = await fetch('/api/chart/ticks/record', {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol }),
        });
        const s = (await r.json()) as TickRecordStatus & { error?: string };
        if (s.clock) setStatus(s);
        if (!r.ok) setError(s.error ?? `HTTP ${r.status}`);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [symbol],
  );

  if (!eligible || !status?.available || !status.key) return null;

  const state = chipState(status);
  const n = status.updatesToday.toLocaleString();
  const label =
    state === 'recording'
      ? `Ticks: recording · ${n} updates today`
      : state === 'waiting'
        ? 'Ticks: recorder ready, market opens 09:15'
        : state === 'stalled'
          ? 'Ticks: recorder running but no data — see tooltip'
          : state === 'idle'
            ? status.updatesToday
              ? `Ticks: stopped · ${n} updates today`
              : 'Ticks: not recording today'
            : status.days.length
              ? `Ticks: ${status.days.length} day${status.days.length === 1 ? '' : 's'} recorded · last ${dayLabel(status.days[status.days.length - 1]!)}`
              : 'Ticks: none recorded';
  const title = [
    'Records the live Upstox feed for this contract so the footprint uses real volume (exact per ~1 s, blue box)',
    'instead of the estimate from 1-minute bars. It only covers the time it was running: start it before 09:15',
    `to get the whole session. It stops by itself at ${RECORD_UNTIL}. Needs a valid Upstox token in the repo .env.`,
    status.days.length ? `Recorded: ${status.days.join(', ')}` : '',
    error ? `\nError: ${error}` : '',
    status.log && (state === 'stalled' || error || state === 'idle') ? `\nRecorder log:\n${status.log}` : '',
  ]
    .filter(Boolean)
    .join('\n');
  const canStart = (state === 'idle' || state === 'stalled') && status.clock.canRecord && !status.running;
  const canStop = status.running;

  return (
    <span className="mr-3 flex items-center gap-1.5 text-[12px] tabular-nums" title={title}>
      <span style={{ color: error ? '#f23645' : COLOR[state] }}>●</span>
      <span className="whitespace-nowrap text-[#b2b5be]">{error ? `Ticks: ${error}` : label}</span>
      {canStart || canStop ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => void act(canStop ? 'DELETE' : 'POST')}
          className="rounded border border-[#363a45] px-1.5 py-0.5 text-[11px] font-medium text-[#b2b5be] transition-colors hover:bg-[#2a2e39] hover:text-white disabled:opacity-50"
          title={canStop ? 'Stop recording this contract' : `Record this contract's feed until ${RECORD_UNTIL}`}
        >
          {busy ? '…' : canStop ? 'Stop' : `Record till ${RECORD_UNTIL}`}
        </button>
      ) : null}
    </span>
  );
}
