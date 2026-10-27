'use client';

import { useEffect, useRef, useState } from 'react';
import type { CvdDivergence } from '../../lib/tv/cvd-divergence';
import { load, save } from '../../lib/tv/storage';
import { PANEL_WIDTH_KEY } from './panel-width';

const WIDTH_KEY = PANEL_WIDTH_KEY;
const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));

/** One divergence as the list needs it: the detector's result plus the times and links the chart works out. */
export interface CvdItem {
  key: string;
  div: CvdDivergence;
  /** A wick/delta setup pointing the same way sits next to the second swing. */
  near: boolean;
  /** Start of the candle holding the second swing, and the close of the candle that confirmed it (unix s). */
  time: number;
  confirmTime: number | null;
}

export type CvdFilter = 'all' | 'bullish' | 'bearish';

interface Props {
  symbol: string;
  items: CvdItem[];
  /** The chart has a CVD indicator with divergences switched on. */
  enabled: boolean;
  /** Why the list is empty when it is not about the data: no CVD indicator, or divergences off. */
  missing: 'indicator' | 'off' | null;
  onAddIndicator: () => void;
  filter: CvdFilter;
  onFilter: (f: CvdFilter) => void;
  showFailed: boolean;
  onShowFailed: (on: boolean) => void;
  intraday: boolean;
  timeZone: string;
  active: number | null;
  onJump: (index: number) => void;
}

const fmtVol = (v: number) => {
  const a = Math.abs(v);
  const s = a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : `${Math.round(a)}`;
  return `${v < 0 ? '−' : ''}${s}`;
};
const fmtPrice = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3));

const FILTERS: { id: CvdFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'bullish', label: 'Bullish' },
  { id: 'bearish', label: 'Bearish' },
];

/** Right-hand panel: the price/CVD divergences on the chart, newest first; a click centres the chart on one. */
export function CvdPanel({
  symbol,
  items,
  enabled,
  missing,
  onAddIndicator,
  filter,
  onFilter,
  showFailed,
  onShowFailed,
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
  const rows = items
    .filter((x) => (filter === 'all' || (filter === 'bullish') === x.div.bullish) && (showFailed || x.div.status !== 'failed'))
    .sort((a, b) => b.div.i2 - a.div.i2);

  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  widthRef.current = width;
  useEffect(() => {
    setWidth(clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)));
  }, []);
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
        aria-label="Resize CVD divergence panel"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          save(WIDTH_KEY, DEFAULT_WIDTH);
        }}
        className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#26a69a]/60 ${
          dragging ? 'bg-[#26a69a]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-col-resize" /> : null}
      <div className="flex h-11 items-center border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-semibold text-[#d1d4dc]">CVD divergence</span>
      </div>
      <div className="space-y-2.5 border-b border-[#2a2e39] px-3 py-2.5 text-[12px] text-[#b2b5be]">
        <div role="group" aria-label="Direction" className="grid grid-cols-3 overflow-hidden rounded-md border border-[#2a2e39]">
          {FILTERS.map((f, i) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => onFilter(f.id)}
              className={`py-1 transition-colors ${i ? 'border-l border-[#2a2e39]' : ''} ${
                filter === f.id
                  ? 'bg-[#2a2e39] text-white shadow-[inset_0_-2px_0_#26a69a]'
                  : 'hover:bg-[#1e222d] hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label
          className="flex items-center justify-between gap-2"
          title="Failed = price closed beyond the second swing's extreme, or no break of the swing between them came within the window. Also hides them on the chart."
        >
          <span>Show failed</span>
          <input
            type="checkbox"
            checked={showFailed}
            onChange={(e) => onShowFailed(e.target.checked)}
            className="h-3.5 w-3.5 accent-[#26a69a]"
          />
        </label>
        <div className="text-[11px] leading-snug text-[#787b86]">
          Swing size, hidden kinds and the meaning of each tag: CVD indicator settings (⚙ on the CVD pane).
        </div>
      </div>
      <div className="border-b border-[#2a2e39] px-3 py-1.5 text-[11px] text-[#787b86]">
        {symbol} · {rows.length} divergence{rows.length === 1 ? '' : 's'} on the chart&apos;s candles
      </div>
      <div className="grid grid-cols-[1fr_auto] gap-x-3 border-b border-[#2a2e39] px-3 py-1.5 text-[11px] font-medium text-[#787b86]">
        <span>Second swing · status</span>
        <span className="w-16 text-right">Price Δ</span>
      </div>
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {rows.map(({ key, div: d, near, time, confirmTime }) => {
          const color = d.bullish ? 'text-[#26a69a]' : 'text-[#ef5350]';
          const chip =
            d.status === 'confirmed'
              ? `${d.bullish ? 'long' : 'short'} ✓ ${confirmTime != null ? when(confirmTime) : ''}`
              : d.status === 'pending'
                ? 'waiting for confirmation'
                : 'failed';
          const chipColor =
            d.status === 'confirmed' ? 'text-[#d1d4dc]' : d.status === 'pending' ? 'text-[#ffb74d]' : 'text-[#787b86]';
          const move = ((d.p2 - d.p1) / d.p1) * 100;
          return (
            <div
              key={key}
              role="button"
              tabIndex={0}
              onClick={() => onJump(d.i2)}
              onKeyDown={(e) => e.key === 'Enter' && onJump(d.i2)}
              title={`Swing ${fmtPrice(d.p1)} → ${fmtPrice(d.p2)} while CVD went ${fmtVol(d.c1)} → ${fmtVol(d.c2)}`}
              data-active={d.i2 === active}
              className={`grid cursor-pointer grid-cols-[1fr_auto] items-center gap-x-3 px-3 py-1.5 text-[12.5px] hover:bg-[#2a2e39] ${
                d.i2 === active ? 'bg-[#2a2e39]' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block tabular-nums text-[#d1d4dc]">{when(time)}</span>
                <span className="block truncate text-[11px]">
                  <span className={color}>
                    {d.bullish ? '▲' : '▼'} {d.hidden ? 'hidden ' : ''}
                    {d.bullish ? 'bullish' : 'bearish'}
                  </span>
                  <span className="text-[#787b86]"> · </span>
                  <span className={chipColor}>{chip}</span>
                  {near ? <span className="text-[#e879f9]"> · + Δ setup</span> : null}
                </span>
                <span className="block truncate text-[11px] text-[#787b86]">
                  price {fmtPrice(d.p1)} → {fmtPrice(d.p2)} · CVD {fmtVol(d.c1)} → {fmtVol(d.c2)}
                </span>
              </span>
              <span className={`w-16 text-right tabular-nums ${move >= 0 ? 'text-[#089981]' : 'text-[#f23645]'}`}>
                {move >= 0 ? '+' : ''}
                {move.toFixed(2)}%
              </span>
            </div>
          );
        })}
        {!rows.length ? (
          <div className="px-4 py-10 text-center text-[13px] text-[#787b86]">
            {missing === 'indicator' ? (
              <>
                This list reads the CVD indicator.
                <button
                  type="button"
                  onClick={onAddIndicator}
                  className="mt-3 block w-full rounded border border-[#363a45] py-1.5 text-[#d1d4dc] hover:bg-[#2a2e39]"
                >
                  Add CVD indicator
                </button>
              </>
            ) : missing === 'off' ? (
              'Divergences are Off in the CVD indicator settings.'
            ) : enabled ? (
              'No divergences on these candles with the current settings. Only candles with 5+ minute-bars under them are used, so older history is skipped.'
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
