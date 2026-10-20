'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Quote } from '../../app/api/chart/quotes/route';
import { load, save } from '../../lib/tv/storage';
import { Icons } from './icons';

export interface WatchItem {
  symbol: string;
  name: string;
}

export const DEFAULT_WATCHLIST: WatchItem[] = [
  { symbol: '^NSEI', name: 'NIFTY 50' },
  { symbol: '^NSEBANK', name: 'NIFTY BANK' },
  { symbol: 'RELIANCE.NS', name: 'Reliance Industries' },
  { symbol: 'HDFCBANK.NS', name: 'HDFC Bank' },
  { symbol: 'TCS.NS', name: 'Tata Consultancy Services' },
  { symbol: 'INFY.NS', name: 'Infosys' },
  { symbol: 'AAPL', name: 'Apple Inc.' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation' },
  { symbol: 'GC=F', name: 'Gold — COMEX futures' },
];

const STORAGE_KEY = 'tv:watchlist';
const WIDTH_KEY = 'tv:watchWidth';
export const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));
const short = (symbol: string) => symbol.replace(/\.(NS|BO)$/, '');

const fmtPrice = (p: number) =>
  p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: p < 1 ? 4 : 2 });

interface Props {
  active: string;
  onSelect: (symbol: string) => void;
  onAdd: () => void;
  /** Symbols to add from the search dialog arrive here (set by the parent). */
  pending: WatchItem | null;
  onPendingHandled: () => void;
}

export function WatchlistPanel({ active, onSelect, onAdd, pending, onPendingHandled }: Props) {
  const [items, setItems] = useState<WatchItem[]>(DEFAULT_WATCHLIST);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  widthRef.current = width;

  useEffect(() => {
    setItems(load<WatchItem[]>(STORAGE_KEY, DEFAULT_WATCHLIST));
    setWidth(clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)));
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (loaded) save(STORAGE_KEY, items);
  }, [items, loaded]);

  useEffect(() => {
    if (!pending) return;
    setItems((cur) => (cur.some((i) => i.symbol === pending.symbol) ? cur : [...cur, pending]));
    onPendingHandled();
  }, [pending, onPendingHandled]);

  const symbolsKey = items.map((i) => i.symbol).join(',');
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!symbolsKey) return;
      try {
        const res = await fetch(`/api/chart/quotes?symbols=${encodeURIComponent(symbolsKey)}`, {
          signal,
          cache: 'no-store',
        });
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as { quotes: Record<string, Quote> };
        setQuotes((cur) => ({ ...cur, ...body.quotes }));
        setFailed(false);
      } catch {
        if (!signal?.aborted) setFailed(true);
      }
    },
    [symbolsKey],
  );

  useEffect(() => {
    if (!loaded) return;
    const ctl = new AbortController();
    void refresh(ctl.signal);
    const timer = setInterval(() => {
      if (!document.hidden) void refresh(ctl.signal);
    }, 5000);
    return () => {
      ctl.abort();
      clearInterval(timer);
    };
  }, [loaded, refresh]);

  /** Drag the panel's left edge: moving left widens it. */
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
        aria-label="Resize watchlist"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          save(WIDTH_KEY, DEFAULT_WIDTH);
        }}
        className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#2962ff]/60 ${
          dragging ? 'bg-[#2962ff]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-col-resize" /> : null}
      <div className="flex h-11 items-center justify-between border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-semibold text-[#d1d4dc]">Watchlist</span>
        <button
          type="button"
          onClick={onAdd}
          title="Add symbol"
          className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
        >
          <Icons.plus size={18} />
        </button>
      </div>
      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 border-b border-[#2a2e39] px-3 py-1.5 text-[11px] font-medium text-[#787b86]">
        <span>Symbol</span>
        <span className="w-16 text-right">Last</span>
        <span className="w-14 text-right">Chg</span>
        <span className="w-14 text-right">Chg%</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {items.map((item) => {
          const q = quotes[item.symbol];
          const up = q ? q.change >= 0 : true;
          const color = !q ? 'text-[#787b86]' : up ? 'text-[#089981]' : 'text-[#f23645]';
          return (
            <div
              key={item.symbol}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(item.symbol)}
              onKeyDown={(e) => e.key === 'Enter' && onSelect(item.symbol)}
              title={item.name}
              className={`group relative grid cursor-pointer grid-cols-[1fr_auto_auto_auto] items-center gap-x-3 px-3 py-1.5 text-[13px] hover:bg-[#2a2e39] ${
                item.symbol === active ? 'bg-[#2a2e39]' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block truncate font-semibold text-[#d1d4dc]">
                  {short(item.symbol)}
                </span>
                <span className="block truncate text-[11px] text-[#787b86]">{item.name}</span>
              </span>
              <span className="w-16 text-right tabular-nums text-[#d1d4dc]">
                {q ? fmtPrice(q.price) : '—'}
              </span>
              <span className={`w-14 text-right tabular-nums ${color}`}>
                {q ? `${up ? '+' : ''}${q.change.toFixed(2)}` : ''}
              </span>
              <span className={`w-14 text-right tabular-nums ${color}`}>
                {q ? `${up ? '+' : ''}${q.changePct.toFixed(2)}%` : ''}
              </span>
              <button
                type="button"
                aria-label={`Remove ${item.symbol}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setItems((cur) => cur.filter((i) => i.symbol !== item.symbol));
                }}
                className="absolute right-1 top-1 hidden h-5 w-5 items-center justify-center rounded bg-[#1e222d] text-[#787b86] hover:text-[#f23645] group-hover:flex"
              >
                <Icons.close size={12} />
              </button>
            </div>
          );
        })}
        {!items.length ? (
          <div className="px-4 py-10 text-center text-[13px] text-[#787b86]">
            Your watchlist is empty. Use + to add a symbol.
          </div>
        ) : null}
      </div>
      {failed ? (
        <div className="border-t border-[#2a2e39] px-3 py-1.5 text-[11px] text-[#ffb74d]">
          Prices unavailable — retrying…
        </div>
      ) : null}
    </div>
  );
}
