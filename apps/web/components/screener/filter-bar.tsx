'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { FIELD, FILTERABLE, GROUP_ORDER, type FieldDef } from '../../lib/screener/fields';
import { compact, parseNumber, rangeLabel } from '../../lib/screener/format';
import { MARKET_LIST, type Market } from '../../lib/screener/markets';
import { isActive, isMulti, isRange, isToggle } from '../../lib/screener/query';
import { toggleIn, withFilter, withRange } from '../../lib/screener/screens';
import type { FilterState, RangeFilter } from '../../lib/screener/types';
import { Icons } from '../tv/icons';
import { Popover } from './popover';

const pillBase =
  'inline-flex h-7 shrink-0 items-center rounded-md border text-[12px] font-medium transition-colors';
const idle = `${pillBase} gap-1 border-[#363a45] px-2 text-[#b2b5be] hover:border-[#5d606b] hover:text-white`;

const Chevron = () => (
  <span className="inline-block h-3 w-3 text-current opacity-70">
    <Icons.chevron size={12} />
  </span>
);

/** One-tap bounds for the numbers a momentum screen reaches for. `min`/`max` says which bound the chip sets. */
const QUICK: Record<string, { bound: 'min' | 'max'; values: number[] }> = {
  changePct: { bound: 'min', values: [3, 5, 10, 20, 50] },
  rvolTime: { bound: 'min', values: [2, 3, 5, 10] },
  rvol: { bound: 'min', values: [2, 3, 5, 10] },
  float: { bound: 'max', values: [5e6, 10e6, 20e6, 50e6] },
  volume: { bound: 'min', values: [250e3, 500e3, 1e6, 5e6] },
  marketCap: { bound: 'max', values: [50e6, 300e6, 2e9, 10e9] },
  gapPct: { bound: 'min', values: [2, 4, 8, 15] },
  shortFloat: { bound: 'min', values: [10, 20, 30] },
};

const unitHint = (d: FieldDef, currency: string) =>
  d.unit === 'usd' || d.unit === 'usdbig'
    ? currency
    : d.unit === 'pct'
      ? '%'
      : d.unit === 'shares'
        ? 'shares'
        : d.unit === 'days'
          ? 'days'
          : d.unit === 'x'
            ? '×'
            : '';

const inputText = (d: FieldDef, n: number | undefined) =>
  n == null ? '' : d.unit === 'shares' || d.unit === 'usdbig' ? compact(n) : String(n);

function RangeEditor({
  def,
  value,
  currency,
  onChange,
}: {
  def: FieldDef;
  value: RangeFilter | undefined;
  currency: string;
  onChange: (r: RangeFilter) => void;
}) {
  const [min, setMin] = useState(inputText(def, value?.min));
  const [max, setMax] = useState(inputText(def, value?.max));
  // The field is edited as text ("15M"); the filter only changes when both boxes parse.
  const parsed = (t: string) => (t.trim() === '' ? undefined : parseNumber(t));
  const lo = parsed(min);
  const hi = parsed(max);
  const bad = lo === null || hi === null;
  const crossed = lo != null && hi != null && lo > hi;

  const push = (a: string, b: string) => {
    const x = parsed(a);
    const y = parsed(b);
    if (x === null || y === null || (x != null && y != null && x > y)) return;
    onChange({ ...(x != null ? { min: x } : {}), ...(y != null ? { max: y } : {}) });
  };
  // The one-tap market-cap values are dollar amounts: they mean nothing in rupees or yen.
  const quick = def.unit === 'usdbig' && currency !== 'USD' ? undefined : QUICK[def.id];
  const unit = unitHint(def, currency);
  const box = (invalid: boolean) =>
    `h-9 w-full rounded-md border bg-[#131722] px-2.5 text-[14px] tabular-nums text-white outline-none placeholder:text-[#5d606b] focus:border-[#2962ff] ${invalid ? 'border-[#f23645]' : 'border-[#363a45]'}`;

  return (
    <div className="space-y-3 p-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-[11px] uppercase tracking-wide text-[#868993]">
            Min {unit}
          </span>
          <input
            autoFocus
            inputMode="decimal"
            className={box(lo === null || crossed)}
            placeholder="Any"
            value={min}
            onChange={(e) => {
              setMin(e.target.value);
              push(e.target.value, max);
            }}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] uppercase tracking-wide text-[#868993]">
            Max {unit}
          </span>
          <input
            inputMode="decimal"
            className={box(hi === null || crossed)}
            placeholder="Any"
            value={max}
            onChange={(e) => {
              setMax(e.target.value);
              push(min, e.target.value);
            }}
          />
        </label>
      </div>
      {bad || crossed ? (
        <p className="text-[12px] text-[#f23645]">
          {crossed ? 'Min is above max.' : 'Enter a number: 2, 0.5, 15M, 300K.'}
        </p>
      ) : null}
      {quick ? (
        <div className="flex flex-wrap gap-1.5">
          {quick.values.map((n) => (
            <button
              key={n}
              type="button"
              className="rounded-md border border-[#363a45] px-2 py-1 text-[12px] text-[#b2b5be] hover:border-[#2962ff] hover:text-white"
              onClick={() => {
                const text = inputText(def, n);
                if (quick.bound === 'min') {
                  setMin(text);
                  push(text, max);
                } else {
                  setMax(text);
                  push(min, text);
                }
              }}
            >
              {quick.bound === 'min' ? '>' : '<'} {inputText(def, n)}
            </button>
          ))}
        </div>
      ) : null}
      {def.hint ? <p className="text-[12px] leading-snug text-[#868993]">{def.hint}</p> : null}
    </div>
  );
}

