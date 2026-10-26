'use client';

import { useEffect, useState } from 'react';

interface BrokerInfo {
  id: string;
  name: string;
  configured: boolean;
  connected: boolean;
}

interface Card {
  id: string;
  name: string;
  /** One line under the name. */
  blurb: string;
  /** Brand-ish colours for the monogram tile. */
  bg: string;
  fg: string;
  mark: string;
  /** No integration yet: shown, but not clickable. */
  soon?: boolean;
}

/** The catalogue the dialog shows; `paper` is built in, the rest map to /api/broker ids. */
const CARDS: Card[] = [
  { id: 'paper', name: 'Paper Trading', blurb: 'Simulated account on this chart', bg: '#ffffff', fg: '#131722', mark: 'TV' },
  { id: 'upstox', name: 'Upstox', blurb: 'NSE equity, real orders', bg: '#5b2d9f', fg: '#ffffff', mark: 'up' },
  { id: 'kite', name: 'Zerodha Kite', blurb: 'NSE equity, real orders', bg: '#387ed1', fg: '#ffffff', mark: 'Z' },
  { id: 'ibkr', name: 'Interactive Brokers', blurb: 'US stocks', bg: '#d81222', fg: '#ffffff', mark: 'IB', soon: true },
  { id: 'groww', name: 'Groww', blurb: 'Paid API plan', bg: '#00b386', fg: '#ffffff', mark: 'G', soon: true },
];

/** TradingView's "Trade with your broker" picker: one card per broker, with its connection state. */
export function TradeDialog({
  onClose,
  onPaper,
  onBroker,
}: {
  onClose: () => void;
  onPaper: () => void;
  /** Open the live panel on this broker. */
  onBroker: (id: string) => void;
}) {
  const [brokers, setBrokers] = useState<BrokerInfo[] | null>(null);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [note, setNote] = useState<{ id: string; text: string } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch('/api/broker', { cache: 'no-store' })
      .then(async (r) => {
        const body = (await r.json()) as { brokers?: BrokerInfo[]; error?: string };
        if (!r.ok || !body.brokers) throw new Error(body.error ?? `HTTP ${r.status}`);
        if (alive) setBrokers(body.brokers);
      })
      .catch((e: Error) => alive && setFailed(e.message));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const status = (c: Card): { label: string; tone: string } | null => {
    if (c.id === 'paper') return null;
    if (c.soon) return { label: 'Coming soon', tone: '#787b86' };
    const b = brokers?.find((x) => x.id === c.id);
    if (!b) return failed ? { label: 'Unavailable', tone: '#f23645' } : null;
    if (!b.configured) return { label: 'Not set up', tone: '#ffb74d' };
    return b.connected ? { label: 'Connected', tone: '#089981' } : { label: 'Connect', tone: '#2962ff' };
  };

  const pick = (c: Card) => {
    if (c.soon) return;
    if (c.id === 'paper') {
      onPaper();
      onClose();
      return;
    }
    const b = brokers?.find((x) => x.id === c.id);
    if (!b) return;
    if (!b.configured) {
      setNote({ id: c.id, text: `${c.name} API key and secret are not set on the server. See .env.example.` });
      return;
    }
    if (b.connected) {
      onBroker(c.id);
      onClose();
      return;
    }
    // sign in at the broker; its redirect brings the browser back to /chart
    window.location.href = `/api/broker/${c.id}/login`;
  };

  const shown = CARDS.filter((c) => c.name.toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/60 p-3 sm:p-8"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Trade with your broker"
        className="relative my-auto w-full max-w-[860px] rounded-xl border border-[#2a2e39] bg-[#1e222d] p-5 shadow-2xl sm:p-8"
      >
        <div className="mb-5 flex items-center gap-3">
          {searching ? (
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search brokers"
              className="h-9 min-w-0 flex-1 rounded border border-[#363a45] bg-[#131722] px-3 text-[14px] text-[#d1d4dc] outline-none focus:border-[#2962ff]"
            />
          ) : (
            <h2 className="flex-1 text-[20px] font-semibold text-white sm:text-[24px]">Trade with your broker</h2>
          )}
          <button
            type="button"
            aria-label="Search brokers"
            className="rounded p-1.5 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
            onClick={() => {
              setSearching((s) => !s);
              setQuery('');
            }}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="m13 13 4 4" strokeLinecap="round" />
            </svg>
          </button>
          <button
            type="button"
            aria-label="Close"
            className="rounded p-1.5 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
            onClick={onClose}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <path d="m4 4 12 12M16 4 4 16" />
            </svg>
          </button>
        </div>

        {failed ? <p className="mb-3 text-[12px] text-[#f23645]">Broker status unavailable: {failed}</p> : null}

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {shown.map((c) => {
            const s = status(c);
            return (
              <button
                key={c.id}
                type="button"
                disabled={c.soon}
                onClick={() => pick(c)}
                className="flex min-h-[150px] flex-col items-center justify-center gap-2 rounded-lg border border-[#2a2e39] bg-[#131722] px-3 py-4 text-center transition-colors enabled:hover:border-[#2962ff] enabled:hover:bg-[#1a1e2a] disabled:opacity-55"
              >
                <span
                  className="flex h-12 w-12 items-center justify-center rounded-xl text-[18px] font-bold"
                  style={{ background: c.bg, color: c.fg }}
                  aria-hidden
                >
                  {c.mark}
                </span>
                <span className="text-[15px] text-[#d1d4dc]">{c.name}</span>
                <span className="text-[12px] leading-snug text-[#787b86]">{c.blurb}</span>
                {s ? (
                  <span className="text-[12px] font-medium" style={{ color: s.tone }}>
                    {s.label}
                  </span>
                ) : null}
                {note?.id === c.id ? <span className="text-[11px] leading-snug text-[#ffb74d]">{note.text}</span> : null}
              </button>
            );
          })}
        </div>
        {!shown.length ? <p className="py-8 text-center text-[#787b86]">No broker matches “{query}”.</p> : null}
      </div>
    </div>
  );
}
