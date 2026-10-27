'use client';

import { useEffect, useRef, useState } from 'react';
import type { Fvg, FvgConfig } from '../../lib/tv/fvg';
import { load, save } from '../../lib/tv/storage';
import { PANEL_WIDTH_KEY } from './panel-width';

const WIDTH_KEY = PANEL_WIDTH_KEY;
const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));

export type FvgFilter = 'all' | 'bullish' | 'bearish';

/** What the list shows, applied after detection. */
export interface FvgView {
  filter: FvgFilter;
  /** Also list zones price has invalidated (they are faded on the chart). */
  invalidated: boolean;
  /** Hide gaps whose against-volume share is above this percent; 100 shows all. */
  maxAgainst: number;
}

export const FVG_VIEW_DEFAULTS: FvgView = { filter: 'all', invalidated: false, maxAgainst: 100 };

interface Props {
  symbol: string;
  /** Detected gaps (already limited by the view), oldest first. */
  items: Fvg[];
  /** Total detected before the view, so an empty list can say why. */
  detected: number;
  cfg: FvgConfig;
  onCfg: (patch: Partial<FvgConfig>) => void;
  view: FvgView;
  onView: (patch: Partial<FvgView>) => void;
  marksOn: boolean;
  onMarks: (on: boolean) => void;
  intraday: boolean;
  timeZone: string;
  active: number | null;
  onJump: (index: number) => void;
}

const fmtVol = (v: number) =>
  v >= 1e6
    ? `${(v / 1e6).toFixed(2)}M`
    : v >= 1e3
      ? `${(v / 1e3).toFixed(1)}K`
      : `${Math.round(v)}`;
const fmtPrice = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3));

const FILTERS: { id: FvgFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'bullish', label: 'Bullish' },
  { id: 'bearish', label: 'Bearish' },
];

const field =
  'w-16 rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-0.5 text-right text-[12px] text-[#d1d4dc] outline-none focus:border-[#2962ff]';

function NumberField({
  label,
  title,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  title: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label
      className="flex items-center justify-between gap-2 text-[12px] text-[#b2b5be]"
      title={title}
    >
      <span>{label}</span>
      <input
        type="number"
        className={field}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(Math.min(max, Math.max(min, n)));
        }}
      />
    </label>
  );
}

