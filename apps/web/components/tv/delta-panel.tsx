'use client';

import { useEffect, useRef, useState } from 'react';
import type {
  DeltaConfig,
  DeltaRange,
  DeltaView,
  PlacedDivergence,
} from '../../lib/tv/delta-divergence';
import { load, save } from '../../lib/tv/storage';
import { PANEL_WIDTH_KEY } from './panel-width';
import { sessionDay } from '../../lib/tv/timeframes';
import { Icons } from './icons';

/** The list's candle length: a confirmation is known (and actionable) at the candle's close, one candle after its start. */
const CANDLE_SECS = 900;
const WIDTH_KEY = PANEL_WIDTH_KEY;
const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));

interface Props {
  symbol: string;
  items: PlacedDivergence[];
  /** 1-minute bars are loaded for this symbol (the footprint's delta needs them). */
  hasData: boolean;
  /** The chart's own candle size: the list is always 15-minute candles, whatever the chart shows. */
  chartTf: string;
  chartIs15m: boolean;
  cfg: DeltaConfig;
  onCfg: (patch: Partial<DeltaConfig>) => void;
  /** Which setups the list shows: a date range and a minimum |delta|. */
  view: DeltaView;
  onView: (patch: Partial<DeltaView>) => void;
  /** First and last 1-minute bar loaded (unix s): the most the range can cover. */
  dataFrom: number | null;
  dataTo: number | null;
  /** First 1-minute bar: candles before it use 5-minute bars (a coarser delta). */
  fineFrom: number | null;
  marksOn: boolean;
  onMarks: (on: boolean) => void;
  intraday: boolean;
  timeZone: string;
  active: number | null;
  onJump: (index: number) => void;
  /** Phones: a drawer over the chart instead of a docked column. */
  overlay?: boolean;
  onClose?: () => void;
}

const fmtVol = (v: number) => {
  const a = Math.abs(v);
  const s =
    a >= 1e6
      ? `${(a / 1e6).toFixed(1)}M`
      : a >= 1e3
        ? `${(a / 1e3).toFixed(1)}K`
        : `${Math.round(a)}`;
  return `${v < 0 ? '−' : '+'}${s}`;
};

const field =
  'w-14 rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-0.5 text-right text-[12px] text-[#d1d4dc] outline-none focus:border-[#2962ff]';

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

