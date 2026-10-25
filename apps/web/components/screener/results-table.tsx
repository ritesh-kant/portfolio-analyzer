'use client';

import { useRef, useState } from 'react';
import { COLUMNABLE, FIELD, GROUP_ORDER, VIEWS, type FieldDef } from '../../lib/screener/fields';
import { formatValue } from '../../lib/screener/format';
import { toggleIn } from '../../lib/screener/screens';
import type { ScreenerRow, SortState } from '../../lib/screener/types';
import { Icons } from '../tv/icons';
import { Popover } from './popover';

const GREEN = '#089981';
const RED = '#f23645';
/** Columns coloured by sign. Others (volume, price…) stay neutral: only direction-of-move numbers earn colour. */
const SIGNED = new Set([
  'changePct',
  'change',
  'gapPct',
  'fromOpenPct',
  'perf52w',
  'vs50d',
  'vs200d',
]);

const read = (row: ScreenerRow, id: string) =>
  (row as unknown as Record<string, number | string | null | undefined>)[id];

function logoColor(symbol: string): string {
  let h = 0;
  for (const ch of symbol) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 38%)`;
}

function Cell({ def, row, currency }: { def: FieldDef; row: ScreenerRow; currency: string }) {
  const v = read(row, def.id);
  if (def.unit === 'text') {
    return <span className={v ? 'truncate' : 'text-[#5d606b]'}>{v ? String(v) : '—'}</span>;
  }
  const n = typeof v === 'number' ? v : null;
  const text = formatValue(def.unit, n, SIGNED.has(def.id));
  const color = SIGNED.has(def.id) && n != null && n !== 0 ? (n > 0 ? GREEN : RED) : undefined;
  return (
    <span style={{ color }} className={n == null ? 'text-[#5d606b]' : undefined}>
      {def.id === 'float' && row.floatEstimated ? (
        <span
          title="Yahoo has no float for this name: showing shares outstanding, an upper bound"
          className="mr-0.5 text-[#ffb74d]"
        >
          ≈
        </span>
      ) : null}
      {text}
      {n != null && (def.unit === 'usd' || def.unit === 'usdbig') ? (
        <span className="ml-1 text-[11px] font-medium text-[#868993]">{currency}</span>
      ) : null}
    </span>
  );
}

/** The `Custom ▾` menu: a column set to start from, and a checkbox per column. */
export function ColumnsMenu({
  columns,
  onChange,
  anchor,
  onClose,
}: {
  columns: string[];
  onChange: (next: string[]) => void;
  anchor: React.RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  return (
    <Popover anchor={anchor} onClose={onClose} width={300}>
      <div className="min-h-0 flex-1 overflow-y-auto pb-1" style={{ maxHeight: 460 }}>
        <div className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
          Column sets
        </div>
        {Object.entries(VIEWS).map(([name, ids]) => {
          const on = ids.length === columns.length && ids.every((id, i) => columns[i] === id);
          return (
            <button
              key={name}
              type="button"
              className={`flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-[#2a2e39] ${on ? 'text-[#2962ff]' : ''}`}
              onClick={() => onChange([...ids])}
            >
              {name}
              {on ? <span aria-hidden>✓</span> : null}
            </button>
          );
        })}
        {GROUP_ORDER.map((g) => {
          const items = COLUMNABLE.filter((d) => d.group === g);
          return items.length ? (
            <div key={g}>
              <div className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-[#868993]">
                {g}
              </div>
              {items.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 hover:bg-[#2a2e39]"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[#2962ff]"
                    checked={columns.includes(d.id)}
                    onChange={() => {
                      const next = toggleIn(columns, d.id);
                      if (next.length) onChange(next);
                    }}
                  />
                  <span>{d.label}</span>
                  {d.tier !== 'quote' ? (
                    <span className="text-[11px] text-[#868993]">slower</span>
                  ) : null}
                </label>
              ))}
            </div>
          ) : null;
        })}
      </div>
    </Popover>
  );
}

interface Props {
  rows: ScreenerRow[];
  total: number;
  columns: string[];
  /** What price columns are quoted in (shown beside the number). */
  currency: string;
  /** Market id: outside the US the exchange suffix (RELIANCE.NS) is trimmed from the badge. */
  market: string;
  sort: SortState;
  loading: boolean;
  selected: string | null;
  onSort: (key: string) => void;
  onOpen: (row: ScreenerRow) => void;
  onAddWatch?: (row: ScreenerRow) => void;
  onColumns: (next: string[]) => void;
}

