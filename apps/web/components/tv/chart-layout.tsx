'use client';

import { useEffect, useRef, useState } from 'react';
import {
  type ChartLayout as Layout,
  LAYOUT_KEY,
  layoutById,
  type LayoutSync,
  SYNC_DEFAULTS,
  SYNC_KEY,
} from '../../lib/tv/layout';
import { load, save } from '../../lib/tv/storage';
import { LayoutPicker } from './layout-picker';
import { TvChart } from './tv-chart';

const SIZES_KEY = 'tv:layoutSizes';
type Sizes = { cols: number[]; rows: number[] };
const MIN_SHARE = 0.08;

const ones = (tracks: string) => tracks.split(' ').map(() => 1);
const track = (w: number[]) => w.map((x) => `minmax(0, ${x}fr)`).join(' ');
const sum = (w: number[]) => w.reduce((a, b) => a + b, 0);

/** Drag handles between neighbouring charts, one segment wherever a boundary really separates two cells. */
function Gutters({
  layout,
  sizes,
  onSizes,
  box,
}: {
  layout: Layout;
  sizes: Sizes;
  onSizes: (s: Sizes) => void;
  box: React.RefObject<HTMLDivElement | null>;
}) {
  const drag = (axis: 'cols' | 'rows', b: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    const el = box.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = axis === 'cols' ? rect.width : rect.height;
    const start = axis === 'cols' ? e.clientX : e.clientY;
    const base = sizes[axis].slice();
    const total = sum(base);
    const pair = base[b]! + base[b + 1]!;
    const move = (ev: PointerEvent) => {
      const delta = (((axis === 'cols' ? ev.clientX : ev.clientY) - start) / px) * total;
      const a = Math.min(pair - MIN_SHARE * total, Math.max(MIN_SHARE * total, base[b]! + delta));
      const next = base.slice();
      next[b] = a;
      next[b + 1] = pair - a;
      onSizes({ ...sizes, [axis]: next });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.style.userSelect = '';
    };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const cT = sizes.cols;
  const rT = sizes.rows;
  const cTot = sum(cT);
  const rTot = sum(rT);
  const before = (w: number[], n: number) => sum(w.slice(0, n));
  const span = (c: { col: number; colSpan?: number; row: number; rowSpan?: number }) => ({
    c0: c.col,
    c1: c.col + (c.colSpan ?? 1) - 1,
    r0: c.row,
    r1: c.row + (c.rowSpan ?? 1) - 1,
  });
  const out: React.ReactNode[] = [];
  // vertical dividers: boundary after column track b (1-based), one segment per row track
  for (let b = 1; b < cT.length; b++)
    for (let r = 1; r <= rT.length; r++) {
      const crossed = layout.cells.some((c) => {
        const s = span(c);
        return s.c0 <= b && s.c1 >= b + 1 && s.r0 <= r && s.r1 >= r;
      });
      if (crossed) continue;
      out.push(
        <div
          key={`v${b}-${r}`}
          onPointerDown={drag('cols', b - 1)}
          className="absolute z-20 w-[7px] -translate-x-1/2 cursor-col-resize hover:bg-[#2962ff]/60"
          style={{
            left: `${(before(cT, b) / cTot) * 100}%`,
            top: `${(before(rT, r - 1) / rTot) * 100}%`,
            height: `${(rT[r - 1]! / rTot) * 100}%`,
          }}
        />,
      );
    }
  for (let b = 1; b < rT.length; b++)
    for (let c = 1; c <= cT.length; c++) {
      const crossed = layout.cells.some((x) => {
        const s = span(x);
        return s.r0 <= b && s.r1 >= b + 1 && s.c0 <= c && s.c1 >= c;
      });
      if (crossed) continue;
      out.push(
        <div
          key={`h${b}-${c}`}
          onPointerDown={drag('rows', b - 1)}
          className="absolute z-20 h-[7px] -translate-y-1/2 cursor-row-resize hover:bg-[#2962ff]/60"
          style={{
            top: `${(before(rT, b) / rTot) * 100}%`,
            left: `${(before(cT, c - 1) / cTot) * 100}%`,
            width: `${(cT[c - 1]! / cTot) * 100}%`,
          }}
        />,
      );
    }
  return <>{out}</>;
}

/**
 * /chart host. Every layout is a grid of full charts sharing ONE control panel: the top bar, the drawing tools
 * and the side panels are rendered (through portals) by whichever chart is active, TradingView style.
 * Click a chart to make it the active one.
 */
export function ChartLayout() {
  const [layoutId, setLayoutId] = useState('1');
  const [sync, setSync] = useState<LayoutSync>(SYNC_DEFAULTS);
  const [active, setActive] = useState(0);
  const [topSlot, setTopSlot] = useState<HTMLElement | null>(null);
  const [leftSlot, setLeftSlot] = useState<HTMLElement | null>(null);
  const [railSlot, setRailSlot] = useState<HTMLElement | null>(null);
  const [bottomSlot, setBottomSlot] = useState<HTMLElement | null>(null);
  const [allSizes, setAllSizes] = useState<Record<string, Sizes>>({});
  const gridRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setLayoutId(load<string>(LAYOUT_KEY, '1'));
    setSync({ ...SYNC_DEFAULTS, ...load<Partial<LayoutSync>>(SYNC_KEY, {}) });
    setAllSizes(load<Record<string, Sizes>>(SIZES_KEY, {}));
    setReady(true);
  }, []);

  const layout = layoutById(layoutId);
  const change = (id: string) => {
    setLayoutId(id);
    save(LAYOUT_KEY, id);
    setActive((a) => (a < layoutById(id).cells.length ? a : 0));
  };
  const changeSync = (patch: Partial<LayoutSync>) => {
    const next = { ...sync, ...patch };
    setSync(next);
    save(SYNC_KEY, next);
  };
  const picker = <LayoutPicker layout={layout} onChange={change} sync={sync} onSync={changeSync} />;
  const sizes: Sizes = (() => {
    const saved = allSizes[layout.id];
    const c = ones(layout.cols);
    const r = ones(layout.rows);
    return saved && saved.cols.length === c.length && saved.rows.length === r.length
      ? saved
      : { cols: c, rows: r };
  })();
  const setSizes = (next: Sizes) => {
    const all = { ...allSizes, [layout.id]: next };
    setAllSizes(all);
    save(SIZES_KEY, all);
  };
  const multi = layout.cells.length > 1;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#131722] text-[#d1d4dc]">
      <div ref={setTopSlot} className="shrink-0" />
      <div className="flex min-h-0 flex-1">
        <div ref={setLeftSlot} className="flex min-h-0 shrink-0" />
        <div
          ref={gridRef}
          className="relative grid min-h-0 min-w-0 flex-1 gap-[3px] bg-[#2a2e39]"
          style={{ gridTemplateColumns: track(sizes.cols), gridTemplateRows: track(sizes.rows) }}
        >
          {multi ? (
            <Gutters layout={layout} sizes={sizes} onSizes={setSizes} box={gridRef} />
          ) : null}
          {ready
            ? layout.cells.map((cell, i) => (
                <div
                  key={`${layout.id}-${i}`}
                  className="min-h-0 min-w-0"
                  style={{
                    gridColumn: `${cell.col} / span ${cell.colSpan ?? 1}`,
                    gridRow: `${cell.row} / span ${cell.rowSpan ?? 1}`,
                  }}
                >
                  <TvChart
                    layoutSlot={picker}
                    embed={{
                      pane: i,
                      active: i === active,
                      onActivate: () => setActive(i),
                      multi,
                      topSlot,
                      leftSlot,
                      railSlot,
                      bottomSlot,
                    }}
                  />
                </div>
              ))
            : null}
        </div>
        <div ref={setRailSlot} className="flex min-h-0 shrink-0" />
      </div>
      <div ref={setBottomSlot} className="shrink-0" />
    </div>
  );
}
