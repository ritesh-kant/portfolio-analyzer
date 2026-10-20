'use client';

import Link from 'next/link';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { TIMEFRAMES } from '../../lib/tv/timeframes';
import { Icons } from './icons';

export type ChartType = 'candles' | 'hollow' | 'bars' | 'line' | 'area' | 'ha';

export const CHART_TYPES: { id: ChartType; label: string; icon: (p: { size?: number }) => ReactNode }[] = [
  { id: 'candles', label: 'Candles', icon: Icons.candles },
  { id: 'hollow', label: 'Hollow candles', icon: Icons.candles },
  { id: 'ha', label: 'Heikin Ashi', icon: Icons.candles },
  { id: 'bars', label: 'Bars', icon: Icons.bars },
  { id: 'line', label: 'Line', icon: Icons.line },
  { id: 'area', label: 'Area', icon: Icons.area },
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

function Menu({ trigger, children, align = 'left' }: { trigger: (open: boolean) => ReactNode; children: (close: () => void) => ReactNode; align?: 'left' | 'right' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', off);
    return () => window.removeEventListener('mousedown', off);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <div onClick={() => setOpen((o) => !o)}>{trigger(open)}</div>
      {open ? (
        <div
          className={`absolute top-9 z-50 min-w-44 rounded-md border border-[#363a45] bg-[#1e222d] py-1 shadow-xl ${align === 'right' ? 'right-0' : 'left-0'}`}
        >
          {children(() => setOpen(false))}
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
  onNews: () => void;
  footprint: FootprintUi;
  onFootprint: (patch: Partial<FootprintUi>) => void;
  onFootprintStep: (dir: 1 | -1) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onScreenshot: () => void;
  onFullscreen: () => void;
}

export function TopBar(p: Props) {
  const currentType = CHART_TYPES.find((c) => c.id === p.chartType)!;
  const TypeIcon = currentType.icon;
  const favs = TIMEFRAMES.filter((t) => FAVORITES.includes(t.id));
  const extra = !FAVORITES.includes(p.tfId) ? TIMEFRAMES.find((t) => t.id === p.tfId) : undefined;
  return (
    <div className="flex h-11 shrink-0 items-center gap-0.5 border-b border-[#2a2e39] bg-[#131722] px-2 text-[#d1d4dc]">
      <Link href="/dashboard" title="Back to dashboard" className="mr-1 flex h-8 w-8 items-center justify-center rounded-full bg-[#2962ff] text-white hover:bg-[#1e4fd6]">
        <Icons.home size={18} />
      </Link>
      <button type="button" className={`${tbtn()} min-w-[110px] font-semibold`} onClick={p.onOpenSearch} title="Symbol search">
        <Icons.search size={18} />
        {p.symbol}
      </button>
      <button type="button" className={tbtn()} onClick={p.onOpenSearch} title="Change symbol">
        <Icons.plus size={18} />
      </button>
      <Sep />
      {favs.map((t) => (
        <button key={t.id} type="button" className={tbtn(p.tfId === t.id)} onClick={() => p.onTf(t.id)}>
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
              {t.label === 'D' ? '1 day' : t.label === 'W' ? '1 week' : t.label === 'M' ? '1 month' : t.label.replace('m', ' minute').replace('h', ' hour')}
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
                  p.onChartType(c.id);
                  close();
                }}
                className={`flex w-full items-center gap-3 px-4 py-1.5 text-left text-[13px] hover:bg-[#2a2e39] ${p.chartType === c.id ? 'text-[#2962ff]' : ''}`}
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
        Indicators
      </button>
      <button type="button" className={tbtn(p.news)} onClick={p.onNews} title="Company filings (NSE announcements / SEC EDGAR)">
        <Icons.bolt size={20} />
        News
      </button>
      <div className="flex items-center">
        <button
          type="button"
          className={`${tbtn(p.footprint.on)} rounded-r-none`}
          onClick={() => p.onFootprint({ on: !p.footprint.on })}
          title="Volume footprint (bid × ask per price level)"
        >
          <Icons.footprint size={20} />
          Footprint
        </button>
        <Menu
          trigger={() => (
            <button type="button" className={`${tbtn(p.footprint.on)} rounded-l-none px-1`} title="Footprint settings">
              <Icons.chevron size={16} />
            </button>
          )}
        >
          {() => (
            <div className="w-64 space-y-3 px-4 py-3 text-[13px]" onClick={(e) => e.stopPropagation()}>
              <div className="font-semibold">Footprint settings</div>
              <div className="flex items-center justify-between">
                <span>Row size</span>
                <span className="flex items-center gap-1">
                  <button type="button" className="h-6 w-6 rounded bg-[#2a2e39] hover:bg-[#363a45]" onClick={() => p.onFootprintStep(-1)}>
                    −
                  </button>
                  <span className="w-14 text-center tabular-nums">{p.footprint.step}</span>
                  <button type="button" className="h-6 w-6 rounded bg-[#2a2e39] hover:bg-[#363a45]" onClick={() => p.onFootprintStep(1)}>
                    +
                  </button>
                </span>
              </div>
              <label className="flex items-center justify-between">
                Auto row size
                <input type="checkbox" checked={p.footprint.auto} onChange={(e) => p.onFootprint({ auto: e.target.checked })} />
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
                <input type="checkbox" checked={p.footprint.showDelta} onChange={(e) => p.onFootprint({ showDelta: e.target.checked })} />
              </label>
              <p className="text-[11px] leading-snug text-[#787b86]">
                Estimated from 1-minute bars: this data source has no tick or bid/ask feed, so each minute&apos;s volume
                is spread over the rows it traded through. Needs the last ~7 sessions.
              </p>
            </div>
          )}
        </Menu>
      </div>
      <Sep />
      <button type="button" className={`${tbtn()} disabled:opacity-35`} onClick={p.onUndo} disabled={!p.canUndo} title="Undo (Ctrl+Z)">
        <Icons.undo size={20} />
      </button>
      <button type="button" className={`${tbtn()} disabled:opacity-35`} onClick={p.onRedo} disabled={!p.canRedo} title="Redo (Ctrl+Shift+Z)">
        <Icons.redo size={20} />
      </button>
      <div className="flex-1" />
      <button type="button" className={tbtn()} onClick={p.onFullscreen} title="Fullscreen">
        <Icons.fullscreen size={20} />
      </button>
      <button type="button" className={tbtn()} onClick={p.onScreenshot} title="Download chart image">
        <Icons.camera size={20} />
      </button>
    </div>
  );
}