function Select<T extends string>({
  label,
  title,
  value,
  options,
  onChange,
}: {
  label: string;
  title: string;
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <label
      className="flex items-center justify-between gap-2 text-[12px] text-[#b2b5be]"
      title={title}
    >
      <span>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-0.5 text-[12px] text-[#d1d4dc] outline-none focus:border-[#2962ff]"
      >
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Right-hand panel: the fair value gaps on the chart's candles with their volume split, newest first. */
export function FvgPanel({
  symbol,
  items,
  detected,
  cfg,
  onCfg,
  view,
  onView,
  marksOn,
  onMarks,
  intraday,
  timeZone,
  active,
  onJump,
}: Props) {
  const when = (t: number) =>
    new Date(t * 1000).toLocaleString('en-GB', {
      timeZone,
      day: '2-digit',
      month: 'short',
      ...(intraday ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
    });
  const listRef = useRef<HTMLDivElement>(null);
  // a pick made on the chart (or by the list itself) scrolls its row into view
  useEffect(() => {
    if (active == null) return;
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);
  const rows = [...items].reverse();

  // Read the saved width up front: a panel only mounts after a click (never during hydration), and starting at the
  // default then jumping to the saved width resized the chart twice, which flickered.
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

  return (
    <div
      className="relative flex shrink-0 flex-col border-l border-[#2a2e39] bg-[#131722]"
      style={{ width }}
      data-tv-ui
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize fair value gap panel"
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
      <div className="flex h-11 items-center border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-semibold text-[#d1d4dc]">Fair value gaps</span>
      </div>
      <div className="space-y-2.5 border-b border-[#2a2e39] px-3 py-2.5 text-[12px] text-[#b2b5be]">
        <div
          role="group"
          aria-label="Direction"
          className="grid grid-cols-3 overflow-hidden rounded-md border border-[#2a2e39]"
        >
          {FILTERS.map((f, i) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={view.filter === f.id}
              onClick={() => onView({ filter: f.id })}
              className={`py-1 transition-colors ${i ? 'border-l border-[#2a2e39]' : ''} ${
                view.filter === f.id
                  ? 'bg-[#2a2e39] text-white shadow-[inset_0_-2px_0_#4fc3f7]'
                  : 'hover:bg-[#1e222d] hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 items-center gap-x-4 gap-y-2">
          <label
            className="flex items-center justify-between gap-2"
            title="Also list zones price has already invalidated"
          >
            <span>Invalidated</span>
            <input
              type="checkbox"
              checked={view.invalidated}
              onChange={(e) => onView({ invalidated: e.target.checked })}
              className="h-3.5 w-3.5 accent-[#4fc3f7]"
            />
          </label>
          <label className="flex items-center justify-between gap-2">
            <span>Mark on chart</span>
            <input
              type="checkbox"
              checked={marksOn}
              onChange={(e) => onMarks(e.target.checked)}
              className="h-3.5 w-3.5 accent-[#4fc3f7]"
            />
          </label>
        </div>
        <NumberField
          label="Against volume % ≤"
          title="Hide gaps where more than this share of the three candles' volume went against the gap. Low = the move had the volume behind it. 100 shows all."
          value={view.maxAgainst}
          min={0}
          max={100}
          step={5}
          onChange={(maxAgainst) => onView({ maxAgainst })}
        />
        <details>
          <summary className="cursor-pointer select-none text-[#b2b5be] hover:text-white">
            Rules &amp; settings
          </summary>
          <div className="mt-2 space-y-1.5 text-[11px] leading-snug text-[#787b86]">
            <p>
              A gap is three candles whose outer wicks do not overlap: a{' '}
              <span className="text-[#089981]">bullish</span> gap has candle 1&apos;s high below
              candle 3&apos;s low, a <span className="text-[#f23645]">bearish</span> one the
              reverse. The zone is what price skipped.
            </p>
            <p>
              The volume of the three candles is split into what drove the gap (candles 2 and 3) and
              what sat against it (candle 1). <span className="text-[#d1d4dc]">Against %</span> is
              that against-share: low means real participation behind the move, high means a weaker
              gap that is likelier to fail when price returns.
            </p>
            <p>
              Detection follows the Flux Charts indicator&apos;s documentation, not its code, so
              edge cases can differ. Volume is the chart&apos;s own candle volume.
            </p>
          </div>
          <div className="mt-3 space-y-2">
            <Select
              label="Invalidation"
              title="Close: a candle has to close through the zone's far side. Wick: touching it is enough."
              value={cfg.invalidation}
              options={[
                { id: 'close', label: 'Close' },
                { id: 'wick', label: 'Wick' },
              ]}
              onChange={(invalidation) => onCfg({ invalidation })}
            />
            <Select
              label="Detection"
              title="Same type: all three candles point the gap's way. All: candles of either colour may form it."
              value={cfg.detection}
              options={[
                { id: 'same', label: 'Same type' },
                { id: 'all', label: 'All' },
              ]}
              onChange={(detection) => onCfg({ detection })}
            />
            <Select
              label="Filter"
              title="Volume: the three candles must trade more than the recent average. ATR: the gap must be tall enough against volatility. Off lists every gap."
              value={cfg.filter}
              options={[
                { id: 'volume', label: 'Volume' },
                { id: 'atr', label: 'ATR size' },
                { id: 'off', label: 'Off' },
              ]}
              onChange={(filter) => onCfg({ filter })}
            />
            {cfg.filter === 'volume' ? (
              <NumberField
                label="Volume threshold %"
                title="The three candles' volume must be at least this percent of three average candles (mean of the 20 candles before). 150 = one and a half times."
                value={cfg.volumeThreshold}
                min={0}
                max={1000}
                step={10}
                onChange={(volumeThreshold) => onCfg({ volumeThreshold })}
              />
            ) : null}
            {cfg.filter === 'atr' ? (
              <NumberField
                label="Gap ≥ ATR ×"
                title="The gap's height must be at least this many ATRs (14)."
                value={cfg.atrMult}
                min={0}
                max={10}
                step={0.1}
                onChange={(atrMult) => onCfg({ atrMult })}
              />
            ) : null}
          </div>
        </details>
      </div>
      <div className="border-b border-[#2a2e39] px-3 py-1.5 text-[11px] text-[#787b86]">
        {symbol} · {rows.length} gap{rows.length === 1 ? '' : 's'} on the chart&apos;s candles
      </div>
      <div className="grid grid-cols-[1fr_auto] gap-x-3 border-b border-[#2a2e39] px-3 py-1.5 text-[11px] font-medium text-[#787b86]">
        <span>Gap completed · status</span>
        <span className="w-16 text-right">Against</span>
      </div>
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((g) => {
          const color = g.bullish ? 'text-[#26a69a]' : 'text-[#ef5350]';
          const chip =
            g.status === 'invalidated'
              ? 'invalidated'
              : g.status === 'tested'
                ? 'tested'
                : 'untouched';
          const chipColor =
            g.status === 'open'
              ? 'text-[#ffb74d]'
              : g.status === 'tested'
                ? 'text-[#d1d4dc]'
                : 'text-[#787b86]';
          const pct = g.against;
          return (
            <div
              key={g.index}
              role="button"
              tabIndex={0}
              onClick={() => onJump(g.index)}
              onKeyDown={(e) => e.key === 'Enter' && onJump(g.index)}
              title={`Zone ${fmtPrice(g.bottom)} → ${fmtPrice(g.top)} · volume of the three candles ${fmtVol(g.volume)}${
                pct != null
                  ? `, ${pct.toFixed(1)}% of it against the gap`
                  : ' (no volume split: the candles carry no volume)'
              }`}
              data-active={g.index === active}
              className={`grid cursor-pointer grid-cols-[1fr_auto] items-center gap-x-3 px-3 py-1.5 text-[12.5px] hover:bg-[#2a2e39] ${
                g.index === active ? 'bg-[#2a2e39]' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block tabular-nums text-[#d1d4dc]">{when(g.time)}</span>
                <span className="block truncate text-[11px]">
                  <span className={color}>{g.bullish ? '▲ bullish' : '▼ bearish'}</span>
                  <span className="text-[#787b86]"> · </span>
                  <span className={chipColor}>{chip}</span>
                </span>
                <span className="block truncate text-[11px] text-[#787b86]">
                  {fmtPrice(g.bottom)} → {fmtPrice(g.top)} · vol {fmtVol(g.volume)}
                </span>
              </span>
              <span
                className={`w-16 text-right tabular-nums ${
                  pct == null
                    ? 'text-[#787b86]'
                    : pct <= 25
                      ? 'text-[#089981]'
                      : pct >= 40
                        ? 'text-[#f23645]'
                        : 'text-[#d1d4dc]'
                }`}
              >
                {pct == null ? '—' : `${Math.round(pct)}%`}
              </span>
            </div>
          );
        })}
        {!rows.length ? (
          <div className="px-4 py-10 text-center text-[13px] text-[#787b86]">
            {detected > 0
              ? 'Every gap is hidden by the list settings above (direction, invalidated, against volume).'
              : 'No gaps on these candles with the current settings. Try the filter Off, or a smaller volume threshold.'}
          </div>
        ) : null}
      </div>
    </div>
  );
}
