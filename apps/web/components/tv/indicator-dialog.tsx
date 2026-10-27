'use client';

import { useState } from 'react';
import {
  CATALOG,
  type IndicatorCfg,
  PALETTE,
  catalogOf,
  indicatorLabel,
} from '../../lib/tv/indicator-defs';
import { CVD_ANCHORS, type CvdAnchor } from '../../lib/tv/cvd';
import { Icons } from './icons';

/** Indicators that live in a docked side panel rather than a chart series: Add opens it, Remove closes it. */
export interface PanelEntry {
  id: string;
  name: string;
  description: string;
  open: boolean;
  onToggle: () => void;
}

interface PickerProps {
  active: IndicatorCfg[];
  panels?: PanelEntry[];
  onAdd: (type: IndicatorCfg['type']) => void;
  onRemove: (id: string) => void;
  onClose: () => void;
}

export function IndicatorPicker({ active, panels = [], onAdd, onRemove, onClose }: PickerProps) {
  const [q, setQ] = useState('');
  const list = CATALOG.filter((c) =>
    `${c.name} ${c.description} ${c.type}`.toLowerCase().includes(q.toLowerCase()),
  );
  const panelList = panels.filter((p) =>
    `${p.name} ${p.description}`.toLowerCase().includes(q.toLowerCase()),
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
          {panelList.map((p) => (
            <div key={p.id} className="flex items-center gap-3 px-5 py-2 hover:bg-[#2a2e39]">
              <button type="button" className="min-w-0 flex-1 text-left" onClick={p.onToggle}>
                <div className="text-[14px] font-medium text-[#d1d4dc]">{p.name}</div>
                <div className="text-[12px] text-[#787b86]">{p.description}</div>
              </button>
              {p.open ? (
                <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-[11px] text-[#7ea4ff]">
                  open
                </span>
              ) : null}
              <button
                type="button"
                onClick={p.onToggle}
                className="rounded border border-[#363a45] px-2 py-1 text-[12px] text-[#d1d4dc] hover:border-[#2962ff]"
              >
                {p.open ? 'Remove' : 'Add'}
              </button>
            </div>
          ))}
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
        {cfg.type !== 'vwap' && cfg.type !== 'volume' && cfg.type !== 'cvd' ? (
          <NumberField
            label={cfg.type === 'macd' ? 'Fast length' : cfg.type === 'svp' ? 'Rows' : 'Length'}
            value={cfg.length}
            min={1}
            onChange={(length) => onChange({ ...cfg, length })}
          />
        ) : null}
        {cfg.type === 'cvd' ? (
          <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
            Anchor period
            <select
              value={cfg.anchor ?? 'session'}
              onChange={(e) => onChange({ ...cfg, anchor: e.target.value as CvdAnchor })}
              className="w-40 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]"
            >
              {CVD_ANCHORS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {cfg.type === 'cvd' ? (
          <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
            Divergences
            <select
              value={cfg.divMode ?? 'regular'}
              onChange={(e) => onChange({ ...cfg, divMode: e.target.value as 'off' | 'regular' | 'all' })}
              className="w-40 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]"
            >
              <option value="off">Off</option>
              <option value="regular">Regular</option>
              <option value="all">Regular + hidden</option>
            </select>
          </label>
        ) : null}
        {cfg.type === 'cvd' && (cfg.divMode ?? 'regular') !== 'off' ? (
          <>
            <NumberField
              label="Swing size (candles each side)"
              value={cfg.divPivot ?? 3}
              min={1}
              onChange={(divPivot) => onChange({ ...cfg, divPivot: Math.min(20, divPivot) })}
            />
            <NumberField
              label="Max candles between swings"
              value={cfg.divGap ?? 30}
              min={4}
              onChange={(divGap) => onChange({ ...cfg, divGap: Math.min(200, divGap) })}
            />
          </>
        ) : null}
        {cfg.type === 'cvd' ? (
          <details className="mt-2 rounded border border-[#363a45] bg-[#131722] px-3 py-2 text-[12px] leading-relaxed text-[#b2b5be]">
            <summary className="cursor-pointer text-[13px] text-[#d1d4dc]">What do these divergences mean?</summary>
            <p className="mt-2">
              A divergence is when price and the CVD (running buy minus sell volume) disagree between two
              consecutive swing highs or two consecutive swing lows. It is a warning, not a signal: it can persist
              or fail. The tag is drawn on the second swing.
            </p>
            <p className="mt-2">
              <b className="text-[#26a69a]">▲ Bull div (regular bullish)</b>: price makes a <i>lower low</i>, CVD
              makes a <i>higher low</i>. Sellers are running out of steam, so a reversal up is possible.
            </p>
            <p className="mt-2">
              <b className="text-[#ef5350]">▼ Bear div (regular bearish)</b>: price makes a <i>higher high</i>, CVD
              makes a <i>lower high</i>. Buyers are exhausted, so a reversal down is possible.
            </p>
            <p className="mt-2">
              <b className="text-[#26a69a]">▲ Hidden Bull</b>: price makes a <i>higher low</i>, CVD makes a{' '}
              <i>lower low</i>. Sellers pushed hard but price held higher, meaning buyers absorbed them. It hints
              that an uptrend continues, so it is not a reversal.
            </p>
            <p className="mt-2">
              <b className="text-[#ef5350]">▼ Hidden Bear</b>: price makes a <i>lower high</i>, CVD makes a{' '}
              <i>higher high</i>. Buyers pushed hard but price stayed lower, meaning sellers absorbed them. It
              hints that a downtrend continues.
            </p>
            <p className="mt-2">
              <b className="text-[#d1d4dc]">Line style</b>: solid = confirmed (a candle closed through the swing
              between the two points), dashed = still waiting, faded = failed. <b className="text-[#e879f9]">+ Δ</b>{' '}
              = a wick/delta setup pointing the same way sits next to it.
            </p>
            <p className="mt-2">
              CVD here is estimated from bars, and only candles with at least 5 minute-bars under them are used.
            </p>
          </details>
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
        {cfg.type !== 'volume' && cfg.type !== 'svp' && cfg.type !== 'cvd' ? (
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
