'use client';

import Link from 'next/link';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { TIMEFRAMES } from '../../lib/tv/timeframes';
import { Icons } from './icons';

export type ChartType = 'candles' | 'hollow' | 'bars' | 'line' | 'area' | 'ha';

/** Chart-type menu entries: the plain types plus Volume footprint (a mode of the candles, with its own settings). */
export type ChartMenuId = ChartType | 'footprint';

export const CHART_TYPES: {
  id: ChartMenuId;
  label: string;
  icon: (p: { size?: number }) => ReactNode;
}[] = [
  { id: 'candles', label: 'Candles', icon: Icons.candles },
  { id: 'hollow', label: 'Hollow candles', icon: Icons.candles },
  { id: 'ha', label: 'Heikin Ashi', icon: Icons.candles },
  { id: 'bars', label: 'Bars', icon: Icons.bars },
  { id: 'line', label: 'Line', icon: Icons.line },
  { id: 'area', label: 'Area', icon: Icons.area },
  { id: 'footprint', label: 'Volume footprint', icon: Icons.footprint },
];

export interface FootprintUi {
  on: boolean;
  auto: boolean;
  step: number;
  imbalance: number;
  showDelta: boolean;
}

const FAVORITES = ['1m', '5m', '15m', '1h', '1D'];

const tbtn = (active = false) =>
  `flex h-8 items-center gap-1.5 rounded px-2.5 text-[13px] font-medium transition-colors ${
    active ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#d1d4dc] hover:bg-[#2a2e39]'
  }`;

