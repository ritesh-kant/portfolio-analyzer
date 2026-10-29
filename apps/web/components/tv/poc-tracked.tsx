'use client';

import { useEffect, useState } from 'react';
import { MARKETS } from '../../lib/screener/markets';
import {
  evaluate,
  OPEN_MIN,
  RULES,
  summarize,
  TARGET_R,
  tradeOf,
  type TradeMarks,
  type Evaluation,
  type TrackedSetup,
} from '../../lib/screener/poc-eval';
import { zoneClock } from '../../lib/screener/relvol';
import type { ScreenerRow } from '../../lib/screener/types';
import { load, save } from '../../lib/tv/storage';
import type { Bar } from '../../lib/tv/types';
import { Icons } from './icons';

const KEY = 'tv:pocTracked';
export const loadTracked = (): TrackedSetup[] => load<TrackedSetup[]>(KEY, []);
export const saveTracked = (list: TrackedSetup[]) => save(KEY, list);

/** Session close, minutes after midnight on the exchange clock: when "tomorrow's close" has happened. */
const CLOSE_MIN: Record<string, number> = { us: 16 * 60, in: 15 * 60 + 30 };
/** The candles fetched reach a month back: a result older than this can't be scored again under a new wait. */
const REFETCH_DAYS = 25;
/** NSE intraday round trip at real costs (brokerage, STT, charges, slippage), as % of the price. */
const IN_COST_PCT = 0.2;
const REFRESH_MS = 60_000;
const GREEN = '#26a69a';
const RED = '#ef5350';
const GREY = '#868993';

/**
 * Candles per symbol, kept for a minute and shared by every mount: reopening the tab, or a hundred tracked names,
 * must not refetch everything. At most `MAX_FETCHES` run at once.
 */
const barsCache = new Map<string, { at: number; p: Promise<Bar[]> }>();
const MAX_FETCHES = 6;
let running = 0;
const queue: (() => void)[] = [];

async function limited<T>(fn: () => Promise<T>): Promise<T> {
  if (running >= MAX_FETCHES) await new Promise<void>((go) => queue.push(go));
  running += 1;
  try {
    return await fn();
  } finally {
    running -= 1;
    queue.shift()?.();
  }
}

