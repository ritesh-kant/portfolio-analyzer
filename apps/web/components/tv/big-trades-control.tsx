'use client';

import { useRef, useState } from 'react';
import type { BigTradesCfg } from '../../lib/tv/bigtrades';

export interface BigTradesUi extends BigTradesCfg {
  on: boolean;
}

interface Props {
  ui: BigTradesUi;
  onChange: (patch: Partial<BigTradesUi>) => void;
  /** Trades loaded for the symbol / bubbles shown after the size filter. */
  loaded: number;
  shown: number;
  /** Share of volume the sampled ticks account for (0–1), or null. */
  coverage: number | null;
  days: string[];
}

const field =
  'w-16 rounded border border-[#2a2e39] bg-[#1e222d] px-1.5 py-0.5 text-right text-[12px] text-[#d1d4dc] outline-none focus:border-[#2962ff]';

/** Small floating control: switch the Big Trades bubbles on, set the size filter and the aggregate window. */
export function BigTradesControl({ ui, onChange, loaded, shown, coverage, days }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });
  const box = useRef<HTMLDivElement>(null);
  return (
    <div ref={box} className="flex items-center gap-1 text-[12px]" data-tv-ui>
      {open ? (
        <div style={{ left: pos.left, top: pos.top }} className="fixed z-50 w-64 rounded border border-[#2a2e39] bg-[#1e222d] p-3 text-[11px] text-[#b2b5be] shadow-lg">
          <div className="mb-2 text-[12px] font-medium text-[#d1d4dc]">Big Trades</div>
          <label className="mb-2 flex items-center justify-between gap-2">
            <span>Min trade size</span>
            <input
              type="number"
              min={1}
              className={field}
              value={ui.minQty}
              onChange={(e) => onChange({ minQty: Math.max(1, Number(e.target.value) || 1) })}
            />
          </label>
          <label className="mb-2 flex items-center justify-between gap-2">
            <span>Aggregate period (ms)</span>
            <input
              type="number"
              min={0}
              step={100}
              className={field}
              value={ui.aggMs}
              onChange={(e) => onChange({ aggMs: Math.max(0, Number(e.target.value) || 0) })}
            />
          </label>
          <p className="leading-snug text-[#787b86]">
            {loaded
              ? `${shown} of ${loaded} trades shown · ${days.join(', ')}`
              : 'No captured trades for this symbol. Run scripts/capture_ticks.py during market hours.'}
            {coverage != null ? ` · sampled: ~${Math.round(coverage * 100)}% of volume seen` : ''}
          </p>
          <p className="mt-1 leading-snug text-[#787b86]">
            The feed is a ~1 s snapshot of the last trade, so some trades are missing. Side comes from the best
            bid/ask, else the tick rule.
          </p>
        </div>
      ) : null}
      <>
        <button
          type="button"
          onClick={() => onChange({ on: !ui.on })}
          className={`rounded px-2 py-1 ${ui.on ? 'bg-[#2962ff]/30 text-white' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'}`}
          title="Big Trades bubbles: large trades, green = buyer was the aggressor, red = seller"
        >
          Big Trades
        </button>
        {ui.on ? (
          <button
            type="button"
            onClick={() => {
              const r = box.current?.getBoundingClientRect();
              if (r) setPos({ left: Math.min(r.left, window.innerWidth - 270), top: r.bottom + 4 });
              setOpen((v) => !v);
            }}
            className="rounded px-1.5 py-1 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
            title="Big Trades settings"
          >
            ⚙
          </button>
        ) : null}
      </>
    </div>
  );
}
