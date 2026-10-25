'use client';

import { useEffect, useState } from 'react';
import { type VpSettings, VP_DEFAULTS } from '../../lib/tv/drawings';
import { DEFAULT_ROWS } from '../../lib/tv/volume-profile';
import { Icons } from './icons';

interface Props {
  title?: string;
  vp: VpSettings;
  rows: number;
  /** What Reset restores. */
  defaults?: VpSettings;
  /** Label of the width input. */
  widthLabel?: string;
  onChange: (next: { vp: VpSettings; rows: number }) => void;
  onClose: () => void;
}

const BOX =
  'rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]';

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="flex items-center justify-between gap-3 py-1.5 text-[13px] text-[#d1d4dc]">
    <span>{label}</span>
    <div className="flex items-center gap-2">{children}</div>
  </div>
);

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 py-1 text-[13px] text-[#d1d4dc]">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Num({ value, onCommit, min, max }: { value: number; onCommit: (v: number) => void; min: number; max: number }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text);
    if (text.trim() !== '' && Number.isFinite(v)) onCommit(Math.min(max, Math.max(min, Math.round(v))));
    else setText(String(value));
  };
  return (
    <input
      type="number"
      value={text}
      min={min}
      max={max}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
      className={`${BOX} w-20 text-right`}
    />
  );
}

function Swatch({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <input
      type="color"
      value={/^#[0-9a-f]{6}$/i.test(value) ? value : '#787b86'}
      onChange={(e) => onChange(e.target.value)}
      className="h-6 w-7 cursor-pointer rounded border border-[#363a45] bg-transparent p-0"
    />
  );
}

const H = ({ children }: { children: string }) => (
  <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">{children}</div>
);

/** TradingView-style settings for a fixed range volume profile: Inputs (rows, volume, value area, placement, width) and Style. */
export function VpSettingsDialog({
  title = 'Fixed range volume profile',
  vp,
  rows,
  defaults = VP_DEFAULTS,
  widthLabel = 'Width (% of box)',
  onChange,
  onClose,
}: Props) {
  const [tab, setTab] = useState<'inputs' | 'style'>('inputs');
  const set = (patch: Partial<VpSettings>) => onChange({ rows, vp: { ...vp, ...patch } });

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50" onMouseDown={onClose} data-tv-ui>
      <div
        className="flex max-h-[88vh] w-[min(400px,94vw)] flex-col rounded-lg border border-[#363a45] bg-[#1e222d] p-5 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      >
        <div className="mb-3 flex items-center justify-between text-[16px] font-semibold text-[#d1d4dc]">
          {title}
          <button type="button" onClick={onClose} className="text-[#787b86] hover:text-white" aria-label="Close">
            <Icons.close size={20} />
          </button>
        </div>
        <div className="mb-2 flex gap-4 border-b border-[#363a45] text-[13px]">
          {(['inputs', 'style'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`-mb-px border-b-2 pb-1.5 capitalize ${tab === t ? 'border-[#2962ff] text-white' : 'border-transparent text-[#787b86] hover:text-[#d1d4dc]'}`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto pr-1">
          {tab === 'inputs' ? (
            <div>
              <H>Profile</H>
              <Row label="Number of rows">
                <Num
                  value={rows}
                  min={2}
                  max={500}
                  onCommit={(next) => onChange({ vp, rows: next })}
                />
              </Row>
              <Row label="Volume">
                <select value={vp.volume} onChange={(e) => set({ volume: e.target.value as VpSettings['volume'] })} className={BOX}>
                  <option value="updown">Up/Down</option>
                  <option value="total">Total</option>
                </select>
              </Row>
              <Row label="Value area volume (%)">
                <Num value={vp.valueArea} min={1} max={100} onCommit={(valueArea) => set({ valueArea })} />
              </Row>
              <H>Layout</H>
              <Row label="Placement">
                <select value={vp.placement} onChange={(e) => set({ placement: e.target.value as 'left' | 'right' })} className={BOX}>
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                </select>
              </Row>
              <Row label={widthLabel}>
                <Num value={vp.width} min={5} max={100} onCommit={(width) => set({ width })} />
              </Row>
            </div>
          ) : (
            <div>
              <H>Volume</H>
              <Row label="Up volume">
                <Swatch value={vp.upColor} onChange={(upColor) => set({ upColor })} />
              </Row>
              <Row label="Down volume">
                <Swatch value={vp.downColor} onChange={(downColor) => set({ downColor })} />
              </Row>
              <Row label="Value area up">
                <Swatch value={vp.vaUpColor} onChange={(vaUpColor) => set({ vaUpColor })} />
              </Row>
              <Row label="Value area down">
                <Swatch value={vp.vaDownColor} onChange={(vaDownColor) => set({ vaDownColor })} />
              </Row>
              <Row label="Opacity">
                <input
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={vp.opacity}
                  onChange={(e) => set({ opacity: Number(e.target.value) })}
                  className="w-32"
                />
              </Row>
              <H>Lines</H>
              <div className="flex items-center gap-2">
                <Check label="POC" checked={vp.showPoc} onChange={(showPoc) => set({ showPoc })} />
                <span className="ml-auto" />
                <Swatch value={vp.pocColor} onChange={(pocColor) => set({ pocColor })} />
                <select value={vp.pocWidth} onChange={(e) => set({ pocWidth: Number(e.target.value) })} className={BOX}>
                  {[1, 2, 3, 4].map((w) => (
                    <option key={w} value={w}>
                      {w}px
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <Check label="VAH / VAL" checked={vp.showVa} onChange={(showVa) => set({ showVa })} />
                <span className="ml-auto" />
                <Swatch value={vp.vaLineColor} onChange={(vaLineColor) => set({ vaLineColor })} />
              </div>
              <Check label="Price labels" checked={vp.showLabels} onChange={(showLabels) => set({ showLabels })} />
              <H>Background</H>
              <div className="flex items-center gap-2">
                <Check label="Fill box" checked={vp.showBackground} onChange={(showBackground) => set({ showBackground })} />
                <span className="ml-auto" />
                <Swatch value={vp.backgroundColor} onChange={(backgroundColor) => set({ backgroundColor })} />
                <input
                  type="range"
                  min={0.02}
                  max={0.4}
                  step={0.01}
                  value={vp.backgroundOpacity}
                  onChange={(e) => set({ backgroundOpacity: Number(e.target.value) })}
                  className="w-24"
                />
              </div>
            </div>
          )}
        </div>
        <div className="mt-3 flex justify-between border-t border-[#363a45] pt-3 text-[13px]">
          <button
            type="button"
            onClick={() => onChange({ vp: { ...defaults }, rows: DEFAULT_ROWS })}
            className="rounded px-3 py-1.5 text-[#b2b5be] hover:bg-[#2a2e39]"
          >
            Reset
          </button>
          <button type="button" onClick={onClose} className="rounded bg-[#2962ff] px-4 py-1.5 text-white hover:bg-[#1e53e5]">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
