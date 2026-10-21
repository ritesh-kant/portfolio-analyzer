'use client';

import { useEffect, useRef, useState } from 'react';
import type { SearchHit } from '../../lib/tv/types';
import { Icons } from './icons';

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

interface Props {
  onPick: (symbol: string, name?: string) => void;
  onClose: () => void;
}

export function SymbolSearch({ onPick, onClose }: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>(SUGGESTED);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits(SUGGESTED);
      setActive(0);
      return;
    }
    const ctl = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/chart/search?q=${encodeURIComponent(q)}`, {
          signal: ctl.signal,
        });
        const body = (await res.json()) as { hits: SearchHit[] };
        setHits(body.hits);
        setActive(0);
      } catch {
        // aborted or offline — keep the previous list
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [query]);

  const choose = (symbol: string, name?: string) => {
    onPick(symbol.toUpperCase(), name);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 pt-[10vh]"
      onMouseDown={onClose}
    >
      <div
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
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose();
              else if (e.key === 'ArrowDown') setActive((a) => Math.min(hits.length - 1, a + 1));
              else if (e.key === 'ArrowUp') setActive((a) => Math.max(0, a - 1));
              else if (e.key === 'Enter') {
                const pick = hits[active]?.symbol ?? query.trim();
                if (pick) choose(pick, hits[active]?.name);
              }
            }}
            placeholder="Search — e.g. RELIANCE, TCS.NS, AAPL"
            className="w-full bg-transparent text-[16px] text-[#d1d4dc] outline-none placeholder:text-[#5d606b]"
          />
          {loading ? <span className="text-xs">…</span> : null}
        </div>
        <div className="overflow-y-auto">
          {hits.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-[#787b86]">
              No symbols match “{query}”.
            </div>
          ) : (
            hits.map((h, i) => (
              <button
                key={`${h.symbol}-${h.exchange}`}
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(h.symbol, h.name)}
                className={`flex w-full items-center gap-4 px-5 py-2 text-left ${i === active ? 'bg-[#2a2e39]' : ''}`}
              >
                <span
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white"
                  style={{ background: h.color ?? '#363a45' }}
                >
                  {(h.display ?? h.symbol)
                    .replace(/[^A-Za-z]/g, '')
                    .slice(0, 2)
                    .toUpperCase()}
                </span>
                <span className="w-28 shrink-0 text-[14px] font-semibold text-[#d1d4dc]">
                  {h.display ?? h.symbol}
                </span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-[#9598a1]">{h.name}</span>
                <span className="shrink-0 text-[12px] text-[#787b86]">
                  {h.type.toLowerCase()} · {h.exchange}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
