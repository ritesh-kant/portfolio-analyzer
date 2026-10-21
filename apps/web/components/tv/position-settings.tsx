'use client';

import { useEffect, useState } from 'react';
import type { Drawing } from '../../lib/tv/drawings';
import { POSITION_DEFAULTS, editPositionLevels, positionStats } from '../../lib/tv/position';
import type { Bar } from '../../lib/tv/types';
import { Icons } from './icons';

interface Props {
  drawing: Drawing;
  bars: Bar[];
  decimals: number;
  onChange: (next: Drawing) => void;
  onClose: () => void;
}

/** A number box that only commits on blur / Enter, so half-typed values never reach the chart. */
function Field({
  label,
  value,
  step,
  min,
  onCommit,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  onCommit: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text);
    if (text.trim() !== '' && Number.isFinite(v) && (min == null || v >= min)) onCommit(v);
    else setText(String(value));
  };
  return (
    <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
      {label}
      <input
        type="number"
        value={text}
        step={step}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className="w-28 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-right text-[#d1d4dc] outline-none focus:border-[#2962ff]"
      />
    </label>
  );
}

export function PositionSettings({ drawing, bars, decimals, onChange, onClose }: Props) {
  const tick = 10 ** -decimals;
  const st = positionStats(drawing, bars, tick);
  const fix = (v: number) => Number(v.toFixed(Math.max(decimals, 2)));
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50"
      onMouseDown={onClose}
      data-tv-ui
    >
      <div
        className="w-[min(360px,94vw)] rounded-lg border border-[#363a45] bg-[#1e222d] p-5 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      >
        <div className="mb-3 flex items-center justify-between text-[16px] font-semibold text-[#d1d4dc]">
          {drawing.kind === 'long' ? 'Long position' : 'Short position'}
          <button
            type="button"
            onClick={onClose}
            className="text-[#787b86] hover:text-white"
            aria-label="Close"
          >
            <Icons.close size={20} />
          </button>
        </div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
          Risk / reward
        </div>
        <Field
          label="Account size"
          value={drawing.account ?? POSITION_DEFAULTS.account}
          min={0}
          onCommit={(account) => onChange({ ...drawing, account })}
        />
        <Field
          label="Lot size"
          value={drawing.lot ?? POSITION_DEFAULTS.lot}
          min={0.0000001}
          onCommit={(lot) => onChange({ ...drawing, lot })}
        />
        <Field
          label="Risk (% of account)"
          value={drawing.riskPct ?? POSITION_DEFAULTS.riskPct}
          min={0}
          onCommit={(riskPct) => onChange({ ...drawing, riskPct })}
        />
        <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
          Levels
        </div>
        <Field
          label="Entry price"
          value={fix(st.entry)}
          step={tick}
          onCommit={(entry) => onChange(editPositionLevels(drawing, { entry }))}
        />
        <Field
          label="Profit level"
          value={fix(st.target)}
          step={tick}
          onCommit={(target) => onChange(editPositionLevels(drawing, { target }))}
        />
        <Field
          label="Stop level"
          value={fix(st.stop)}
          step={tick}
          onCommit={(stop) => onChange(editPositionLevels(drawing, { stop }))}
        />
        <div className="mt-3 text-[12px] text-[#787b86]">
          Qty {st.qty} · risk/reward {st.rr.toFixed(2)}
        </div>
      </div>
    </div>
  );
}
