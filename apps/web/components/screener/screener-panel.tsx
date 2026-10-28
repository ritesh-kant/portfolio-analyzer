'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_SORT, FIELD, VIEWS } from '../../lib/screener/fields';
import { DEFAULT_MARKET, marketOf } from '../../lib/screener/markets';
import { isActive } from '../../lib/screener/query';
import {
  type Screen,
  blankScreen,
  filtersForMarket,
  newId,
  pillsFor,
  pushHistory,
  redo as redoHistory,
  sameFilters,
  seedScreens,
  startHistory,
  undo as undoHistory,
  withPill,
  type History,
} from '../../lib/screener/screens';
import type { FilterState, ScreenerResponse, ScreenerRow } from '../../lib/screener/types';
import { load, save } from '../../lib/tv/storage';
import { Icons } from '../tv/icons';
import { FilterBar } from './filter-bar';
import { Popover } from './popover';
import { ColumnsMenu, ResultsTable } from './results-table';

const SCREENS_KEY = 'scr:screens';
const ACTIVE_KEY = 'scr:active';
/** Ids of the built-in screens this browser has already been offered. */
const PRESETS_SEEN_KEY = 'scr:presetsSeen';
const AUTO_KEY = 'scr:auto';
const WIDTH_KEY = 'scr:width';
const DEFAULT_WIDTH = 640;
const WIDE_WIDTH = 960;
const MIN_WIDTH = 420;
/** The chart keeps at least this much room: the panel can grow, but never swallow the chart. */
const CHART_MIN = 320;
const clampWidth = (w: number) =>
  Math.round(
    Math.min(Math.max(MIN_WIDTH, w), Math.max(MIN_WIDTH, window.innerWidth - CHART_MIN - 48)),
  );
const REFRESH_MS = 30_000;
const DEBOUNCE_MS = 350;

interface Props {
  /** `page`: fills its parent. `side`: the chart's right-hand panel, which can be widened / closed. */
  mode: 'page' | 'side';
  /** The symbol the chart is on, highlighted in the table. */
  activeSymbol?: string | null;
  onOpenSymbol: (row: ScreenerRow) => void;
  onAddWatch?: (row: ScreenerRow) => void;
  onClose?: () => void;
}

const iconButton =
  'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[#363a45] text-[#b2b5be] transition-colors hover:border-[#5d606b] hover:text-white disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-[#363a45] disabled:hover:text-[#b2b5be]';

const Refresh = ({ spin }: { spin: boolean }) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.4"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={spin ? 'animate-spin' : ''}
    aria-hidden
  >
    <path d="M16.5 10a6.5 6.5 0 1 1-2-4.7M16.5 3.5v3h-3" />
  </svg>
);

function hhmmss(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour12: false });
  } catch {
    return '';
  }
}

