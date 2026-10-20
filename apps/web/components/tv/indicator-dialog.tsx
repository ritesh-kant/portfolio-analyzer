'use client';

import { useState } from 'react';
import {
  CATALOG,
  type IndicatorCfg,
  PALETTE,
  catalogOf,
  indicatorLabel,
} from '../../lib/tv/indicator-defs';
import { Icons } from './icons';

interface PickerProps {
  active: IndicatorCfg[];
  onAdd: (type: IndicatorCfg['type']) => void;
  onRemove: (id: string) => void;
  onClose: () => void;
}

export function IndicatorPicker({ active, onAdd, onRemove, onClose }: PickerProps) {
  const [q, setQ] = useState('');
  const list = CATALOG.filter((c) =>
    `${c.name} ${c.description} ${c.type}`.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 pt-[10vh]"
      onMouseDown={onClose}
    >
      <div
        className="flex max-h-[70vh] w-[min(560px,94vw)] flex-col overflow-hidden rounded-lg border border-[#363a45] bg-[#1e222d] shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 pt-4 text-[18px] font-semibold text-[#d1d4dc]">
          Indicators
          <button
            type="button"
            onClick={onClose}
            className="text-[#787b86] hover:text-white"
            aria-label="Close"
          >
            <Icons.close size={22} />
          </button>
        </div>
        <div className="flex items-center gap-3 border-b border-[#2a2e39] px-5 py-3 text-[#787b86]">
          <Icons.search size={20} />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && onClose()}
            placeholder="Search"
            className="w-full bg-transparent text-[15px] text-[#d1d4dc] outline-none placeholder:text-[#5d606b]"
          />
        </div>
        <div className="overflow-y-auto pb-2">
          {list.map((c) => {
            const count = active.filter((a) => a.type === c.type).length;
            return (
              <div key={c.type} className="flex items-center gap-3 px-5 py-2 hover:bg-[#2a2e39]">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => onAdd(c.type)}
                >
                  <div className="text-[14px] font-medium text-[#d1d4dc]">{c.name}</div>
                  <div className="text-[12px] text-[#787b86]">{c.description}</div>
                </button>
                {count > 0 ? (
                  <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-[11px] text-[#7ea4ff]">
                    {count} on chart
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => onAdd(c.type)}
                  className="rounded border border-[#363a45] px-2 py-1 text-[12px] text-[#d1d4dc] hover:border-[#2962ff]"
                >
                  Add
                </button>
              </div>
            );
          })}
          {active.length ? (
            <div className="mt-2 border-t border-[#2a2e39] px-5 pt-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
                On chart
              </div>
              {active.map((a) => (
                <div
                  key={a.id}
                  className="flex items-center justify-between py-1 text-[13px] text-[#d1d4dc]"
                >
                  <span className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: a.color }} />
                    {indicatorLabel(a)}
                  </span>
                  <button
                    type="button"
                    onClick={() => onRemove(a.id)}
                    className="text-[#787b86] hover:text-[#f23645]"
                    aria-label="Remove"
                  >
                    <Icons.close size={16} />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

interface SettingsProps {
  cfg: IndicatorCfg;
  onChange: (cfg: IndicatorCfg) => void;
  onClose: () => void;
}

const NumberField = ({
  label,
  value,
  onChange,
  min = 1,
}: {
  label: string;
  value: number;
  min?: number;
  onChange: (n: number) => void;
}) => (
  <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
    {label}
    <input
      type="number"
      min={min}
      value={value}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (Number.isFinite(n) && n >= min) onChange(n);
      }}
      className="w-24 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-right text-[#d1d4dc] outline-none focus:border-[#2962ff]"
    />
  </label>
);

export function IndicatorSettings({ cfg, onChange, onClose }: SettingsProps) {
  const entry = catalogOf(cfg.type);
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50"
      onMouseDown={onClose}
    >
      <div
        className="w-[min(360px,94vw)] rounded-lg border border-[#363a45] bg-[#1e222d] p-5 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      >
        <div className="mb-3 flex items-center justify-between text-[16px] font-semibold text-[#d1d4dc]">
          {entry.name}
          <button
            type="button"
            onClick={onClose}
            className="text-[#787b86] hover:text-white"
            aria-label="Close"
          >
            <Icons.close size={20} />
          </button>
        </div>
        {cfg.type !== 'vwap' && cfg.type !== 'volume' ? (
          <NumberField
            label={cfg.type === 'macd' ? 'Fast length' : 'Length'}
            value={cfg.length}
            onChange={(length) => onChange({ ...cfg, length })}
          />
        ) : null}
        {cfg.type === 'macd' ? (
          <>
            <NumberField
              label="Slow length"
              value={cfg.length2 ?? 26}
              onChange={(length2) => onChange({ ...cfg, length2 })}
            />
            <NumberField
              label="Signal smoothing"
              value={cfg.length3 ?? 9}
              onChange={(length3) => onChange({ ...cfg, length3 })}
            />
          </>
        ) : null}
        {cfg.type === 'bb' ? (
          <NumberField
            label="Std dev"
            value={cfg.mult ?? 2}
            min={0.1}
            onChange={(mult) => onChange({ ...cfg, mult })}
          />
        ) : null}
        {cfg.type !== 'volume' ? (
          <div className="flex items-center justify-between py-1.5 text-[13px] text-[#d1d4dc]">
            Colour
            <div className="flex gap-1.5">
              {PALETTE.map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={c}
                  onClick={() => onChange({ ...cfg, color: c })}
                  className={`h-5 w-5 rounded-full ${cfg.color === c ? 'ring-2 ring-white ring-offset-2 ring-offset-[#1e222d]' : ''}`}
                  style={{ background: c }}
                />
              ))}
            </div>
          </div>
        ) : null}
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-[#2962ff] px-4 py-1.5 text-[13px] font-semibold text-white hover:bg-[#1e4fd6]"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
