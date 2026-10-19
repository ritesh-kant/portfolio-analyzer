'use client';

import { useEffect, useState } from 'react';

export const RANGES = ['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '5Y', 'All'] as const;
export type RangeId = (typeof RANGES)[number];

interface Props {
  onRange: (r: RangeId) => void;
  timeZone: string;
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
        setNow(new Intl.DateTimeFormat('en-GB', { timeZone: p.timeZone, hour12: false, timeStyle: 'medium' }).format(new Date()));
      } catch {
        setNow('');
      }
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [p.timeZone]);

  return (
    <div className="flex h-9 shrink-0 items-center gap-0.5 border-t border-[#2a2e39] bg-[#131722] px-2">
      {RANGES.map((r) => (
        <button key={r} type="button" className={b()} onClick={() => p.onRange(r)}>
          {r}
        </button>
      ))}
      <div className="flex-1" />
      <span className="mr-3 text-[12px] tabular-nums text-[#b2b5be]">
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