function fetchBars(symbol: string): Promise<Bar[]> {
  const hit = barsCache.get(symbol);
  if (hit && Date.now() - hit.at < REFRESH_MS) return hit.p;
  const p = limited(async () => {
    const res = await fetch(
      `/api/chart/bars?symbol=${encodeURIComponent(symbol)}&interval=5m&range=1mo`,
      { cache: 'no-store' },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return ((await res.json()) as { bars: Bar[] }).bars;
  });
  barsCache.set(symbol, { at: Date.now(), p });
  p.catch(() => barsCache.delete(symbol));
  return p;
}

interface Props {
  items: TrackedSetup[];
  /** Only this market's setups are shown, summed and checked. */
  market: string;
  /** Both, or only the longs / only the shorts. */
  side: 'both' | 'long' | 'short';
  /** No entries in this many minutes after each session's open. */
  waitMin: number;
  onChange: (fn: (cur: TrackedSetup[]) => TrackedSetup[]) => void;
  /** Opens the setup on the chart, with its trade drawn once it has filled. */
  onOpen: (row: ScreenerRow, trade: TradeMarks | null) => void;
  active: string;
}

/**
 * Setups saved from the scanner, each replayed against the candles that came after it (rules in
 * `lib/screener/poc-eval.ts`): did the long go up, did the short go down, by the close of the next session.
 */
export function PocTracked({ items: all, market, side, waitMin, onChange, onOpen, active }: Props) {
  const items = all.filter(
    (t) => t.market === market && (side === 'both' || t.long === (side === 'long')),
  );
  const [evals, setEvals] = useState<Record<string, Evaluation>>({});
  const [failed, setFailed] = useState<string[]>([]);
  // a stored result counts only under the wait it was scored with; one too old to refetch candles for stays as it is
  const nowS = Math.floor(Date.now() / 1000);
  const settled = (t: TrackedSetup) =>
    t.final != null &&
    (((t.final.waitMin ?? 0) === waitMin && t.final.rules === RULES) ||
      nowS - t.addedAt > REFETCH_DAYS * 86_400);
  const pendingKey = items
    .filter((t) => !settled(t))
    .map((t) => t.id)
    .join(',');

  useEffect(() => {
    let alive = true;
    const run = async () => {
      const open = items.filter((t) => !settled(t));
      if (!open.length) return;
      const symbols = [...new Set(open.map((t) => t.symbol))];
      const got = await Promise.allSettled(symbols.map(fetchBars));
      if (!alive) return;
      const bars = new Map<string, Bar[]>();
      const bad: string[] = [];
      symbols.forEach((s, i) => {
        const r = got[i]!;
        if (r.status === 'fulfilled') bars.set(s, r.value);
        else bad.push(s);
      });
      const now = Math.floor(Date.now() / 1000);
      const next: Record<string, Evaluation> = {};
      for (const t of open) {
        const b = bars.get(t.symbol);
        if (!b) continue;
        const m = MARKETS[t.market];
        next[t.id] = evaluate(
          t,
          b,
          zoneClock(m?.tz ?? 'UTC'),
          now,
          CLOSE_MIN[t.market] ?? 16 * 60,
          {
            openMin: OPEN_MIN[t.market] ?? 0,
            waitMin,
          },
        );
      }
      setFailed(bad);
      setEvals((cur) => ({ ...cur, ...next }));
      // a result that can no longer change is stored, so it is never fetched again
      const finished = Object.entries(next).filter(([, e]) => e.done);
      if (finished.length) {
        const done = new Map(finished);
        onChange((cur) => cur.map((t) => (done.has(t.id) ? { ...t, final: done.get(t.id) } : t)));
      }
    };
    void run();
    const timer = window.setInterval(() => {
      if (!document.hidden) void run();
    }, REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
    // re-run only when the set of unfinished setups changes, not on every stored result
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingKey, waitMin]);

  const evalOf = (t: TrackedSetup) => {
    if (settled(t)) return t.final!;
    const e = evals[t.id];
    return e && e.waitMin === waitMin && e.rules === RULES ? e : null;
  };
  const sorted = [...items].sort((a, b) => b.addedAt - a.addedAt);
  const s = summarize(items.map(evalOf).filter((e): e is Evaluation => e != null));

  if (!items.length) {
    return (
      <p className="px-3 py-4 text-[12px] leading-relaxed text-[#868993]">
        No {MARKETS[market]?.label ?? market}
        {side === 'both' ? '' : ` ${side}`} setups tracked yet. Use the flag on a scanner card to
        save a setup, then come back after the next session&apos;s close to see whether the long
        went up or the short went down.
      </p>
    );
  }

  return (
    <>
      <div className="border-b border-[#2a2e39] px-3 py-2 text-[12px] text-[#b2b5be]">
        <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-[#868993]">
          <span>
            {MARKETS[market]?.label ?? market}
            {side === 'both' ? '' : ` · ${side}s only`}
          </span>
          <button
            type="button"
            title="Stop tracking every setup listed here (only this market and side)"
            onClick={() => {
              const ids = new Set(items.map((t) => t.id));
              if (!window.confirm(`Remove all ${ids.size} tracked setup(s) listed here?`)) return;
              onChange((cur) => cur.filter((t) => !ids.has(t.id)));
            }}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-[#f23645]"
          >
            <Icons.trash size={13} />
            Remove all {items.length}
          </button>
        </div>
        <div>
          {s.total} tracked · {s.finished} finished
          {s.traded ? (
            <>
              {' '}
              · went the right way{' '}
              <span className="text-[#d1d4dc]">
                {s.rightWay}/{s.traded}
              </span>{' '}
              · median{' '}
              <span style={{ color: (s.medianR ?? 0) >= 0 ? GREEN : RED }}>
                {fmtR(s.medianR ?? 0)}
              </span>{' '}
              · avg move{' '}
              <span style={{ color: (s.avgPct ?? 0) >= 0 ? GREEN : RED }}>
                {fmtPct(s.avgPct ?? 0)}
              </span>{' '}
              · total {fmtR(s.totalR)}
            </>
          ) : null}
        </div>
        {s.finished ? (
          <div className="mt-0.5 text-[11px] text-[#868993]">
            {s.targets} hit +{TARGET_R}R · {s.stops} stopped · {s.closed} closed at the next close
            {s.notFilled ? ` · ${s.notFilled} never entered (not reached, or broke first)` : ''}
          </div>
        ) : null}
        {failed.length ? (
          <div className="mt-0.5 text-[11px] text-[#ffb74d]">
            Prices unavailable for {failed.join(', ')}: retrying every minute
          </div>
        ) : null}
      </div>
      {sorted.map((t) => (
        <TrackedRow
          key={t.id}
          t={t}
          e={evalOf(t)}
          active={t.symbol === active}
          onOpen={() => onOpen(t.row, tradeOf(t, evalOf(t)))}
          onRemove={() => onChange((cur) => cur.filter((x) => x.id !== t.id))}
        />
      ))}
    </>
  );
}

const fmtPct = (p: number) => `${p >= 0 ? '+' : '−'}${Math.abs(p).toFixed(2)}%`;
const fmtR = (r: number) => `${r >= 0 ? '+' : '−'}${Math.abs(r).toFixed(1)}R`;
const fmtPx = (p: number) =>
  p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: p < 10 ? 3 : 2 });

/** "2026-10-28" → "28 Oct". */
const dayLabel = (day: string) =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
  });

