'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { formatValue } from '../../lib/screener/format';
import { MARKETS, marketOf, universeFor } from '../../lib/screener/markets';
import {
  AWAY_PCT,
  APPROACH_PCT,
  LIVE_BARS,
  LOOKBACK_SESSIONS,
  PULLBACK_SHARE,
  type PocStage,
} from '../../lib/screener/poc-retest';
import type { FilterState, ScreenerResponse, ScreenerRow } from '../../lib/screener/types';
import { load, save } from '../../lib/tv/storage';
import { Icons } from './icons';
import { PANEL_WIDTH_KEY } from './panel-width';
import { addToWatchlist, watchlistSymbols } from './watchlist-panel';

const WIDTH_KEY = PANEL_WIDTH_KEY;
const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));
const VIEW_KEY = 'tv:pocScanView';
const REFRESH_MS = 60_000;

/**
 * The last scan per market and filters, kept outside the panel: it unmounts whenever another right panel opens, and
 * coming back shows these results at once instead of rescanning. They are rescanned once older than `REFRESH_MS`
 * (or on the refresh button); a scan still running when the panel closed is picked up, not started again.
 */
const scans = new Map<string, { data: ScreenerResponse; at: number }>();
const inFlight = new Map<string, Promise<ScreenerResponse>>();