function Menu({
  trigger,
  children,
}: {
  trigger: (open: boolean) => ReactNode;
  children: (close: () => void) => ReactNode;
}) {
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const open = pos != null;
  useEffect(() => {
    if (!open) return;
    const off = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPos(null);
    };
    window.addEventListener('mousedown', off);
    window.addEventListener('touchstart', off);
    return () => {
      window.removeEventListener('mousedown', off);
      window.removeEventListener('touchstart', off);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative shrink-0">
      <div
        onClick={(e) => {
          if (open) return setPos(null);
          // The bar scrolls sideways on phones, which would clip an absolutely positioned menu,
          // so the menu is placed in viewport coordinates and kept on screen.
          const rect = e.currentTarget.getBoundingClientRect();
          setPos({
            left: Math.max(8, Math.min(rect.left, window.innerWidth - 272)),
            top: rect.bottom + 4,
          });
        }}
      >
        {trigger(open)}
      </div>
      {pos ? (
        <div
          className="fixed z-50 max-h-[70vh] min-w-44 overflow-y-auto rounded-md border border-[#363a45] bg-[#1e222d] py-1 shadow-xl"
          style={{ left: pos.left, top: pos.top }}
        >
          {children(() => setPos(null))}
        </div>
      ) : null}
    </div>
  );
}

const Sep = () => <div className="mx-1 h-5 w-px bg-[#2a2e39]" />;

interface Props {
  symbol: string;
  onOpenSearch: () => void;
  tfId: string;
  onTf: (id: string) => void;
  chartType: ChartType;
  onChartType: (t: ChartType) => void;
  onIndicators: () => void;
  news: boolean;
  sessions: boolean;
  onSessions: () => void;
  onNews: () => void;
  replay: boolean;
  onReplay: () => void;
  footprint: FootprintUi;
  onFootprint: (patch: Partial<FootprintUi>) => void;
  onFootprintStep: (dir: 1 | -1) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onScreenshot: () => void;
  onFullscreen: () => void;
  /** Phones have no right rail, so the watchlist opens from here. */
  watchlistOpen?: boolean;
  onWatchlist?: () => void;
}

export function TopBar(p: Props) {
  const activeId: ChartMenuId = p.footprint.on ? 'footprint' : p.chartType;
  const currentType = CHART_TYPES.find((c) => c.id === activeId)!;
  const TypeIcon = currentType.icon;
  const favs = TIMEFRAMES.filter((t) => FAVORITES.includes(t.id));
  const extra = !FAVORITES.includes(p.tfId) ? TIMEFRAMES.find((t) => t.id === p.tfId) : undefined;
  return (
    <div className="scrollbar-none flex h-11 shrink-0 items-center gap-0.5 overflow-x-auto border-b border-[#2a2e39] bg-[#131722] px-2 text-[#d1d4dc] [&>*]:shrink-0">
      <Link
        href="/dashboard"
        title="Back to dashboard"
        className="mr-1 flex h-8 w-8 items-center justify-center rounded-full bg-[#2962ff] text-white hover:bg-[#1e4fd6]"
      >
        <Icons.home size={18} />
      </Link>
      <button
        type="button"
        className={`${tbtn()} font-semibold md:min-w-[110px]`}
        onClick={p.onOpenSearch}
        title="Symbol search"
      >
        <Icons.search size={18} />
        {p.symbol}
      </button>
      <button type="button" className={tbtn()} onClick={p.onOpenSearch} title="Change symbol">
        <Icons.plus size={18} />
      </button>
      <Sep />
      {favs.map((t) => (
        <button
          key={t.id}
          type="button"
          className={tbtn(p.tfId === t.id)}
          onClick={() => p.onTf(t.id)}
        >
          {t.label}
        </button>
      ))}
      {extra ? (
        <button type="button" className={tbtn(true)} onClick={() => p.onTf(extra.id)}>
          {extra.label}
        </button>
      ) : null}
      <Menu
        trigger={() => (
          <button type="button" className={`${tbtn()} px-1`} title="Time interval">
            <Icons.chevron size={16} />
          </button>
        )}
      >
        {(close) =>
          TIMEFRAMES.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                p.onTf(t.id);
                close();
              }}
              className={`flex w-full px-4 py-1.5 text-left text-[13px] hover:bg-[#2a2e39] ${p.tfId === t.id ? 'text-[#2962ff]' : ''}`}
            >
              {t.label === 'D'
                ? '1 day'
                : t.label === 'W'
                  ? '1 week'
                  : t.label === 'M'
                    ? '1 month'
                    : t.label.replace('m', ' minute').replace('h', ' hour')}
            </button>
          ))
        }
      </Menu>
      <Sep />
      <Menu
        trigger={() => (
          <button type="button" className={tbtn()} title="Chart type">
            <TypeIcon size={20} />
          </button>
        )}
      >
        {(close) =>
          CHART_TYPES.map((c) => {
            const I = c.icon;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  if (c.id === 'footprint') {
                    p.onFootprint({ on: true });
                  } else {
                    p.onChartType(c.id);
                    p.onFootprint({ on: false });
                  }
                  close();
                }}
                className={`flex w-full items-center gap-3 px-4 py-1.5 text-left text-[13px] hover:bg-[#2a2e39] ${activeId === c.id ? 'text-[#2962ff]' : ''}`}
              >
                <I size={18} />
                {c.label}
              </button>
            );
          })
        }
      </Menu>
      <Sep />
      <button type="button" className={tbtn()} onClick={p.onIndicators}>
        <Icons.indicators size={20} />
        <span className="hidden md:inline">Indicators</span>
      </button>
      <button
        type="button"
        className={tbtn(p.news)}
        onClick={p.onNews}
        title="Company filings (NSE announcements / SEC EDGAR)"
      >
        <Icons.bolt size={20} />
        <span className="hidden md:inline">News</span>
      </button>
      <button
        type="button"
        className={tbtn(p.sessions)}
        onClick={p.onSessions}
        title="Session shading: tint hours outside regular trading, dash each trading-day start"
      >
        <Icons.sessions size={20} />
        <span className="hidden md:inline">Sessions</span>
      </button>
      <button
        type="button"
        className={tbtn(p.replay)}
        onClick={p.onReplay}
        title="Replay: step through history candle by candle"
      >
        <Icons.replay size={20} />
        <span className="hidden md:inline">Replay</span>
      </button>
      {p.footprint.on ? (
        <Menu
          trigger={() => (
            <button type="button" className={tbtn(true)} title="Volume footprint settings">
              <Icons.gear size={18} />
              <span className="hidden md:inline">Footprint</span>
            </button>
          )}
        >
          {() => (
            <div
              className="w-64 space-y-3 px-4 py-3 text-[13px]"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="font-semibold">Footprint settings</div>
              <div className="flex items-center justify-between">
                <span>Row size</span>
                <span className="flex items-center gap-1">
                  <button
                    type="button"
                    className="h-6 w-6 rounded bg-[#2a2e39] hover:bg-[#363a45]"
                    onClick={() => p.onFootprintStep(-1)}
                  >
                    −
                  </button>
                  <span className="w-14 text-center tabular-nums">{p.footprint.step}</span>
                  <button
                    type="button"
                    className="h-6 w-6 rounded bg-[#2a2e39] hover:bg-[#363a45]"
                    onClick={() => p.onFootprintStep(1)}
                  >
                    +
                  </button>
                </span>
              </div>
              <label className="flex items-center justify-between">
                Auto row size
                <input
                  type="checkbox"
                  checked={p.footprint.auto}
                  onChange={(e) => p.onFootprint({ auto: e.target.checked })}
                />
              </label>
              <label className="flex items-center justify-between">
                Imbalance
                <select
                  value={p.footprint.imbalance}
                  onChange={(e) => p.onFootprint({ imbalance: Number(e.target.value) })}
                  className="rounded border border-[#363a45] bg-[#131722] px-2 py-0.5"
                >
                  {[2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>
                      {n * 100}%
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center justify-between">
                Delta &amp; volume
                <input
                  type="checkbox"
                  checked={p.footprint.showDelta}
                  onChange={(e) => p.onFootprint({ showDelta: e.target.checked })}
                />
              </label>
              <p className="text-[11px] leading-snug text-[#787b86]">
                Estimated from 1-minute bars: this data source has no tick or bid/ask feed, so each
                minute&apos;s volume is spread over the rows it traded through. Needs the last ~7
                sessions.
              </p>
            </div>
          )}
        </Menu>
      ) : null}
      <Sep />
      <button
        type="button"
        className={`${tbtn()} disabled:opacity-35`}
        onClick={p.onUndo}
        disabled={!p.canUndo}
        title="Undo (Ctrl+Z)"
      >
        <Icons.undo size={20} />
      </button>
      <button
        type="button"
        className={`${tbtn()} disabled:opacity-35`}
        onClick={p.onRedo}
        disabled={!p.canRedo}
        title="Redo (Ctrl+Shift+Z)"
      >
        <Icons.redo size={20} />
      </button>
      <div className="flex-1" />
      {p.onWatchlist ? (
        <button
          type="button"
          className={`${tbtn(!!p.watchlistOpen)} md:hidden`}
          onClick={p.onWatchlist}
          title="Watchlist"
        >
          <Icons.list size={20} />
        </button>
      ) : null}
      <button type="button" className={tbtn()} onClick={p.onFullscreen} title="Fullscreen">
        <Icons.fullscreen size={20} />
      </button>
      <button
        type="button"
        className={tbtn()}
        onClick={p.onScreenshot}
        title="Download chart image"
      >
        <Icons.camera size={20} />
      </button>
    </div>
  );
}
