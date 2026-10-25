'use client';

import { useEffect, useState } from 'react';
import { type Drawing, type FibSettings, type LineStyle, defaultFib, fibOf } from '../../lib/tv/drawings';
import { Icons } from './icons';

interface Props {
  drawing: Drawing;
  onChange: (next: Drawing) => void;
  onClose: () => void;
}

const BOX =
  'rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]';

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 py-1 text-[13px] text-[#d1d4dc]">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Num({ value, onCommit, step }: { value: number; onCommit: (v: number) => void; step?: number }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text);
    if (text.trim() !== '' && Number.isFinite(v)) onCommit(v);
    else setText(String(value));
  };
  return (
    <input
      type="number"
      value={text}
      step={step}
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

function StyleSelect({ value, onChange }: { value: LineStyle; onChange: (s: LineStyle) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as LineStyle)} className={BOX}>
      <option value="solid">Solid</option>
      <option value="dashed">Dashed</option>
      <option value="dotted">Dotted</option>
    </select>
  );
}

function Width({ value, onChange }: { value: number; onChange: (w: number) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(Number(e.target.value))} className={BOX}>
      {[1, 2, 3, 4].map((w) => (
        <option key={w} value={w}>
          {w}px
        </option>
      ))}
    </select>
  );
}

const H = ({ children }: { children: string }) => (
  <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">{children}</div>
);

type Tab = 'style' | 'levels';

/** TradingView-style settings for a Fib retracement: per-level on/value/colour, trend line, background, labels, extend. */
export function FibSettingsDialog({ drawing, onChange, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('style');
  const fib = fibOf(drawing);
  const set = (patch: Partial<FibSettings>) => onChange({ ...drawing, fib: { ...fib, ...patch } });
  const setLevel = (i: number, patch: Partial<FibSettings['levels'][number]>) =>
    set({ levels: fib.levels.map((l, k) => (k === i ? { ...l, ...patch } : l)) });

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
          Fib retracement
          <button type="button" onClick={onClose} className="text-[#787b86] hover:text-white" aria-label="Close">
            <Icons.close size={20} />
          </button>
        </div>
        <div className="mb-2 flex gap-4 border-b border-[#363a45] text-[13px]">
          {(['style', 'levels'] as const).map((t) => (
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
          {tab === 'levels' ? (
            <div>
              <H>Levels</H>
              {fib.levels.map((l, i) => (
                <div key={i} className="flex items-center gap-2 py-1">
                  <input type="checkbox" checked={l.on} onChange={(e) => setLevel(i, { on: e.target.checked })} />
                  <Num value={l.v} step={0.001} onCommit={(v) => setLevel(i, { v })} />
                  <Swatch value={l.color} onChange={(color) => setLevel(i, { color })} />
                  <span className="ml-auto text-[12px] text-[#787b86]">{+(l.v * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          ) : (
            <div>
              <H>Trend line</H>
              <div className="flex items-center gap-2 py-1">
                <input
                  type="checkbox"
                  checked={fib.trend.on}
                  onChange={(e) => set({ trend: { ...fib.trend, on: e.target.checked } })}
                />
                <Swatch value={fib.trend.color} onChange={(color) => set({ trend: { ...fib.trend, color } })} />
                <Width value={fib.trend.width} onChange={(width) => set({ trend: { ...fib.trend, width } })} />
                <StyleSelect value={fib.trend.style} onChange={(style) => set({ trend: { ...fib.trend, style } })} />
              </div>
              <H>Level lines</H>
              <div className="flex items-center gap-2 py-1">
                <Width value={fib.lines.width} onChange={(width) => set({ lines: { ...fib.lines, width } })} />
                <StyleSelect value={fib.lines.style} onChange={(style) => set({ lines: { ...fib.lines, style } })} />
              </div>
              <H>Background</H>
              <div className="flex items-center gap-3 py-1">
                <Check
                  label="Fill between levels"
                  checked={fib.background.on}
                  onChange={(on) => set({ background: { ...fib.background, on } })}
                />
                <input
                  type="range"
                  min={0.02}
                  max={0.6}
                  step={0.01}
                  value={fib.background.opacity}
                  onChange={(e) => set({ background: { ...fib.background, opacity: Number(e.target.value) } })}
                  className="ml-auto w-28"
                />
              </div>
              <H>Labels</H>
              <Check label="Show labels" checked={fib.labels.show} onChange={(show) => set({ labels: { ...fib.labels, show } })} />
              <Check label="Show prices" checked={fib.labels.price} onChange={(price) => set({ labels: { ...fib.labels, price } })} />
              <Check
                label="Levels as percents"
                checked={fib.labels.percent}
                onChange={(percent) => set({ labels: { ...fib.labels, percent } })}
              />
              <label className="flex items-center justify-between py-1 text-[13px] text-[#d1d4dc]">
                Labels position
                <select
                  value={fib.labels.side}
                  onChange={(e) => set({ labels: { ...fib.labels, side: e.target.value as 'left' | 'right' } })}
                  className={BOX}
                >
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                </select>
              </label>
              <H>Options</H>
              <Check label="Extend lines left" checked={fib.extendLeft} onChange={(extendLeft) => set({ extendLeft })} />
              <Check label="Extend lines right" checked={fib.extendRight} onChange={(extendRight) => set({ extendRight })} />
              <Check label="Reverse" checked={fib.reverse} onChange={(reverse) => set({ reverse })} />
            </div>
          )}
        </div>
        <div className="mt-3 flex justify-between border-t border-[#363a45] pt-3 text-[13px]">
          <button
            type="button"
            onClick={() => onChange({ ...drawing, fib: defaultFib() })}
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
