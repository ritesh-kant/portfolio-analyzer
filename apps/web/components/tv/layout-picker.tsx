'use client';

import { useEffect, useRef, useState } from 'react';
import { type ChartLayout, LAYOUT_GROUPS, type LayoutSync } from '../../lib/tv/layout';

/** Outline drawing of a layout: one rounded cell per chart (TradingView style). */
function Thumb({ layout, selected = false }: { layout: ChartLayout; selected?: boolean }) {
  const many = layout.cells.length > 8;
  return (
    <div
      className={`grid rounded-[5px] border-[1.5px] ${selected ? 'border-[#131722] bg-[#131722]' : 'border-[#9598a1] bg-[#9598a1]'}`}
      style={{
        width: 38,
        height: 30,
        gap: many ? 1 : 1.5,
        gridTemplateColumns: layout.cols,
        gridTemplateRows: layout.rows,
        overflow: 'hidden',
      }}
    >
      {layout.cells.map((cell, i) => (
        <div
          key={i}
          className={selected ? 'bg-white' : 'bg-[#141414]'}
          style={{
            gridColumn: `${cell.col} / span ${cell.colSpan ?? 1}`,
            gridRow: `${cell.row} / span ${cell.rowSpan ?? 1}`,
          }}
        />
      ))}
    </div>
  );
}

function Switch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between px-4 py-2.5 text-[15px] text-[#d1d4dc] hover:bg-[#2a2e39]"
    >
      {label}
      <span
        className={`relative h-5 w-9 rounded-full transition-colors ${on ? 'bg-[#2962ff]' : 'bg-[#4c525e]'}`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`}
        />
      </span>
    </button>
  );
}

export function LayoutPicker({
  layout,
  onChange,
  sync,
  onSync,
}: {
  layout: ChartLayout;
  onChange: (id: string) => void;
  sync: LayoutSync;
  onSync: (patch: Partial<LayoutSync>) => void;
}) {
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const open = pos != null;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPos(null);
    };
    window.addEventListener('mousedown', off);
    return () => window.removeEventListener('mousedown', off);
  }, [open]);
  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        title="Chart layout"
        onClick={(e) => {
          if (open) return setPos(null);
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            left: Math.max(8, Math.min(r.left, window.innerWidth - 616)),
            top: r.bottom + 4,
          });
        }}
        className={`flex h-8 items-center rounded px-2 transition-colors ${
          open ? 'bg-[#2a2e39]' : 'hover:bg-[#2a2e39]'
        }`}
      >
        <span className="scale-[0.6]">
          <Thumb layout={layout} />
        </span>
      </button>
      {pos ? (
        <div
          style={{ left: pos.left, top: pos.top }}
          className="fixed z-[60] max-h-[85vh] w-[min(600px,94vw)] overflow-y-auto rounded-lg border border-[#2a2e39] bg-[#141414] py-1 shadow-2xl"
        >
          {LAYOUT_GROUPS.map((g) => (
            <div
              key={g.count}
              className="flex items-center gap-3 border-b border-[#2a2e39] px-4 py-2 last:border-b-0"
            >
              <span className="w-6 shrink-0 text-[17px] text-[#787b86]">{g.count}</span>
              <div className="flex flex-wrap gap-2.5">
                {g.layouts.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    title={l.id}
                    onClick={() => {
                      setPos(null);
                      onChange(l.id);
                    }}
                    className={`rounded-md p-[5px] ${l.id === layout.id ? 'bg-white' : 'hover:bg-[#2a2e39]'}`}
                  >
                    <Thumb layout={l} selected={l.id === layout.id} />
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div className="border-t border-[#2a2e39] px-4 pb-1 pt-3 text-[13px] font-semibold tracking-wide text-[#787b86]">
            SYNC IN LAYOUT
          </div>
          <Switch
            label="Symbol"
            on={sync.symbol}
            onClick={() => onSync({ symbol: !sync.symbol })}
          />
          <Switch
            label="Interval"
            on={sync.interval}
            onClick={() => onSync({ interval: !sync.interval })}
          />
        </div>
      ) : null}
    </div>
  );
}
