'use client';

import { useEffect, useState } from 'react';
import { POC_DEFAULTS, type PocConfig } from '../../lib/screener/poc-retest';
import { Icons } from './icons';

/** What the chart indicator shows beside the detection settings. */
export interface PocView {
  /** Also draw POCs price already came back to (touched, retested, broken), faintly and only up to the touch. */
  showTested: boolean;
}
export const POC_VIEW_DEFAULTS: PocView = { showTested: false };

const BOX =
  'rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]';

const Row = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
  <div className="flex items-center justify-between gap-3 py-1.5 text-[13px] text-[#d1d4dc]" title={hint}>
    <span>{label}</span>
    <div className="flex items-center gap-2">{children}</div>
  </div>
);

function Num({
  value,
  onCommit,
  min,
  max,
  step = 1,
}: {
  value: number;
  onCommit: (v: number) => void;
  min: number;
  max: number;
  step?: number;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text);
    if (text.trim() !== '' && Number.isFinite(v)) {
      const snapped = Math.round(v / step) * step;
      onCommit(Math.min(max, Math.max(min, Number(snapped.toFixed(4)))));
    } else setText(String(value));
  };
  return (
    <input
      type="number"
      value={text}
      min={min}
      max={max}
      step={step}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
      className={`${BOX} w-20 text-right`}
    />
  );
}

interface Props {
  cfg: PocConfig;
  view: PocView;
  onChange: (cfg: PocConfig) => void;
  onView: (view: PocView) => void;
  onClose: () => void;
}

/** Settings for the POC retest indicator: the detection thresholds (the scanner keeps the defaults) and what to draw. */
export function PocSettingsDialog({ cfg, view, onChange, onView, onClose }: Props) {
  const set = (patch: Partial<PocConfig>) => onChange({ ...cfg, ...patch });
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50"
      onMouseDown={onClose}
      data-tv-ui
    >
      <div
        className="flex max-h-[88vh] w-[min(400px,94vw)] flex-col rounded-lg border border-[#363a45] bg-[#1e222d] p-5 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      >
        <div className="mb-3 flex items-center justify-between text-[16px] font-semibold text-[#d1d4dc]">
          POC retest
          <button
            type="button"
            onClick={onClose}
            className="text-[#787b86] hover:text-white"
            aria-label="Close"
          >
            <Icons.close size={20} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <Row label="Sessions to look back" hint="How many full sessions back a POC can come from">
            <Num value={cfg.lookback} min={1} max={30} onCommit={(lookback) => set({ lookback })} />
          </Row>
          <Row label="Moved away (%)" hint="How far beyond the zone edge price must go">
            <Num
              value={cfg.awayPct}
              min={0.1}
              max={20}
              step={0.1}
              onCommit={(awayPct) => set({ awayPct })}
            />
          </Row>
          <Row label="Pulling back (% of move)" hint="Share of the move away given back">
            <Num
              value={cfg.pullbackPct}
              min={5}
              max={95}
              step={5}
              onCommit={(pullbackPct) => set({ pullbackPct })}
            />
          </Row>
          <Row label="Approaching within (%)" hint="Distance to the entry edge that counts as approaching">
            <Num
              value={cfg.approachPct}
              min={0.1}
              max={5}
              step={0.1}
              onCommit={(approachPct) => set({ approachPct })}
            />
          </Row>
          <label className="flex items-center gap-2 py-1.5 text-[13px] text-[#d1d4dc]">
            <input
              type="checkbox"
              checked={view.showTested}
              onChange={(e) => onView({ ...view, showTested: e.target.checked })}
            />
            Show POCs price already came back to
          </label>
          <p className="mt-2 text-[11px] leading-snug text-[#868993]">
            Needs a 1–15 minute chart. The scanner always uses the defaults. Not backtested: it marks the
            pattern, it does not say it pays.
          </p>
        </div>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => {
              onChange(POC_DEFAULTS);
              onView(POC_VIEW_DEFAULTS);
            }}
            className="rounded border border-[#363a45] px-3 py-1 text-[13px] text-[#d1d4dc] hover:bg-[#2a2e39]"
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
