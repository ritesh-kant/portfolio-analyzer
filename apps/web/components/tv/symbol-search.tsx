'use client';

import { useEffect, useRef, useState } from 'react';
import { load, save } from '../../lib/tv/storage';
import type { SearchHit } from '../../lib/tv/types';
import { Icons } from './icons';

const FAVORITES_KEY = 'tv:symbolFavorites';

const SUGGESTED: SearchHit[] = [
  { symbol: '^NSEI', name: 'NIFTY 50', exchange: 'NSE', type: 'INDEX' },
  { symbol: '^NSEBANK', name: 'NIFTY BANK', exchange: 'NSE', type: 'INDEX' },
  { symbol: 'RELIANCE.NS', name: 'Reliance Industries', exchange: 'NSE', type: 'EQUITY' },
  { symbol: 'HDFCBANK.NS', name: 'HDFC Bank', exchange: 'NSE', type: 'EQUITY' },
  { symbol: 'TCS.NS', name: 'Tata Consultancy Services', exchange: 'NSE', type: 'EQUITY' },
  { symbol: 'DEVYANI.NS', name: 'Devyani International', exchange: 'NSE', type: 'EQUITY' },
  { symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', type: 'EQUITY' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation', exchange: 'NASDAQ', type: 'EQUITY' },
  { symbol: 'TSLA', name: 'Tesla, Inc.', exchange: 'NASDAQ', type: 'EQUITY' },
  { symbol: 'SPY', name: 'SPDR S&P 500 ETF', exchange: 'NYSE Arca', type: 'ETF' },
  { symbol: 'BTC-USD', name: 'Bitcoin USD', exchange: 'CCC', type: 'CRYPTOCURRENCY' },
];

type Tab = 'all' | 'favorites' | 'stocks' | 'fno' | 'commodity' | 'etfs';

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'favorites', label: 'Favorites' },
  { id: 'stocks', label: 'Stocks' },
  { id: 'fno', label: 'F&O' },
  { id: 'commodity', label: 'Commodity' },
  { id: 'etfs', label: 'ETFs' },
];

const isFuture = (h: SearchHit) => /^future/i.test(h.type);
// Yahoo commodity futures look like GC=F; NSE futures look like RELIANCE1!.NFO / RELIANCE26NOVFUT.NFO.
const isCommodity = (h: SearchHit) => isFuture(h) && /=F$/i.test(h.symbol);
const isFno = (h: SearchHit) => isFuture(h) && !isCommodity(h);

function matchesTab(h: SearchHit, tab: Tab): boolean {
  switch (tab) {
    case 'stocks':
      return /^equity$/i.test(h.type);
    case 'fno':
      return isFno(h);
    case 'commodity':
      return isCommodity(h);
    case 'etfs':
      return /^etf$/i.test(h.type);
    default:
      return true;
  }
}

interface Props {
  onPick: (symbol: string, name?: string) => void;
  onClose: () => void;
}