/** Right-hand panel: candles whose body and estimated delta point opposite ways, newest first. */
export function DeltaPanel({
  symbol,
  items,
  hasData,
  chartTf,
  chartIs15m,
  cfg,
  onCfg,
  view,
  onView,
  dataFrom,
  dataTo,
  fineFrom,
  marksOn,
  onMarks,
  intraday,
  timeZone,
  active,
  onJump,
  overlay = false,
  onClose,
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
  const dayLabel = (t: number) =>
    new Date(t * 1000).toLocaleDateString('en-GB', { timeZone, day: '2-digit', month: 'short' });
  const RANGES: { id: DeltaRange; label: string; title: string }[] = [
    { id: '1w', label: '1W', title: 'Last 7 days' },
    { id: '2w', label: '2W', title: 'Last 14 days' },
    { id: '1m', label: '1M', title: 'Last month' },
    {
      id: '3m',
      label: '3M',
      title:
        'Last 3 months (NSE stocks: 1-minute exchange bars from Upstox; others reach about 60 days)',
    },
    {
      id: '6m',
      label: '6M',
      title: 'Last 6 months (NSE stocks only; other symbols reach about 60 days)',
    },
    { id: 'custom', label: 'Custom', title: 'Pick a start and end date' },
  ];

  // Drag the panel's left edge: moving left widens it (same as the watchlist); the width is remembered.
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
      className={
        overlay
          ? 'fixed bottom-0 right-0 top-11 z-40 flex flex-col border-l border-[#2a2e39] bg-[#131722] shadow-2xl'
          : 'relative flex shrink-0 flex-col border-l border-[#2a2e39] bg-[#131722]'
      }
      style={{ width: overlay ? 'min(88vw, 340px)' : width }}
      data-tv-ui
    >
      <div
        hidden={overlay}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize delta divergence panel"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          save(WIDTH_KEY, DEFAULT_WIDTH);
        }}
        className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#e879f9]/60 ${
          dragging ? 'bg-[#e879f9]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-col-resize" /> : null}
      <div className="flex h-11 items-center justify-between border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-semibold text-[#d1d4dc]">Delta divergence</span>
        {overlay ? (
          <button
            type="button"
            onClick={onClose}
            title="Close"
            className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
          >
            <Icons.close size={18} />
          </button>
        ) : null}
      </div>
      <div className="space-y-2.5 border-b border-[#2a2e39] px-3 py-2.5 text-[12px] text-[#b2b5be]">
        <div
          role="group"
          aria-label="Date range"
          className="grid grid-cols-6 overflow-hidden rounded-md border border-[#2a2e39]"
        >
          {RANGES.map((r, i) => {
            const on = view.range === r.id;
            return (
              <button
                key={r.id}
                type="button"
                title={r.title}
                aria-pressed={on}
                onClick={() => onView({ range: r.id })}
                className={`py-1 transition-colors ${i ? 'border-l border-[#2a2e39]' : ''} ${
                  on
                    ? 'bg-[#2a2e39] text-white shadow-[inset_0_-2px_0_#e879f9]'
                    : 'hover:bg-[#1e222d] hover:text-white'
                }`}
              >
                {r.label}
              </button>
            );
          })}
        </div>
        {view.range === 'custom' ? (
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1.5">
            <input
              type="date"
              aria-label="From date"
              value={view.from}
              min={dataFrom != null ? sessionDay(dataFrom, timeZone) : undefined}
              max={view.to || (dataTo != null ? sessionDay(dataTo, timeZone) : undefined)}
              onChange={(e) => onView({ from: e.target.value })}
              className="min-w-0 rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-1 text-[12px] text-[#d1d4dc] outline-none [color-scheme:dark] focus:border-[#2962ff]"
            />
            <span className="text-[#787b86]">→</span>
            <input
              type="date"
              aria-label="To date"
              value={view.to}
              min={view.from || (dataFrom != null ? sessionDay(dataFrom, timeZone) : undefined)}
              max={dataTo != null ? sessionDay(dataTo, timeZone) : undefined}
              onChange={(e) => onView({ to: e.target.value })}
              className="min-w-0 rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-1 text-[12px] text-[#d1d4dc] outline-none [color-scheme:dark] focus:border-[#2962ff]"
            />
          </div>
        ) : null}
        <div className="grid grid-cols-2 items-center gap-x-4">
          <NumberField
            label="Min |Δ|"
            title="Hide setups whose signal candle has a smaller footprint delta than this, in shares/contracts (0 shows all)"
            value={view.minDelta}
            min={0}
            max={10000000}
            step={50}
            onChange={(minDelta) => onView({ minDelta })}
          />
          <label className="flex items-center justify-between gap-2">
            <span>Mark on chart</span>
            <input
              type="checkbox"
              checked={marksOn}
              onChange={(e) => onMarks(e.target.checked)}
              className="h-3.5 w-3.5 accent-[#e879f9]"
            />
          </label>
        </div>
        <details>
          <summary className="cursor-pointer select-none text-[#b2b5be] hover:text-white">
            Rules &amp; settings
          </summary>
          <div className="mt-2 space-y-1.5 text-[11px] leading-snug text-[#787b86]">
            <p>
              <span className="text-[#f23645]">Buyers trapped</span>: long upper wick (at least
              twice the lower one), positive delta, then a red candle breaks the low (short).
            </p>
            <p>
              <span className="text-[#089981]">Sellers trapped</span>: long lower wick (at least
              twice the upper one), negative delta, then a green candle breaks the high (long).
            </p>
            <p>
              Solid badge = confirmed, hollow = waiting. Delta is{' '}
              <span className="text-[#ffb74d]">estimated from 1-minute bars</span> (5-minute for
              candles older than about 4 weeks, tagged), not trades. Check the location yourself
              (order block, value-area edge, POC).
            </p>
          </div>
          <div className="mt-3 space-y-2">
            <NumberField
              label="Wick % of range ≥"
              title="The dominant wick (upper for buyers trapped, lower for sellers trapped) must be at least this share of the candle's high-low range, and at least as big as its body"
              value={cfg.wick}
              min={0}
              max={100}
              step={5}
              onChange={(wick) => onCfg({ wick })}
            />
            <NumberField
              label="Wick × other wick ≥"
              title="The dominant wick must be at least this many times the wick on the opposite side: 2 = twice as long. Keeps candles with a wick at both ends out."
              value={cfg.dominance}
              min={1}
              max={20}
              step={0.5}
              onChange={(dominance) => onCfg({ dominance })}
            />
            <NumberField
              label="|Δ| % of volume ≥"
              title="Skip candles whose footprint delta is smaller than this share of the candle's volume (0 keeps every disagreement, however small)"
              value={cfg.delta}
              min={0}
              max={100}
              step={1}
              onChange={(delta) => onCfg({ delta })}
            />
            <NumberField
              label="Candle RVOL ≥"
              title="Volume over the mean of the 20 candles before it; 0 keeps every setup"
              value={cfg.rvol}
              min={0}
              max={10}
              step={0.5}
              onChange={(rvol) => onCfg({ rvol })}
            />
            <NumberField
              label="Confirm within (candles)"
              title="How many 15-minute candles after the signal candle may break its low/high before the setup counts as having no follow-through"
              value={cfg.within}
              min={1}
              max={12}
              step={1}
              onChange={(within) => onCfg({ within })}
            />
            <label
              className="flex items-center justify-between gap-2 text-[12px] text-[#b2b5be]"
              title="On: the confirming candle must close below the signal candle's low (or above its high). Off: a wick through the level is enough."
            >
              <span>Confirm on close beyond level</span>
              <input
                type="checkbox"
                checked={cfg.closeBreak}
                onChange={(e) => onCfg({ closeBreak: e.target.checked })}
                className="h-3.5 w-3.5 accent-[#e879f9]"
              />
            </label>
            <label className="flex items-center justify-between gap-2 text-[12px] text-[#b2b5be]">
              <span>Show no follow-through</span>
              <input
                type="checkbox"
                checked={cfg.missed}
                onChange={(e) => onCfg({ missed: e.target.checked })}
                className="h-3.5 w-3.5 accent-[#e879f9]"
              />
            </label>
          </div>
        </details>
      </div>
      <div className="border-b border-[#2a2e39] px-3 py-1.5 text-[11px] text-[#787b86]">
        {symbol} · {items.length} setup{items.length === 1 ? '' : 's'} on 15m candles
        {dataFrom != null && dataTo != null ? (
          <span title="NSE stocks use 1-minute exchange bars from Upstox, which reach back years. Other symbols come from Yahoo: 1-minute bars about 4 weeks back, 5-minute bars 60 days back, and candles older than the 1-minute data use 5-minute bars, a coarser estimate.">
            {' '}
            · {dayLabel(dataFrom)} → {dayLabel(dataTo)}
            {fineFrom != null && fineFrom > dataFrom
              ? `, 1-minute delta from ${dayLabel(fineFrom)}`
              : ''}
          </span>
        ) : null}
      </div>
      {!chartIs15m ? (
        <div className="border-b border-[#2a2e39] px-3 py-1.5 text-[11px] leading-snug text-[#ffb74d]">
          The chart is on {chartTf}, the list is always 15-minute candles. A bracket marks the chart
          bars of each 15-minute candle, so one bar&apos;s footprint delta will not match its row.
        </div>
      ) : null}
      <div className="grid grid-cols-[1fr_auto] gap-x-3 border-b border-[#2a2e39] px-3 py-1.5 text-[11px] font-medium text-[#787b86]">
        <span>Signal candle · status</span>
        <span className="w-14 text-right">Δ</span>
      </div>
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((d) => {
          const buyers = d.kind === 'buyers';
          const chip =
            d.status === 'confirmed'
              ? `${buyers ? 'short' : 'long'} ✓ ${d.confirmTime != null ? when(d.confirmTime + CANDLE_SECS) : ''}`
              : d.status === 'pending'
                ? 'waiting for confirmation'
                : 'no follow-through';
          const chipColor =
            d.status === 'confirmed'
              ? 'text-[#e879f9]'
              : d.status === 'pending'
                ? 'text-[#ffb74d]'
                : 'text-[#787b86]';
          return (
            <div
              key={d.time}
              role="button"
              tabIndex={0}
              onClick={() => onJump(d.chartIndex)}
              onKeyDown={(e) => e.key === 'Enter' && onJump(d.chartIndex)}
              title={`${buyers ? 'Upper' : 'Lower'} wick ${d.wick.toFixed(0)}% of the range, footprint delta ${
                d.delta >= 0 ? 'positive' : 'negative'
              } (${d.pct.toFixed(1)}% of volume)${d.rvol != null ? ` · RVOL ${d.rvol.toFixed(1)}×` : ''}`}
              data-active={d.chartIndex === active}
              className={`grid cursor-pointer grid-cols-[1fr_auto] items-center gap-x-3 px-3 py-1.5 text-[12.5px] hover:bg-[#2a2e39] ${
                d.chartIndex === active ? 'bg-[#2a2e39]' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block tabular-nums text-[#d1d4dc]">{when(d.time)}</span>
                <span className="block truncate text-[11px]">
                  <span className={buyers ? 'text-[#f23645]' : 'text-[#089981]'}>
                    {buyers ? 'buyers trapped · upper wick' : 'sellers trapped · lower wick'}{' '}
                    {d.wick.toFixed(0)}%
                  </span>
                  <span className="text-[#787b86]"> · </span>
                  <span className={chipColor}>{chip}</span>
                  {d.coarse ? <span className="text-[#787b86]"> · coarser est. (5m)</span> : null}
                </span>
              </span>
              <span className="w-14 text-right tabular-nums text-[#d1d4dc]">{fmtVol(d.delta)}</span>
            </div>
          );
        })}
        {!rows.length ? (
          <div className="px-4 py-10 text-center text-[13px] text-[#787b86]">
            {hasData
              ? 'No setups on these candles with the current settings.'
              : 'Loading 1-minute and 5-minute data for the footprint delta…'}
          </div>
        ) : null}
      </div>
    </div>
  );
}
