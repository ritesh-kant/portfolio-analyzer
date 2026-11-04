'use client';

import { useState } from 'react';
import {
  CATALOG,
  type IndicatorCfg,
  PALETTE,
  catalogOf,
  indicatorLabel,
  VOL_DOWN_DEFAULT,
  VOL_MA_DEFAULT,
  VOL_UP_DEFAULT,
} from '../../lib/tv/indicator-defs';
import { CVD_ANCHORS, type CvdAnchor } from '../../lib/tv/cvd';
import { GEX_LINES, type GexConvention, gexLineColor, gexLineOn } from '../../lib/tv/gex';
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

const Check = ({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) => (
  <label className="flex items-center gap-2 py-1.5 text-[13px] text-[#d1d4dc]">
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      className="h-3.5 w-3.5 accent-[#2962ff]"
    />
    {label}
  </label>
);

const ColorField = ({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (hex: string) => void;
}) => (
  <label className="flex items-center justify-between py-1.5 pl-6 text-[13px] text-[#d1d4dc]">
    {label}
    <input
      type="color"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-6 w-10 cursor-pointer rounded border border-[#363a45] bg-transparent p-0.5"
    />
  </label>
);

/** Gamma levels: MenthorQ's Style tab, one row per level with show/hide and colour. */
function GexLevelFields({ cfg, onChange }: { cfg: IndicatorCfg; onChange: (cfg: IndicatorCfg) => void }) {
  const style = cfg.gexStyle;
  const set = (key: string, patch: { on?: boolean; color?: string }) =>
    onChange({ ...cfg, gexStyle: { ...style, [key]: { ...style?.[key], ...patch } } });
  return (
    <div className="mt-3 border-t border-[#363a45] pt-3">
      <div className="mb-1 flex items-center justify-between text-[12px] uppercase tracking-wide text-[#787b86]">
        Levels
        <button
          type="button"
          onClick={() => onChange({ ...cfg, gexStyle: undefined })}
          className="rounded border border-[#363a45] px-2 py-0.5 text-[11px] normal-case tracking-normal text-[#b2b5be] hover:text-white"
        >
          Defaults
        </button>
      </div>
      {GEX_LINES.map((def) => {
        const on = gexLineOn(def, style);
        return (
          <div key={def.key} className="flex items-center justify-between py-1 text-[13px] text-[#d1d4dc]">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={on}
                onChange={(e) => set(def.key, { on: e.target.checked })}
                className="h-3.5 w-3.5 accent-[#2962ff]"
              />
              <span className={on ? '' : 'text-[#787b86]'}>{def.name}</span>
            </label>
            <span className="flex items-center gap-2">
              <svg width="28" height="6" aria-hidden>
                <line
                  x1="0"
                  y1="3"
                  x2="28"
                  y2="3"
                  stroke={gexLineColor(def, style)}
                  strokeWidth="1.5"
                  strokeDasharray={def.style === 'dashed' ? '5 3' : def.style === 'dotted' ? '1.5 2.5' : undefined}
                  opacity={on ? 1 : 0.35}
                />
              </svg>
              <input
                type="color"
                value={gexLineColor(def, style)}
                onChange={(e) => set(def.key, { color: e.target.value })}
                aria-label={`${def.name} colour`}
                className={`h-6 w-10 cursor-pointer rounded border border-[#363a45] bg-transparent p-0.5 ${on ? '' : 'opacity-40'}`}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Volume, laid out like TradingView's Vol dialog: Inputs (MA length, colouring) and Style (colours, MA, output). */
function VolumeFields({ cfg, onChange }: { cfg: IndicatorCfg; onChange: (cfg: IndicatorCfg) => void }) {
  const [tab, setTab] = useState<'inputs' | 'style'>('style');
  return (
    <div>
      <div className="mb-2 flex gap-4 border-b border-[#2a2e39] text-[13px] font-medium">
        {(['inputs', 'style'] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 pb-1.5 capitalize ${
              tab === t ? 'border-[#2962ff] text-white' : 'border-transparent text-[#787b86] hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === 'inputs' ? (
        <>
          <NumberField
            label="Volume MA length"
            value={cfg.length}
            onChange={(length) => onChange({ ...cfg, length })}
          />
          <Check
            label="Color based on previous close"
            checked={cfg.volPrevClose ?? false}
            onChange={(volPrevClose) => onChange({ ...cfg, volPrevClose })}
          />
        </>
      ) : (
        <>
          <ColorField
            label="Growing"
            value={cfg.volUp ?? VOL_UP_DEFAULT}
            onChange={(volUp) => onChange({ ...cfg, volUp })}
          />
          <ColorField
            label="Falling"
            value={cfg.volDown ?? VOL_DOWN_DEFAULT}
            onChange={(volDown) => onChange({ ...cfg, volDown })}
          />
          <Check
            label="Volume MA"
            checked={cfg.volMa ?? false}
            onChange={(volMa) => onChange({ ...cfg, volMa })}
          />
          {cfg.volMa ? (
            <ColorField
              label="MA colour"
              value={cfg.volMaColor ?? VOL_MA_DEFAULT}
              onChange={(volMaColor) => onChange({ ...cfg, volMaColor })}
            />
          ) : null}
          <div className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
            Output values
          </div>
          <Check
            label="Labels on price scale"
            checked={cfg.volLabels ?? false}
            onChange={(volLabels) => onChange({ ...cfg, volLabels })}
          />
          <Check
            label="Values in status line"
            checked={cfg.volStatus ?? true}
            onChange={(volStatus) => onChange({ ...cfg, volStatus })}
          />
          <button
            type="button"
            onClick={() =>
              onChange({
                ...cfg,
                length: 20,
                volUp: undefined,
                volDown: undefined,
                volPrevClose: undefined,
                volMa: undefined,
                volMaColor: undefined,
                volLabels: undefined,
                volStatus: undefined,
              })
            }
            className="mt-2 text-[12px] text-[#787b86] hover:text-white"
          >
            Reset to defaults
          </button>
        </>
      )}
    </div>
  );
}

export function IndicatorSettings({ cfg, onChange, onClose }: SettingsProps) {
  const entry = catalogOf(cfg.type);
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50"
      onMouseDown={onClose}
    >
      <div
        className="max-h-[90vh] w-[min(360px,94vw)] overflow-y-auto rounded-lg border border-[#363a45] bg-[#1e222d] p-5 shadow-2xl"
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
        {cfg.type === 'volume' ? <VolumeFields cfg={cfg} onChange={onChange} /> : null}
        {cfg.type !== 'vwap' && cfg.type !== 'volume' && cfg.type !== 'cvd' ? (
          <NumberField
            label={
              cfg.type === 'macd'
                ? 'Fast length'
                : cfg.type === 'svp' || cfg.type === 'avp'
                  ? 'Rows'
                  : cfg.type === 'gex'
                    ? 'Expiries within (days)'
                    : 'Length'
            }
            value={cfg.length}
            min={1}
            onChange={(length) =>
              onChange({ ...cfg, length: cfg.type === 'gex' ? Math.min(120, length) : length })
            }
          />
        ) : null}
        {cfg.type === 'gex' ? (
          <>
            <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
              Dealer position
              <select
                value={cfg.gexConvention ?? 'dealer_long_calls'}
                onChange={(e) =>
                  onChange({ ...cfg, gexConvention: e.target.value as GexConvention })
                }
                className="w-40 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]"
              >
                <option value="dealer_long_calls">Long calls, short puts</option>
                <option value="dealer_short_all">Short everything</option>
              </select>
            </label>
            <Check
              label="Net GEX by strike (column beside the prices)"
              checked={cfg.gexProfile !== false}
              onChange={(gexProfile) => onChange({ ...cfg, gexProfile })}
            />
            <Check
              label="Background: green above HVL, red below"
              checked={cfg.gexZones !== false}
              onChange={(gexZones) => onChange({ ...cfg, gexZones })}
            />
            {cfg.gexZones !== false ? (
              <NumberField
                label="Background strength (%)"
                min={0}
                value={cfg.gexZoneOpacity ?? 50}
                onChange={(n) => onChange({ ...cfg, gexZoneOpacity: Math.min(200, n) })}
              />
            ) : null}
            <Check
              label="Info panel (regime, net delta, level distances, put/call ratios)"
              checked={cfg.gexPanel !== false}
              onChange={(gexPanel) => onChange({ ...cfg, gexPanel })}
            />
            <GexLevelFields cfg={cfg} onChange={onChange} />
            <details className="mt-2 rounded border border-[#363a45] bg-[#131722] px-3 py-2 text-[12px] leading-relaxed text-[#b2b5be]">
              <summary className="cursor-pointer text-[13px] text-[#d1d4dc]">What do these lines mean?</summary>
              <p className="mt-2">
                GEX estimates how much option writers must buy or sell to stay hedged as price moves, from
                the live option chain. <b className="text-[#f4511e]">Call Resistance</b> is the strike with
                the most call gamma above price; <b className="text-[#43a047]">Put Support</b> the most put
                gamma below. <b className="text-[#448aff]">HVL</b> is the gamma flip: above it hedging tends
                to damp moves, below it to amplify them. <b className="text-[#f4511e]">Gamma Wall</b> is the
                single largest strike. <b className="text-[#26c6da]">GEX 1–10</b> are the next-largest strikes
                after Call Resistance and Put Support. 0DTE levels use the nearest expiry only; 1D Min/Max is
                the one-day range implied by at-the-money volatility. The bars in the column right of the
                prices are net GEX at each strike per 1% move (₹ crore for NSE, $ for US): green damps
                moves, red amplifies them.
              </p>
              <p className="mt-2">
                NSE chains come from Upstox; US chains from Yahoo (~15 minutes delayed, implied volatility
                re-solved from bid/ask). US futures have no free option chains, so ES uses SPX, NQ uses QQQ,
                RTY uses IWM, YM uses DIA, GC uses GLD and SI uses SLV, scaled onto the future&rsquo;s price.
              </p>
              <p className="mt-2">
                The sign rests on an assumption about who is short the options. &ldquo;Long calls, short
                puts&rdquo; is the US convention most GEX tools use; in India retail mostly buys options,
                so &ldquo;Short everything&rdquo; may be closer, and then there is no flip. The levels are a
                snapshot of today&rsquo;s open interest, so they are hidden in replay.
              </p>
            </details>
          </>
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
        {cfg.type !== 'volume' && cfg.type !== 'svp' && cfg.type !== 'avp' && cfg.type !== 'cvd' ? (
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