export function SymbolSearch({ onPick, onClose }: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>(SUGGESTED);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const [tab, setTab] = useState<Tab>('all');
  const [favorites, setFavorites] = useState<SearchHit[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const searchFavorites = tab === 'favorites';
  const searchQuery = query.trim();

  useEffect(() => {
    setFavorites(load<SearchHit[]>(FAVORITES_KEY, []));
    input.current?.focus();
  }, []);

  useEffect(() => {
    if (searchFavorites || !searchQuery) {
      setLoading(false);
      setHits(SUGGESTED);
      return;
    }
    const ctl = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/chart/search?q=${encodeURIComponent(searchQuery)}`, {
          signal: ctl.signal,
        });
        const body = (await res.json()) as { hits: SearchHit[] };
        if (ctl.signal.aborted) return;
        setHits(body.hits);
        setActive(0);
      } catch {
        // aborted or offline — keep the previous list
      } finally {
        if (!ctl.signal.aborted) setLoading(false);
      }
    }, 200);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [searchQuery, searchFavorites]);

  const favoriteSymbols = new Set(favorites.map((h) => h.symbol.toUpperCase()));
  const favoriteQuery = searchQuery.toUpperCase();
  const shown = searchFavorites
    ? favorites.filter((h) =>
        [h.symbol, h.display, h.name].some((value) => value?.toUpperCase().includes(favoriteQuery)),
      )
    : hits.filter((h) => matchesTab(h, tab));

  const toggleFavorite = (hit: SearchHit) => {
    const symbol = hit.symbol.toUpperCase();
    const next = favoriteSymbols.has(symbol)
      ? favorites.filter((h) => h.symbol.toUpperCase() !== symbol)
      : [...favorites, hit];
    setFavorites(next);
    save(FAVORITES_KEY, next);
    if (searchFavorites) setActive(0);
  };

  const choose = (symbol: string, name?: string) => {
    onPick(symbol.toUpperCase(), name);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 pt-[10vh]"
      onMouseDown={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
    >
      <div
        role="dialog"
        aria-label="Symbol Search"
        className="flex max-h-[70vh] w-[min(640px,94vw)] flex-col overflow-hidden rounded-lg border border-[#363a45] bg-[#1e222d] shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 pt-4 text-[18px] font-semibold text-[#d1d4dc]">
          Symbol Search
          <button
            type="button"
            onClick={onClose}
            className="text-[#787b86] hover:text-white"
            aria-label="Close"
          >
            <Icons.close size={22} />
          </button>
        </div>
        <div className="flex items-center gap-3 border-b border-[#2a2e39] px-5 py-3 text-[#787b86]">
          <Icons.search size={22} />
          <input
            ref={input}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') setActive((a) => Math.max(0, Math.min(shown.length - 1, a + 1)));
              else if (e.key === 'ArrowUp') setActive((a) => Math.max(0, a - 1));
              else if (e.key === 'Enter') {
                const pick = shown[active]?.symbol ?? (searchFavorites ? '' : searchQuery);
                if (pick) choose(pick, shown[active]?.name);
              }
            }}
            placeholder={searchFavorites ? 'Search favorites' : 'Search — e.g. RELIANCE, TCS.NS, AAPL'}
            className="w-full bg-transparent text-[16px] text-[#d1d4dc] outline-none placeholder:text-[#5d606b]"
          />
          {loading ? <span className="text-xs">…</span> : null}
        </div>
        <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-[#2a2e39] px-3 pt-2 sm:px-5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={tab === t.id}
              onClick={() => {
                setTab(t.id);
                setActive(0);
              }}
              className={`shrink-0 border-b-2 px-2 py-2 text-[14px] font-semibold sm:px-3 ${
                tab === t.id
                  ? 'border-[#d1d4dc] text-[#d1d4dc]'
                  : 'border-transparent text-[#787b86] hover:text-[#d1d4dc]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="overflow-y-auto">
          {shown.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-[#787b86]">
              {searchFavorites && favorites.length === 0
                ? 'Star a symbol in search to add it here.'
                : searchQuery
                  ? `No ${tab === 'all' ? 'symbols' : TABS.find((t) => t.id === tab)?.label.toLowerCase()} match “${searchQuery}”.`
                  : 'Nothing here — try another tab.'}
            </div>
          ) : (
            shown.map((h, i) => {
              const label = h.display ?? h.symbol;
              const isFavorite = favoriteSymbols.has(h.symbol.toUpperCase());
              return (
                <div
                  key={`${h.symbol}-${h.exchange}`}
                  onMouseEnter={() => setActive(i)}
                  className={`flex items-center pr-3 sm:pr-5 ${i === active ? 'bg-[#2a2e39]' : ''}`}
                >
                  <button
                    type="button"
                    onClick={() => choose(h.symbol, h.name)}
                    className="flex min-w-0 flex-1 items-center gap-2 py-2 pl-3 pr-1 text-left sm:gap-4 sm:pl-5"
                  >
                    <span
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white"
                      style={{ background: h.color ?? '#363a45' }}
                    >
                      {label.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1 sm:contents">
                      <span
                        className="block truncate text-[14px] font-semibold text-[#d1d4dc] sm:w-44 sm:shrink-0"
                        title={label}
                      >
                        {label}
                      </span>
                      <span className="block min-w-0 flex-1 truncate text-[13px] text-[#9598a1]">{h.name}</span>
                    </span>
                    <span className="hidden shrink-0 text-[12px] text-[#787b86] sm:inline">
                      {h.type.toLowerCase()} · {h.exchange}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (searchFavorites) input.current?.focus();
                      toggleFavorite(h);
                    }}
                    aria-label={`Favorite ${label}`}
                    aria-pressed={isFavorite}
                    title={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded hover:bg-[#363a45] hover:text-[#f0b90b] ${
                      isFavorite ? 'text-[#f0b90b]' : 'text-[#9598a1]'
                    }`}
                  >
                    <Icons.star size={18} filled={isFavorite} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