function MultiEditor({
  options,
  selected,
  onChange,
  searchable,
}: {
  options: readonly string[];
  selected: string[];
  onChange: (next: string[]) => void;
  searchable: boolean;
}) {
  const [q, setQ] = useState('');
  const shown = options.filter((o) => o.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <div className="flex min-h-0 flex-col">
      {searchable ? (
        <div className="border-b border-[#2a2e39] p-2">
          <input
            autoFocus
            className="h-8 w-full rounded-md border border-[#363a45] bg-[#131722] px-2.5 text-[13px] text-white outline-none placeholder:text-[#5d606b] focus:border-[#2962ff]"
            placeholder="Search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      ) : null}
      <ul className="min-h-0 flex-1 overflow-y-auto py-1" style={{ maxHeight: 280 }}>
        {shown.map((o) => (
          <li key={o}>
            <label className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 hover:bg-[#2a2e39]">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[#2962ff]"
                checked={selected.includes(o)}
                onChange={() => onChange(toggleIn(selected, o))}
              />
              <span>{o}</span>
            </label>
          </li>
        ))}
        {!shown.length ? (
          <li className="px-3 py-2 text-[#868993]">Nothing matches “{q}”.</li>
        ) : null}
      </ul>
    </div>
  );
}

function FilterPill({
  def,
  filters,
  open,
  onOpen,
  onChange,
  currency,
}: {
  def: FieldDef;
  filters: FilterState;
  open: boolean;
  onOpen: (open: boolean) => void;
  onChange: (next: FilterState) => void;
  currency: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const v = filters[def.id];
  const active = isActive(v);
  const summary = isRange(v)
    ? rangeLabel(def.unit, v, currency)
    : isMulti(v)
      ? v.in.length === 1
        ? v.in[0]!
        : `${v.in.length} selected`
      : '';

  return (
    <div ref={ref} className="relative shrink-0">
      {active ? (
        <div className={`${pillBase} overflow-hidden border-white bg-white text-[#131722]`}>
          <button
            type="button"
            className="flex h-full items-center gap-1.5 px-2"
            onClick={() => onOpen(!open)}
          >
            <span className="text-[#6a6d78]">{def.short ?? def.label}</span>
            <span className="max-w-[180px] truncate font-semibold">{summary}</span>
          </button>
          <button
            type="button"
            title={`Clear ${def.label}`}
            aria-label={`Clear ${def.label}`}
            className="flex h-full w-7 items-center justify-center border-l border-[#d1d4dc] text-[#131722] hover:bg-[#e0e3eb]"
            onClick={() => {
              onChange(withFilter(filters, def.id, null));
              onOpen(false);
            }}
          >
            <Icons.close size={12} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          className={`${idle} ${open ? 'border-[#2962ff] text-white' : ''}`}
          onClick={() => onOpen(!open)}
        >
          {def.short ?? def.label}
          <Chevron />
        </button>
      )}
      {open ? (
        <Popover
          anchor={ref}
          onClose={() => onOpen(false)}
          width={def.kind === 'multi' ? 300 : 290}
        >
          <div className="flex items-center justify-between border-b border-[#2a2e39] px-3 py-2">
            <span className="font-medium text-white">{def.label}</span>
            {active ? (
              <button
                type="button"
                className="text-[12px] text-[#2962ff] hover:underline"
                onClick={() => onChange(withFilter(filters, def.id, null))}
              >
                Clear
              </button>
            ) : null}
          </div>
          {def.kind === 'multi' ? (
            <MultiEditor
              options={def.options ?? []}
              selected={isMulti(v) ? v.in : []}
              searchable={(def.options?.length ?? 0) > 8}
              onChange={(next) => onChange(withFilter(filters, def.id, { in: next }))}
            />
          ) : (
            <RangeEditor
              def={def}
              currency={currency}
              value={isRange(v) ? v : undefined}
              onChange={(r) => onChange(withRange(filters, def.id, r))}
            />
          )}
        </Popover>
      ) : null}
    </div>
  );
}

/** The `US ≡` chip: which market, which of its exchanges, and (US only) whether warrants/rights/units are kept out. */
function UniverseChip({
  market,
  filters,
  onChange,
  onMarket,
}: {
  market: Market;
  filters: FilterState;
  onChange: (next: FilterState) => void;
  onMarket: (id: string) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const ex = filters.exchange;
  const venues = isMulti(ex) ? ex.in : [];
  const all = Object.keys(market.venues);
  const common = isToggle(filters.commonOnly) && filters.commonOnly.on;
  const summary =
    venues.length === 0
      ? 'All venues'
      : venues.length === all.length && all.length > 1
        ? 'All venues'
        : venues.join(' · ');
  return (
    <div className="relative shrink-0">
      <button
        ref={ref}
        type="button"
        className={`${idle} ${open ? 'border-[#2962ff] text-white' : ''} text-white`}
        onClick={() => setOpen((o) => !o)}
        title={`${market.label} · ${summary}${market.commonStockOption && common ? ' · common stock only' : ''}`}
      >
        <span aria-hidden>{market.flag}</span>
        <span className="font-semibold">{market.id.toUpperCase()}</span>
        <span className="hidden max-w-[150px] truncate text-[12px] font-normal text-[#868993] sm:inline">
          {summary}
        </span>
        <Chevron />
      </button>
      {open ? (
        <Popover anchor={ref} onClose={() => setOpen(false)} width={280}>
          <div className="min-h-0 flex-1 overflow-y-auto" style={{ maxHeight: 520 }}>
            <div className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
              Market
            </div>
            {MARKET_LIST.map((mk) => (
              <button
                key={mk.id}
                type="button"
                className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left hover:bg-[#2a2e39] ${mk.id === market.id ? 'text-[#2962ff]' : ''}`}
                onClick={() => {
                  if (mk.id !== market.id) onMarket(mk.id);
                  setOpen(false);
                }}
              >
                <span aria-hidden>{mk.flag}</span>
                <span className="flex-1">{mk.label}</span>
                <span className="text-[11px] text-[#868993]">{mk.currency}</span>
                {mk.id === market.id ? <span aria-hidden>✓</span> : null}
              </button>
            ))}
            {all.length > 1 ? (
              <>
                <div className="mt-1 border-t border-[#2a2e39] px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
                  Exchanges
                </div>
                {all.map((o) => (
                  <label
                    key={o}
                    className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 hover:bg-[#2a2e39]"
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[#2962ff]"
                      checked={venues.includes(o)}
                      onChange={() =>
                        onChange({ ...filters, exchange: { in: toggleIn(venues, o) } })
                      }
                    />
                    <span>{o}</span>
                    {o === 'OTC' ? (
                      <span className="text-[12px] text-[#868993]">thin, often untradeable</span>
                    ) : null}
                    {market.id !== 'us' && !market.defaultVenues.includes(o) ? (
                      <span className="text-[12px] text-[#868993]">may repeat names</span>
                    ) : null}
                  </label>
                ))}
              </>
            ) : null}
            {market.commonStockOption ? (
              <label className="mt-1 flex cursor-pointer items-start gap-2.5 border-t border-[#2a2e39] px-3 py-2.5 hover:bg-[#2a2e39]">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-[#2962ff]"
                  checked={common}
                  onChange={() => onChange({ ...filters, commonOnly: { on: !common } })}
                />
                <span>
                  Common stock only
                  <span className="block text-[12px] leading-snug text-[#868993]">
                    Leaves out warrants, rights, units and preferred shares.
                  </span>
                </span>
              </label>
            ) : null}
          </div>
        </Popover>
      ) : null}
    </div>
  );
}

function AddFilter({ shown, onAdd }: { shown: readonly string[]; onAdd: (id: string) => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  useEffect(() => {
    if (!open) setQ('');
  }, [open]);
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return GROUP_ORDER.map((g) => ({
      group: g,
      items: FILTERABLE.filter((d) => d.group === g && d.label.toLowerCase().includes(needle)),
    })).filter((g) => g.items.length);
  }, [q]);
  return (
    <div className="relative shrink-0">
      <button
        ref={ref}
        type="button"
        title="Add filter"
        aria-label="Add filter"
        className={`${pillBase} w-7 justify-center border-[#363a45] text-[#b2b5be] hover:border-[#5d606b] hover:text-white ${open ? 'border-[#2962ff] text-white' : ''}`}
        onClick={() => setOpen((o) => !o)}
      >
        <Icons.plus size={14} />
      </button>
      {open ? (
        <Popover anchor={ref} onClose={() => setOpen(false)} width={300}>
          <div className="border-b border-[#2a2e39] p-2">
            <input
              autoFocus
              className="h-8 w-full rounded-md border border-[#363a45] bg-[#131722] px-2.5 text-[13px] text-white outline-none placeholder:text-[#5d606b] focus:border-[#2962ff]"
              placeholder="Search filters"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-1" style={{ maxHeight: 380 }}>
            {groups.map(({ group, items }) => (
              <div key={group}>
                <div className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
                  {group}
                </div>
                {items.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-[#2a2e39]"
                    onClick={() => {
                      setOpen(false);
                      onAdd(d.id);
                    }}
                  >
                    <span>{d.label}</span>
                    <span className="text-[11px] text-[#868993]">
                      {shown.includes(d.id)
                        ? 'on bar'
                        : d.tier === 'quote'
                          ? ''
                          : d.yahoo
                            ? ''
                            : 'slower'}
                    </span>
                  </button>
                ))}
              </div>
            ))}
            {!groups.length ? (
              <div className="px-3 py-3 text-[#868993]">No filter matches “{q}”.</div>
            ) : null}
          </div>
        </Popover>
      ) : null}
    </div>
  );
}

interface BarProps {
  market: Market;
  onMarket: (id: string) => void;
  filters: FilterState;
  pills: readonly string[];
  openId: string | null;
  onOpen: (id: string | null) => void;
  onChange: (next: FilterState) => void;
  onAddPill: (id: string) => void;
  onReset: () => void;
}

/** The row of filter pills under the screen's name: the heart of the TradingView screener. */
export function FilterBar({
  market,
  onMarket,
  filters,
  pills,
  openId,
  onOpen,
  onChange,
  onAddPill,
  onReset,
}: BarProps) {
  const activeCount = pills.filter((id) => isActive(filters[id])).length;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <UniverseChip market={market} filters={filters} onChange={onChange} onMarket={onMarket} />
      <span className="mx-0.5 hidden h-4 w-px bg-[#363a45] sm:block" />
      {pills.map((id) => {
        const def = FIELD.get(id);
        if (!def || (def.kind !== 'range' && def.kind !== 'multi')) return null;
        return (
          <FilterPill
            key={id}
            currency={market.currency}
            def={def}
            filters={filters}
            open={openId === id}
            onOpen={(o) => onOpen(o ? id : null)}
            onChange={onChange}
          />
        );
      })}
      <AddFilter shown={pills} onAdd={onAddPill} />
      {activeCount > 0 ? (
        <button
          type="button"
          className="ml-1 text-[12px] text-[#868993] hover:text-white"
          onClick={onReset}
        >
          Reset
        </button>
      ) : null}
    </div>
  );
}