function when(t: number, tz: string) {
  return new Date(t * 1000).toLocaleString('en-GB', {
    timeZone: tz,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function verdict(e: Evaluation | null): { text: string; color: string } {
  if (!e) return { text: 'Checking…', color: GREY };
  switch (e.outcome) {
    case 'waiting':
      return { text: 'Waiting for entry', color: GREY };
    case 'not filled':
      return { text: 'Never reached entry', color: GREY };
    case 'cancelled':
      return { text: 'Broke before entry', color: GREY };
    case 'open':
      return { text: `Open ${fmtR(e.r ?? 0)}`, color: (e.r ?? 0) >= 0 ? GREEN : RED };
    case 'target':
      return { text: `Target ${fmtR(e.r ?? 0)}`, color: GREEN };
    case 'stopped':
      return { text: `Stopped ${fmtR(e.r ?? 0)}`, color: RED };
    case 'closed':
      return { text: `Closed ${fmtR(e.r ?? 0)}`, color: (e.r ?? 0) >= 0 ? GREEN : RED };
  }
}

function TrackedRow({
  t,
  e,
  active,
  onOpen,
  onRemove,
}: {
  t: TrackedSetup;
  e: Evaluation | null;
  active: boolean;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const tz = MARKETS[t.market]?.tz ?? 'UTC';
  const v = verdict(e);
  const risk = Math.abs(t.entry - t.stop);
  const riskPct = (risk / t.entry) * 100;
  const target = t.entry + (t.long ? 1 : -1) * TARGET_R * risk;
  const costR = t.market === 'in' ? IN_COST_PCT / riskPct : null;
  const done = e?.done ?? false;
  return (
    <div
      data-poc-symbol={t.symbol}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(ev) => {
        if (ev.target === ev.currentTarget && (ev.key === 'Enter' || ev.key === ' ')) {
          ev.preventDefault();
          onOpen();
        }
      }}
      className={`group block w-full cursor-pointer border-b border-[#2a2e39] px-3 py-2 text-left text-[12px] hover:bg-[#1e222d] ${
        active ? 'bg-[#2a2e39]' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-semibold text-[#d1d4dc]">{t.symbol}</span>
          <span className="text-[11px] font-medium" style={{ color: t.long ? GREEN : RED }}>
            {t.long ? 'Long' : 'Short'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <span
            className="rounded px-1.5 py-0.5 text-[11px] font-medium"
            style={{
              color: v.color,
              border: `1px solid ${v.color}${done ? '' : '66'}`,
              background: done ? `${v.color}26` : 'transparent',
            }}
          >
            {v.text}
          </span>
          <button
            type="button"
            title="Stop tracking"
            aria-label={`Stop tracking ${t.symbol}`}
            onClick={(ev) => {
              ev.stopPropagation();
              onRemove();
            }}
            className="flex h-5 w-5 items-center justify-center rounded text-[#787b86] hover:bg-[#363a45] hover:text-[#f23645]"
          >
            <Icons.close size={12} />
          </button>
        </div>
      </div>
      <div className="mt-0.5 text-[11px] text-[#868993]">
        Added {when(t.addedAt, tz)} at {fmtPx(t.priceAtAdd)} · {t.stage.split(' · ')[1]}
      </div>
      <div className="mt-1.5 grid grid-cols-4 gap-1 tabular-nums text-[#b2b5be]">
        <Cell label="Entry" value={fmtPx(t.entry)} />
        <Cell label="Stop" value={fmtPx(t.stop)} />
        <Cell label={`Target ${TARGET_R}R`} value={fmtPx(target)} />
        <Cell
          label="Move"
          value={e?.pct != null ? `${e.pct >= 0 ? '+' : ''}${e.pct.toFixed(2)}%` : '—'}
        />
      </div>
      <div className="mt-1 text-[11px] text-[#868993]">
        {e?.fillPrice != null && e.fillTime != null
          ? `Filled ${fmtPx(e.fillPrice)} at ${when(e.fillTime, tz)}`
          : e?.outcome === 'cancelled' && e.exitTime != null
            ? `Reached the stop at ${when(e.exitTime, tz)} before it could enter`
            : done
              ? 'Price never came back to the entry'
              : 'Not filled yet'}
        {e?.bestR != null ? ` · best ${fmtR(e.bestR)}` : ''}
        {e?.endDay
          ? ` · ${done ? 'checked' : 'checking'} to the close of ${dayLabel(e.endDay)}`
          : ''}
      </div>
      {costR != null && costR >= 0.5 ? (
        <div className="mt-0.5 text-[11px] text-[#ffb74d]">
          Stop is {riskPct.toFixed(2)}% away: ~{IN_COST_PCT}% of real costs would be −
          {costR.toFixed(1)}R on its own
        </div>
      ) : null}
    </div>
  );
}

const Cell = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div className="text-[10px] uppercase tracking-wide text-[#5d606b]">{label}</div>
    <div>{value}</div>
  </div>
);