export function ScreenerPanel({
  mode,
  activeSymbol = null,
  onOpenSymbol,
  onAddWatch,
  onClose,
}: Props) {
  const [screens, setScreens] = useState<Screen[]>(() => seedScreens());
  const [activeId, setActiveId] = useState<string>(() => seedScreens()[0]!.id);
  const [hydrated, setHydrated] = useState(false);
  const [history, setHistory] = useState<History<FilterState>>(() =>
    startHistory(seedScreens()[0]!.filters),
  );
  const [openPill, setOpenPill] = useState<string | null>(null);
  const [result, setResult] = useState<ScreenerResponse | null>(null);
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);
  const [auto, setAuto] = useState(true);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  widthRef.current = width;
  const expanded = width > (DEFAULT_WIDTH + WIDE_WIDTH) / 2;
  const [showNotes, setShowNotes] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [nameMenu, setNameMenu] = useState<'list' | 'actions' | null>(null);
  const [colMenu, setColMenu] = useState(false);
  const nameRef = useRef<HTMLButtonElement>(null);
  const gearRef = useRef<HTMLButtonElement>(null);
  const colRef = useRef<HTMLButtonElement>(null);

  const screen = screens.find((s) => s.id === activeId) ?? screens[0]!;
  const market = marketOf(screen.market);

  // ── persistence ────────────────────────────────────────────────────────────
  useEffect(() => {
    const saved = load<Screen[] | null>(SCREENS_KEY, null);
    // Screens saved before markets existed carry none: they were all US screens.
    const base = (Array.isArray(saved) && saved.length ? saved : seedScreens()).map((s) => ({
      ...s,
      market: s.market ?? DEFAULT_MARKET,
    }));
    // A built-in screen shipped after the list was saved is appended once; one the user deleted stays deleted.
    const seen = new Set(load<string[]>(PRESETS_SEEN_KEY, []));
    const have = new Set(base.map((s) => s.id));
    const added = seedScreens().filter((s) => !have.has(s.id) && !seen.has(s.id));
    const list = [...base, ...added];
    const wanted = load<string>(ACTIVE_KEY, list[0]!.id);
    const first = list.find((s) => s.id === wanted) ?? list[0]!;
    setScreens(list);
    setActiveId(first.id);
    setHistory(startHistory(first.filters));
    setAuto(load<boolean>(AUTO_KEY, true));
    setWidth(clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)));
    setHydrated(true);
  }, []);
  useEffect(() => {
    if (!hydrated) return;
    save(SCREENS_KEY, screens);
    // Only once the list holding them is saved: the hydrate effect may run twice (React strict mode).
    save(
      PRESETS_SEEN_KEY,
      seedScreens().map((s) => s.id),
    );
  }, [screens, hydrated]);
  useEffect(() => {
    if (hydrated) save(ACTIVE_KEY, activeId);
  }, [activeId, hydrated]);

  const patch = useCallback(
    (p: Partial<Screen>) =>
      setScreens((cur) => cur.map((s) => (s.id === activeId ? { ...s, ...p } : s))),
    [activeId],
  );

  const setFilters = (next: FilterState) => {
    setHistory((h) => pushHistory(h, next, sameFilters));
    patch({ filters: next, pills: pillsFor(next, screen.pills) });
  };
  const undo = () => {
    const h = undoHistory(history);
    setHistory(h);
    patch({ filters: h.present, pills: pillsFor(h.present, screen.pills) });
  };
  const redo = () => {
    const h = redoHistory(history);
    setHistory(h);
    patch({ filters: h.present, pills: pillsFor(h.present, screen.pills) });
  };
  const switchTo = (id: string) => {
    const next = screens.find((s) => s.id === id);
    if (!next) return;
    setActiveId(id);
    setHistory(startHistory(next.filters));
    setOpenPill(null);
    setResult(null);
  };

  // ── fetching ───────────────────────────────────────────────────────────────
  const requestKey = useMemo(
    () =>
      JSON.stringify({ m: screen.market, f: screen.filters, s: screen.sort, c: screen.columns }),
    [screen.filters, screen.sort, screen.columns],
  );
  const keyRef = useRef(requestKey);
  keyRef.current = requestKey;

  useEffect(() => {
    if (!hydrated) return;
    const ctl = new AbortController();
    setStatus('loading');
    const timer = setTimeout(async () => {
      try {
        const { m, f, s, c } = JSON.parse(keyRef.current) as {
          m: string;
          f: FilterState;
          s: Screen['sort'];
          c: string[];
        };
        const res = await fetch('/api/screener', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ market: m, filters: f, sort: s, columns: c, limit: 100 }),
          signal: ctl.signal,
          cache: 'no-store',
        });
        const body = (await res.json()) as ScreenerResponse & { error?: string };
        if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
        setResult(body);
        setError('');
        setStatus('ok');
      } catch (e) {
        if (ctl.signal.aborted) return;
        setError(e instanceof Error ? e.message : 'Screener unavailable');
        setStatus('error');
      }
    }, DEBOUNCE_MS);
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [requestKey, tick, hydrated]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => {
      if (!document.hidden) setTick((n) => n + 1);
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [auto]);

  // ── screen management ──────────────────────────────────────────────────────
  const newScreen = () => {
    const name = window.prompt('Name for the new screen', 'New screen')?.trim();
    if (!name) return;
    const s = blankScreen(name, screen.market);
    setScreens((cur) => [...cur, s]);
    setActiveId(s.id);
    setHistory(startHistory(s.filters));
    setOpenPill(null);
    setResult(null);
  };
  const rename = () => {
    const name = window.prompt('Rename screen', screen.name)?.trim();
    if (name) patch({ name });
  };
  const duplicate = () => {
    const copy: Screen = { ...structuredClone(screen), id: newId(), name: `${screen.name} copy` };
    setScreens((cur) => [...cur, copy]);
    setActiveId(copy.id);
    setHistory(startHistory(copy.filters));
  };
  const remove = () => {
    if (screens.length < 2 || !window.confirm(`Delete “${screen.name}”?`)) return;
    const rest = screens.filter((s) => s.id !== screen.id);
    setScreens(rest);
    setActiveId(rest[0]!.id);
    setHistory(startHistory(rest[0]!.filters));
  };
  const restoreBuiltIns = () => {
    const have = new Set(screens.map((s) => s.id));
    setScreens((cur) => [...cur, ...seedScreens().filter((s) => !have.has(s.id))]);
  };
  const switchMarket = (id: string) => {
    const filters = filtersForMarket(screen.filters, id);
    setHistory((h) => pushHistory(h, filters, sameFilters));
    patch({ market: id, filters, pills: pillsFor(filters, screen.pills) });
    setOpenPill(null);
    setResult(null);
  };
  const resetFilters = () => {
    // Clears every filter but keeps the universe (exchanges, common stock only) the way the user set it.
    const kept: FilterState = {};
    for (const k of ['exchange', 'commonOnly']) if (screen.filters[k]) kept[k] = screen.filters[k]!;
    setFilters(kept);
  };

  const onSort = (key: string) => {
    const same = screen.sort.key === key;
    const textual = key === 'symbol' || FIELD.get(key)?.unit === 'text';
    patch({
      sort: {
        key,
        dir: same ? (screen.sort.dir === 'desc' ? 'asc' : 'desc') : textual ? 'asc' : 'desc',
      },
    });
  };

  const viewName =
    Object.entries(VIEWS).find(
      ([, ids]) =>
        ids.length === screen.columns.length && ids.every((id, i) => screen.columns[i] === id),
    )?.[0] ?? 'Custom';

  const rows = result?.rows ?? [];
  const activeFilters = Object.entries(screen.filters).filter(
    ([k, v]) => k !== 'exchange' && k !== 'commonOnly' && isActive(v),
  ).length;
  const loading = status === 'loading';
  const frame =
    mode === 'side'
      ? 'relative h-full shrink-0 border-l border-[#2a2e39]'
      : 'h-full rounded-xl border border-[#2a2e39]';

  /** Drag the panel's left edge: moving left widens it (same as the watchlist). */
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
  const setSaved = (w: number) => {
    const next = clampWidth(w);
    setWidth(next);
    save(WIDTH_KEY, next);
  };

  return (
    <section
      aria-label="Stock screener"
      className={`flex min-h-0 min-w-0 flex-col bg-[#131722] text-[#d1d4dc] ${frame}`}
      style={mode === 'side' ? { width } : undefined}
    >
      {mode === 'side' ? (
        <>
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize screener"
            title="Drag to resize · double-click to reset"
            onMouseDown={startResize}
            onDoubleClick={() => setSaved(DEFAULT_WIDTH)}
            className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#2962ff]/60 ${
              dragging ? 'bg-[#2962ff]/80' : ''
            }`}
          />
          {dragging ? <div className="fixed inset-0 z-[300] cursor-col-resize" /> : null}
        </>
      ) : null}
      {/* title row */}
      <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-[#2a2e39] px-3">
        <div className="text-[14px] font-semibold text-[#d1d4dc]">Stock screener</div>
        {mode === 'side' ? (
          <div className="flex items-center gap-1">
            <button
              type="button"
              title={expanded ? 'Narrower' : 'Wider'}
              aria-label={expanded ? 'Narrower screener' : 'Wider screener'}
              className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
              onClick={() => setSaved(expanded ? DEFAULT_WIDTH : WIDE_WIDTH)}
            >
              {expanded ? <Icons.collapse size={18} /> : <Icons.expand size={18} />}
            </button>
            {onClose ? (
              <button
                type="button"
                title="Close"
                aria-label="Close screener"
                className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
                onClick={onClose}
              >
                <Icons.close size={18} />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* screen name · undo/redo · screen actions */}
      <div className="flex items-center gap-1.5 px-3 pb-2 pt-2.5">
        <button
          ref={nameRef}
          type="button"
          className="flex items-center gap-1.5 rounded-md py-1 pr-2 text-[16px] font-semibold leading-none text-white hover:text-[#2962ff]"
          onClick={() => setNameMenu(nameMenu === 'list' ? null : 'list')}
        >
          {screen.name}
          <Icons.chevron size={14} />
        </button>
        <div className="flex-1" />
        <button
          type="button"
          title="Undo"
          aria-label="Undo"
          className={iconButton}
          disabled={!history.past.length}
          onClick={undo}
        >
          <Icons.undo size={15} />
        </button>
        <button
          type="button"
          title="Redo"
          aria-label="Redo"
          className={iconButton}
          disabled={!history.future.length}
          onClick={redo}
        >
          <Icons.redo size={15} />
        </button>
        <button
          ref={gearRef}
          type="button"
          title="Screen options"
          aria-label="Screen options"
          className={iconButton}
          onClick={() => setNameMenu(nameMenu === 'actions' ? null : 'actions')}
        >
          <Icons.gear size={15} />
        </button>
        {nameMenu === 'list' ? (
          <Popover anchor={nameRef} onClose={() => setNameMenu(null)} width={260}>
            <div className="max-h-[320px] overflow-y-auto py-1">
              {screens.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className={`flex w-full items-center justify-between px-3 py-2 text-left hover:bg-[#2a2e39] ${s.id === screen.id ? 'text-[#2962ff]' : ''}`}
                  onClick={() => {
                    switchTo(s.id);
                    setNameMenu(null);
                  }}
                >
                  {s.name}
                  {s.id === screen.id ? <span aria-hidden>✓</span> : null}
                </button>
              ))}
            </div>
          </Popover>
        ) : null}
        {nameMenu === 'actions' ? (
          <Popover anchor={gearRef} onClose={() => setNameMenu(null)} width={230}>
            <div className="py-1">
              {(
                [
                  ['New screen', newScreen, false],
                  ['Rename', rename, false],
                  ['Duplicate', duplicate, false],
                  ['Restore built-in screens', restoreBuiltIns, false],
                  ['Delete', remove, screens.length < 2],
                ] as const
              ).map(([label, fn, disabled]) => (
                <button
                  key={label}
                  type="button"
                  disabled={disabled}
                  className="block w-full px-3 py-2 text-left hover:bg-[#2a2e39] disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => {
                    setNameMenu(null);
                    fn();
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </Popover>
        ) : null}
      </div>

      {/* filters */}
      <div className="px-3 pb-3">
        <FilterBar
          market={market}
          onMarket={switchMarket}
          filters={screen.filters}
          pills={screen.pills}
          openId={openPill}
          onOpen={setOpenPill}
          onChange={setFilters}
          onAddPill={(id) => {
            patch({ pills: withPill(screen.pills, id) });
            setOpenPill(id);
          }}
          onReset={resetFilters}
        />
      </div>

      {/* table toolbar */}
      <div className="flex items-center gap-2 border-t border-[#2a2e39] px-3 py-2">
        <div className="flex h-7 items-center gap-1.5 px-1 text-[13px] text-white">
          <Icons.list size={16} />
          Table
        </div>
        <span className="mx-1 h-4 w-px bg-[#363a45]" />
        <button
          ref={colRef}
          type="button"
          className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[13px] text-white hover:bg-[#2a2e39]"
          onClick={() => setColMenu((m) => !m)}
        >
          <Icons.indicators size={16} />
          {viewName}
          <Icons.chevron size={12} />
        </button>
        {colMenu ? (
          <ColumnsMenu
            columns={screen.columns}
            onChange={(next) => patch({ columns: next })}
            anchor={colRef}
            onClose={() => setColMenu(false)}
          />
        ) : null}
        <div className="flex-1" />
        <label
          className="hidden items-center gap-2 text-[13px] text-[#868993] sm:flex"
          title={`Refresh every ${REFRESH_MS / 1000}s while the tab is visible`}
        >
          <input
            type="checkbox"
            className="h-4 w-4 accent-[#2962ff]"
            checked={auto}
            onChange={(e) => {
              setAuto(e.target.checked);
              save(AUTO_KEY, e.target.checked);
            }}
          />
          Auto-refresh
        </label>
        <button
          type="button"
          title="Refresh now"
          aria-label="Refresh"
          className={iconButton}
          onClick={() => setTick((n) => n + 1)}
        >
          <Refresh spin={loading} />
        </button>
      </div>

      {status === 'error' ? (
        <div className="mx-5 mb-2 flex items-center justify-between gap-3 rounded-md border border-[#f23645]/40 bg-[#f23645]/10 px-3 py-2 text-[13px] text-[#ff8a95]">
          <span>Could not load results: {error}</span>
          <button
            type="button"
            className="shrink-0 font-medium underline"
            onClick={() => setTick((n) => n + 1)}
          >
            Retry
          </button>
        </div>
      ) : null}

      {/* results */}
      {!result && loading ? (
        <div className="flex flex-1 items-center justify-center text-[#868993]">Screening…</div>
      ) : result && !rows.length && !loading ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 px-6 text-center text-[#868993]">
          <span className="text-[15px] text-[#d1d4dc]">
            No names match these filters right now.
          </span>
          <span>
            {activeFilters > 0
              ? 'Loosen a filter, or check whether the market is open: change % and volume reset each session.'
              : 'Try another screen from the list above.'}
          </span>
        </div>
      ) : (
        <ResultsTable
          currency={market.currency}
          market={market.id}
          rows={rows}
          total={result?.total ?? 0}
          columns={screen.columns}
          sort={screen.sort ?? DEFAULT_SORT}
          loading={loading}
          selected={selected ?? activeSymbol}
          onSort={onSort}
          onOpen={(row) => {
            setSelected(row.symbol);
            onOpenSymbol(row);
          }}
          onAddWatch={onAddWatch}
          onColumns={(next) => patch({ columns: next })}
        />
      )}

      {/* footer */}
      <div className="border-t border-[#2a2e39] px-5 py-2 text-[12px] text-[#868993]">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {result ? (
            <span>
              {rows.length < result.total
                ? `Showing ${rows.length} of ${result.total} matches`
                : `${result.total} match${result.total === 1 ? '' : 'es'}`}
            </span>
          ) : null}
          <span title="Yahoo Finance quotes can lag the exchange by up to 15 minutes">
            Yahoo Finance · may be delayed
          </span>
          {result ? <span>updated {hhmmss(result.asOf)}</span> : null}
          {result?.notes.length ? (
            <button
              type="button"
              className="text-[#ffb74d] hover:underline"
              onClick={() => setShowNotes((s) => !s)}
            >
              ⓘ {result.notes.length} note{result.notes.length === 1 ? '' : 's'}
            </button>
          ) : null}
        </div>
        {showNotes && result?.notes.length ? (
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[#ffb74d]">
            {result.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