const th = 'whitespace-nowrap px-3 py-2 text-[13px] font-normal text-[#868993]';

export function ResultsTable({
  currency,
  market,
  rows,
  total,
  columns,
  sort,
  loading,
  selected,
  onSort,
  onOpen,
  onAddWatch,
  onColumns,
}: Props) {
  const plus = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState(false);
  const defs = columns.map((id) => FIELD.get(id)).filter((d): d is FieldDef => !!d);

  const arrow = (key: string) =>
    sort.key === key ? (
      <span className="mr-1 text-white" aria-hidden>
        {sort.dir === 'desc' ? '↓' : '↑'}
      </span>
    ) : null;

  return (
    <div
      className={`min-h-0 flex-1 overflow-auto transition-opacity ${loading ? 'opacity-60' : ''}`}
    >
      <table className="w-full border-separate border-spacing-0 text-[14px] text-[#d1d4dc]">
        <thead className="sticky top-0 z-10 bg-[#131722]">
          <tr>
            <th className={`${th} sticky left-0 z-20 min-w-[210px] bg-[#131722] text-left`}>
              <button
                type="button"
                className="flex flex-col items-start leading-tight hover:text-white"
                onClick={() => onSort('symbol')}
              >
                <span>{arrow('symbol')}Symbol</span>
                <span className="text-[#d1d4dc]">{total}</span>
              </button>
            </th>
            {defs.map((d) => (
              <th
                key={d.id}
                className={`${th} ${d.unit === 'text' ? 'text-left' : 'text-right'}`}
                style={{ minWidth: d.w ?? 90 }}
              >
                <button
                  type="button"
                  title={d.hint ?? d.label}
                  className="whitespace-normal leading-tight hover:text-white"
                  onClick={() => onSort(d.id)}
                >
                  {arrow(d.id)}
                  {d.short ?? d.label}
                </button>
              </th>
            ))}
            <th className={`${th} w-12 text-right`}>
              <button
                ref={plus}
                type="button"
                title="Columns"
                aria-label="Columns"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
                onClick={() => setMenu((m) => !m)}
              >
                <Icons.plus size={18} />
              </button>
              {menu ? (
                <ColumnsMenu
                  columns={columns}
                  onChange={onColumns}
                  anchor={plus}
                  onClose={() => setMenu(false)}
                />
              ) : null}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const active = row.symbol === selected;
            const bg = active ? 'bg-[#2a2e39]' : 'bg-[#131722] group-hover:bg-[#1e222d]';
            return (
              <tr key={row.symbol} className="group cursor-pointer" onClick={() => onOpen(row)}>
                <td className={`sticky left-0 z-[1] border-t border-[#2a2e39] px-3 py-2 ${bg}`}>
                  <div className="flex items-center gap-3">
                    <span
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-semibold text-white"
                      style={{ background: logoColor(row.symbol) }}
                      aria-hidden
                    >
                      {row.symbol[0]}
                    </span>
                    <div className="min-w-0">
                      <span className="inline-block rounded-md bg-[#2a2e39] px-2 py-0.5 text-[13px] font-semibold text-white">
                        {market === 'us' ? row.symbol : row.symbol.replace(/\.[A-Z]{1,3}$/, '')}
                      </span>
                      <div
                        className="max-w-[130px] truncate text-[11px] text-[#868993]"
                        title={row.name}
                      >
                        {row.name}
                      </div>
                    </div>
                    {onAddWatch ? (
                      <button
                        type="button"
                        title="Add to watchlist"
                        aria-label={`Add ${row.symbol} to watchlist`}
                        className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-[#b2b5be] opacity-0 hover:bg-[#363a45] hover:text-white focus-visible:opacity-100 group-hover:opacity-100"
                        onClick={(e) => {
                          e.stopPropagation();
                          onAddWatch(row);
                        }}
                      >
                        <Icons.plus size={16} />
                      </button>
                    ) : null}
                  </div>
                </td>
                {defs.map((d) => (
                  <td
                    key={d.id}
                    className={`whitespace-nowrap border-t border-[#2a2e39] px-3 py-2 tabular-nums ${d.unit === 'text' ? 'text-left' : 'text-right'} ${bg}`}
                  >
                    <Cell def={d} row={row} currency={currency} />
                  </td>
                ))}
                <td className={`border-t border-[#2a2e39] ${bg}`} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