function scan(body: string): Promise<ScreenerResponse> {
  const running = inFlight.get(body);
  if (running) return running;
  const p = (async () => {
    const res = await fetch('/api/screener', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const json = (await res.json()) as ScreenerResponse & { error?: string };
    if (!res.ok || json.error) throw new Error(json.error ?? `HTTP ${res.status}`);
    scans.set(body, { data: json, at: Date.now() });
    return json;
  })().finally(() => inFlight.delete(body));
  inFlight.set(body, p);
  return p;
}
const GREEN = '#26a69a';
const RED = '#ef5350';

type Side = 'both' | 'long' | 'short';
interface View {
  market: 'us' | 'in';
  side: Side;
  /** Also list names still on their way to the zone, not only the ones touching it now. */
  approaching: boolean;
  /** Also list names that have started back toward the zone from their furthest point. */
  pullback: boolean;
}
const VIEW_DEFAULTS: View = { market: 'us', side: 'both', approaching: true, pullback: true };

const SIDES: { id: Side; label: string }[] = [
  { id: 'both', label: 'Both' },
  { id: 'long', label: 'Long' },
  { id: 'short', label: 'Short' },
];

/** Liquid names only: the setup is about where big volume traded, which means nothing on a thin stock. */
function filtersFor(view: View): FilterState {
  const market = marketOf(view.market);
  const stages: PocStage[] = [];
  for (const side of ['Long', 'Short'] as const) {
    if (view.side !== 'both' && view.side !== side.toLowerCase()) continue;
    stages.push(`${side} · first touch`);
    if (view.approaching) stages.push(`${side} · approaching`);
    if (view.pullback) stages.push(`${side} · pulling back`);
  }
  return {
    ...universeFor(market),
    ...(market.id === 'us' ? { price: { min: 2 } } : {}),
    avgVolume: { min: 500_000 },
    pocStage: { in: stages },
  };
}

const COLUMNS = [
  'price',
  'changePct',
  'pocStage',
  'pocEdgePct',
  'pocEntry',
  'pocStop',
  'pocRiskPct',
];

interface Props {
  active: string;
  onOpen: (row: ScreenerRow) => void;
}

/**
 * Scans the market for the volume-profile "untested POC first retest": a recent session's point of control zone
 * that price left by ≥1% (over any number of days) and has not come back to, then the pullback to the zone's near
 * edge. Detection is `lib/screener/poc-retest.ts`, run
 * server-side by the screener API on 5-minute bars.
 */
export function PocScannerPanel({ active, onOpen }: Props) {
  const [view, setView] = useState<View>(() => ({
    ...VIEW_DEFAULTS,
    ...load<Partial<View>>(VIEW_KEY, {}),
  }));
  const body = JSON.stringify({
    market: view.market,
    filters: filtersFor(view),
    sort: { key: 'volume', dir: 'desc' },
    columns: COLUMNS,
    limit: 100,
  });
  const [data, setData] = useState<ScreenerResponse | null>(() => scans.get(body)?.data ?? null);
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>(() =>
    scans.has(body) ? 'ok' : 'loading',
  );
  const [error, setError] = useState('');
  // read after mount: the watchlist lives in localStorage, which the server render can't see
  const [watched, setWatched] = useState<Set<string>>(() => new Set());
  useEffect(() => setWatched(watchlistSymbols()), []);
  const watch = (row: ScreenerRow) => {
    addToWatchlist({ symbol: row.symbol, name: row.name });
    setWatched(watchlistSymbols());
  };
  const req = useRef(0);

  const patch = (p: Partial<View>) =>
    setView((cur) => {
      const next = { ...cur, ...p };
      save(VIEW_KEY, next);
      return next;
    });

  const run = useCallback(async () => {
    const id = ++req.current;
    setStatus('loading');
    try {
      const json = await scan(body);
      if (id !== req.current) return;
      setData(json);
      setStatus('ok');
    } catch (e) {
      if (id !== req.current) return;
      setError(e instanceof Error ? e.message : 'scan failed');
      setStatus('error');
    }
  }, [body]);

  // Show the kept scan for these settings at once; rescan only when it is missing or older than REFRESH_MS.
  useEffect(() => {
    const kept = scans.get(body);
    if (kept) {
      req.current += 1; // a slower scan for the previous settings must not overwrite this one
      setData(kept.data);
      setStatus('ok');
    } else setData(null);
    const due = () => Date.now() - (scans.get(body)?.at ?? 0) >= REFRESH_MS;
    if (due()) void run();
    const t = window.setInterval(() => {
      if (due() && !document.hidden) void run();
    }, 5_000);
    return () => window.clearInterval(t);
  }, [body, run]);

  const [width, setWidth] = useState(() => clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)));
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  widthRef.current = width;
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = widthRef.current;
    setDragging(true);
    const move = (ev: MouseEvent) => setWidth(clampWidth(startWidth + (startX - ev.clientX)));
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      setDragging(false);
      save(WIDTH_KEY, widthRef.current);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  // Live touches first, then approaches, then pullbacks; nearest to the entry first within each.
  const rows = [...(data?.rows ?? [])].sort(
    (a, b) => stageRank(a) - stageRank(b) || (a.pocEdgePct ?? 0) - (b.pocEdgePct ?? 0),
  );
  const currency = marketOf(view.market).currency;
  const updated = data ? new Date(data.asOf).toLocaleTimeString('en-GB', { hour12: false }) : '';

  return (
    <div
      className="relative flex shrink-0 flex-col border-l border-[#2a2e39] bg-[#131722]"
      style={{ width }}
      data-tv-ui
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize POC scanner panel"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          save(WIDTH_KEY, DEFAULT_WIDTH);
        }}
        className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#4fc3f7]/60 ${
          dragging ? 'bg-[#4fc3f7]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-col-resize" /> : null}
      <div className="flex h-11 items-center justify-between border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-semibold text-[#d1d4dc]">POC retest scanner</span>
        <button
          type="button"
          onClick={() => void run()}
          disabled={status === 'loading'}
          title={status === 'loading' ? 'Scanning…' : 'Scan again now'}
          aria-label={status === 'loading' ? 'Scanning' : 'Scan again now'}
          className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white disabled:cursor-default disabled:hover:bg-transparent"
        >
          <span className={status === 'loading' ? 'animate-spin' : ''}>
            <Icons.refresh size={16} />
          </span>
        </button>
      </div>

      <div className="space-y-2.5 border-b border-[#2a2e39] px-3 py-2.5 text-[12px] text-[#b2b5be]">
        <div className="grid grid-cols-2 gap-2">
          {(['us', 'in'] as const).map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={view.market === id}
              onClick={() => patch({ market: id })}
              className={`rounded-md border border-[#2a2e39] py-1 transition-colors ${
                view.market === id
                  ? 'bg-[#2a2e39] text-white shadow-[inset_0_-2px_0_#4fc3f7]'
                  : 'hover:bg-[#1e222d] hover:text-white'
              }`}
            >
              {MARKETS[id]!.flag} {MARKETS[id]!.label}
            </button>
          ))}
        </div>
        <div
          role="group"
          aria-label="Side"
          className="grid grid-cols-3 overflow-hidden rounded-md border border-[#2a2e39]"
        >
          {SIDES.map((s, i) => (
            <button
              key={s.id}
              type="button"
              aria-pressed={view.side === s.id}
              onClick={() => patch({ side: s.id })}
              className={`py-1 transition-colors ${i ? 'border-l border-[#2a2e39]' : ''} ${
                view.side === s.id
                  ? 'bg-[#2a2e39] text-white shadow-[inset_0_-2px_0_#4fc3f7]'
                  : 'hover:bg-[#1e222d] hover:text-white'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <label
          className="flex items-center justify-between gap-2"
          title={`Also list names that have not touched yet but are within ${APPROACH_PCT}% of the entry edge`}
        >
          <span>Include approaching</span>
          <input
            type="checkbox"
            checked={view.approaching}
            onChange={(e) => patch({ approaching: e.target.checked })}
            className="h-3.5 w-3.5 accent-[#4fc3f7]"
          />
        </label>
        <label
          className="flex items-center justify-between gap-2"
          title={`Also list names that have given back at least ${PULLBACK_SHARE * 100}% of their move away and are heading back toward the zone`}
        >
          <span>Include pulling back</span>
          <input
            type="checkbox"
            checked={view.pullback}
            onChange={(e) => patch({ pullback: e.target.checked })}
            className="h-3.5 w-3.5 accent-[#4fc3f7]"
          />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {status === 'error' && !data ? (
          <p className="px-3 py-4 text-[12px] text-[#ef5350]">Scan failed: {error}</p>
        ) : !data ? (
          <p className="px-3 py-4 text-[12px] text-[#868993]">Scanning…</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-4 text-[12px] leading-relaxed text-[#868993]">
            No setups right now. Most liquid names have either already come back to their recent
            POCs or are still moving away from them. It rescans every minute.
          </p>
        ) : (
          rows.map((r) => (
            <SetupRow
              key={r.symbol}
              row={r}
              currency={currency}
              active={r.symbol === active}
              onOpen={onOpen}
              watched={watched.has(r.symbol)}
              onWatch={watch}
            />
          ))
        )}
      </div>

      <div className="space-y-1 border-t border-[#2a2e39] px-3 py-2 text-[11px] leading-snug text-[#868993]">
        <p>
          Setup: a POC zone from the last {LOOKBACK_SESSIONS} sessions that price has not come back
          to → price moved ≥{AWAY_PCT}% away (over any number of days) → pulling back (gave back ≥
          {PULLBACK_SHARE * 100}% of the move) → first touch of the zone&apos;s near edge (live for{' '}
          {LIVE_BARS * 5} min). Stop = the zone&apos;s far edge. Not backtested: it finds the
          pattern, it does not say it pays.
        </p>
        {data ? (
          <p>
            {rows.length} setup{rows.length === 1 ? '' : 's'} · updated {updated}
            {data.notes.length ? ' · the 150 most-traded names were checked' : ''}
            {status === 'error' ? (
              <span className="text-[#ef5350]"> · rescan failed, showing the last results</span>
            ) : null}
          </p>
        ) : null}
      </div>
    </div>
  );
}

const STAGE_ORDER = ['first touch', 'approaching', 'pulling back', 'away'];
const stageRank = (r: ScreenerRow) => {
  const i = STAGE_ORDER.indexOf(r.pocStage?.split(' · ')[1] ?? '');
  return i < 0 ? STAGE_ORDER.length : i;
};

const ago = (n: number | null | undefined) =>
  n == null ? '' : n === 1 ? "yesterday's POC" : `POC from ${n} sessions ago`;

function SetupRow({
  row,
  currency,
  active,
  onOpen,
  watched,
  onWatch,
}: {
  row: ScreenerRow;
  currency: string;
  active: boolean;
  onOpen: (row: ScreenerRow) => void;
  watched: boolean;
  onWatch: (row: ScreenerRow) => void;
}) {
  const long = row.pocStage?.startsWith('Long');
  const touching = row.pocStage?.endsWith('first touch');
  const price = (v: number | null | undefined) => formatValue('usd', v);
  return (
    // a div, not a button: the watchlist button sits inside it
    <div
      role="button"
      tabIndex={0}
      data-active={active}
      onClick={() => onOpen(row)}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onOpen(row);
        }
      }}
      className={`block w-full cursor-pointer border-b border-[#2a2e39] px-3 py-2 text-left text-[12px] transition-colors hover:bg-[#1e222d] ${
        active ? 'bg-[#2a2e39]' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1">
          <span className="truncate font-semibold text-[#d1d4dc]">{row.symbol}</span>
          <button
            type="button"
            disabled={watched}
            title={watched ? 'In your watchlist' : 'Add to watchlist'}
            aria-label={
              watched ? `${row.symbol} is in your watchlist` : `Add ${row.symbol} to watchlist`
            }
            onClick={(e) => {
              e.stopPropagation();
              onWatch(row);
            }}
            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded ${
              watched ? 'text-[#26a69a]' : 'text-[#868993] hover:bg-[#363a45] hover:text-white'
            }`}
          >
            {watched ? <Icons.check size={14} /> : <Icons.plus size={14} />}
          </button>
        </div>
        <span
          className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium"
          style={{
            color: long ? GREEN : RED,
            background: touching ? (long ? '#26a69a26' : '#ef535026') : 'transparent',
            border: `1px solid ${long ? GREEN : RED}${touching ? '' : '66'}`,
          }}
        >
          {row.pocStage}
        </span>
      </div>
      <div className="mt-0.5 truncate text-[11px] text-[#868993]">{row.name}</div>
      <div className="mt-1.5 grid grid-cols-4 gap-1 tabular-nums text-[#b2b5be]">
        <Cell label="Price" value={price(row.price)} />
        <Cell label="Entry" value={price(row.pocEntry)} />
        <Cell label="Stop" value={price(row.pocStop)} />
        <Cell label="Risk" value={formatValue('pct', row.pocRiskPct)} />
      </div>
      <div className="mt-1 text-[11px] text-[#868993]">
        {(row.pocEdgePct ?? 0) <= 0
          ? 'Inside the zone'
          : `${formatValue('pct', row.pocEdgePct)} ${touching ? 'past the entry after touching' : 'to entry'}`}{' '}
        · POC {price(row.pocPrice)} {currency}
      </div>
      <div className="mt-0.5 text-[11px] text-[#868993]">
        {ago(row.pocSessionsAgo)}
        {row.pocExtreme != null && !touching
          ? ` · furthest ${price(row.pocExtreme)}, gave back ${Math.round(row.pocPullbackPct ?? 0)}%`
          : ''}
      </div>
    </div>
  );
}

const Cell = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div className="text-[10px] uppercase tracking-wide text-[#5d606b]">{label}</div>
    <div>{value}</div>
  </div>
);
