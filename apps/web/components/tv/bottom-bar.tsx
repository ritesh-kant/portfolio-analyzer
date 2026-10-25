'use client';

import { useEffect, useState } from 'react';

export const RANGES = ['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '5Y', 'All'] as const;
export type RangeId = (typeof RANGES)[number];

interface Props {
  paperOpen: boolean;
  onPaper: () => void;
  onRange: (r: RangeId) => void;
  timeZone: string;
  /** Unix seconds when the latest candle closes. */
  lastBarEnd: number | null;
  /** ms epoch of the last successful data pull. */
  updatedAt: number | null;
  live: boolean;
  percent: boolean;
  log: boolean;
  auto: boolean;
  onPercent: () => void;
  onLog: () => void;
  onAuto: () => void;
}

const b = (active = false) =>
  `rounded px-2 py-1 text-[12px] font-medium transition-colors ${
    active ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
  }`;

function offsetLabel(timeZone: string): string {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'shortOffset' })
      .formatToParts(new Date())
      .find((x) => x.type === 'timeZoneName')?.value;
    return part?.replace('GMT', 'UTC') ?? timeZone;
  } catch {
    return timeZone;
  }
}

export function BottomBar(p: Props) {
  const [now, setNow] = useState('');
  useEffect(() => {
    const tick = () => {
      try {
        setNow(
          new Intl.DateTimeFormat('en-GB', {
            timeZone: p.timeZone,
            hour12: false,
            timeStyle: 'medium',
          }).format(new Date()),
        );
      } catch {
        setNow('');
      }
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [p.timeZone]);

  return (
    <div className="scrollbar-none flex h-9 shrink-0 items-center gap-0.5 overflow-x-auto border-t border-[#2a2e39] bg-[#131722] px-2 [&>*]:shrink-0">
      <button
        type="button"
        className={`${b(p.paperOpen)} mr-2 border border-[#363a45]`}
        onClick={p.onPaper}
        title="Paper trading: practise with a simulated account on this chart"
      >
        Paper Trading
      </button>
      {RANGES.map((r) => (
        <button key={r} type="button" className={b()} onClick={() => p.onRange(r)}>
          {r}
        </button>
      ))}
      <div className="flex-1" />
      {p.live && p.updatedAt ? (
        <Freshness lastBarEnd={p.lastBarEnd} updatedAt={p.updatedAt} />
      ) : null}
      <span className="mx-3 hidden shrink-0 whitespace-nowrap text-[12px] tabular-nums text-[#b2b5be] sm:inline">
        {now} {offsetLabel(p.timeZone)}
      </span>
      <button type="button" className={b(p.percent)} onClick={p.onPercent} title="Percentage scale">
        %
      </button>
      <button type="button" className={b(p.log)} onClick={p.onLog} title="Logarithmic scale">
        log
      </button>
      <button type="button" className={b(p.auto)} onClick={p.onAuto} title="Auto-fit price scale">
        auto
      </button>
    </div>
  );
}

const ago = (secs: number) =>
  secs < 90
    ? `${Math.max(0, Math.round(secs))}s`
    : secs < 5400
      ? `${Math.round(secs / 60)}m`
      : `${(secs / 3600).toFixed(1)}h`;

/** How fresh the data is: when we last pulled, and how old the newest candle is (that gap is the data source's lag, or a closed market). */
function Freshness({ lastBarEnd, updatedAt }: { lastBarEnd: number | null; updatedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const pulled = (Date.now() - updatedAt) / 1000;
  const barAge = lastBarEnd ? Date.now() / 1000 - lastBarEnd : 0;
  const stale = barAge > 300;
  return (
    <span
      className={`mr-4 hidden min-w-0 shrink truncate text-[12px] tabular-nums md:inline ${stale ? 'text-[#ffb74d]' : 'text-[#b2b5be]'}`}
      title={
        stale
          ? 'The newest candle closed a while ago: the market is closed, or the data source (Yahoo) is behind.'
          : 'Pulled from Yahoo every few seconds; Yahoo itself caches replies ~10 s.'
      }
    >
      <span className={stale ? 'text-[#ffb74d]' : 'text-[#089981]'}>●</span> pulled {ago(pulled)}{' '}
      ago · newest candle closed {ago(Math.max(0, barAge))} ago
    </span>
  );
}
