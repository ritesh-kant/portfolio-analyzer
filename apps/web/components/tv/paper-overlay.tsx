'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import type { IChartApi, ISeriesApi, SeriesType, Time } from 'lightweight-charts';
import { unrealized, type PaperState, type Side } from '../../lib/tv/paper';

const GREEN = '#089981';
const RED = '#f23645';
const BLUE = '#2962ff';

const money = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (n: number) => `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}`;

export interface QuickOrder {
  side: Side;
  type: 'limit' | 'stop';
  price: number;
}

interface Handlers {
  onMoveOrder: (id: number, price: number) => string | null;
  onUpdateOrder: (
    id: number,
    patch: { type?: 'limit' | 'stop'; qty?: number; sl?: number | null; tp?: number | null },
  ) => string | null;
  onOrderMarket: (id: number) => string | null;
  onCancelOrder: (id: number) => void;
  onBracket: (sl: number | undefined | null, tp: number | undefined | null) => string | null;
  onClosePos: () => void;
  onReverse: () => string | null;
  onQuick: (o: QuickOrder) => string | null;
  onDialog: (price: number) => void;
  onModifyOrder: (id: number) => void;
  onHLine: (price: number) => void;
}

interface Props extends Handlers {
  chartRef: React.RefObject<IChartApi | null>;
  getSeries: () => ISeriesApi<SeriesType, Time> | null;
  wrap: HTMLDivElement | null;
  state: PaperState;
  symbol: string;
  last: number | null;
  decimals: number;
  qty: number;
  /** The chart is idle (no drawing tool active). */
  enabled: boolean;
  showPositions: boolean;
  showOrders: boolean;
}

const ORANGE = '#ff9800';

interface ChipProps {
  color: string;
  filled?: boolean;
  ghost?: boolean;
  tip?: string;
  title?: string;
  onClick?: () => void;
  onPointerDown?: (e: React.PointerEvent) => void;
  cursor?: string;
  children: ReactNode;
}

/** One box of TradingView's order tag: filled (side / P&L), bordered (qty, type, ×) or dotted ghost (TP / SL: drag to add). */
function Chip({
  color,
  filled,
  ghost,
  tip,
  title,
  onClick,
  onPointerDown,
  cursor,
  children,
}: ChipProps) {
  const cls =
    'flex h-[22px] items-center rounded-[3px] px-1.5 text-[12px] font-semibold leading-none tabular-nums';
  const style = {
    background: filled ? color : '#131722',
    color: filled ? '#fff' : ghost ? color : '#d1d4dc',
    border: `1px ${ghost ? 'dotted' : 'solid'} ${filled ? color : ghost ? `${color}99` : color}`,
    cursor: cursor ?? (onClick ? 'pointer' : 'default'),
  };
  const inner =
    onClick || onPointerDown ? (
      <button
        type="button"
        className={cls}
        style={style}
        title={title}
        onClick={onClick}
        onPointerDown={onPointerDown}
      >
        {children}
      </button>
    ) : (
      <span className={cls} style={style} title={title}>
        {children}
      </span>
    );
  return (
    <span className="group relative">
      {inner}
      {tip ? (
        <span className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 hidden -translate-x-1/2 whitespace-nowrap rounded bg-[#363a45] px-2.5 py-1.5 text-[13px] font-normal text-[#d1d4dc] shadow-lg group-hover:block">
          {tip}
        </span>
      ) : null}
    </span>
  );
}

/** One horizontal level: a line across the plot, a price tag on the axis and an interactive label. */
function Level({
  price,
  color,
  dashed,
  axisText,
  children,
  draggable,
  onDrop,
  onTap,
  chartRef,
  getSeries,
  decimals,
  fillAxis = true,
}: {
  price: number;
  color: string;
  dashed?: boolean;
  axisText?: string;
  children: (shown: number) => ReactNode;
  draggable?: boolean;
  onDrop?: (price: number) => string | null;
  /** A click (no drag) on the label. */
  onTap?: () => void;
  chartRef: Props['chartRef'];
  getSeries: Props['getSeries'];
  decimals: number;
  fillAxis?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const lineEl = useRef<HTMLDivElement>(null);
  const axisEl = useRef<HTMLDivElement>(null);
  const drag = useRef<{ price: number; moved: boolean } | null>(null);
  const [shown, setShown] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const priceRef = useRef(price);
  priceRef.current = price;

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const el = root.current;
      const series = getSeries();
      const chart = chartRef.current;
      if (!el || !series || !chart) return;
      const p = drag.current ? drag.current.price : priceRef.current;
      const y = series.priceToCoordinate(p);
      const h = chart.panes()[0]?.getHeight() ?? 0;
      if (y == null || y < 0 || y > h) {
        el.style.display = 'none';
        return;
      }
      const axisW = chart.priceScale('right').width();
      el.style.display = '';
      el.style.transform = `translateY(${y}px)`;
      if (lineEl.current) lineEl.current.style.right = `${axisW}px`;
      if (axisEl.current) axisEl.current.style.width = `${axisW}px`;
      el.style.setProperty('--axis', `${axisW}px`);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [chartRef, getSeries]);

  const down = (e: React.PointerEvent) => {
    if (!draggable || e.button !== 0) return;
    if ((e.target as HTMLElement).closest('button')) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { price: priceRef.current, moved: false };
    setErr(null);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    const series = getSeries();
    const host = root.current?.parentElement;
    if (!d || !series || !host) return;
    const y = e.clientY - host.getBoundingClientRect().top;
    const raw = series.coordinateToPrice(y);
    if (raw == null) return;
    d.price = Number(Number(raw).toFixed(decimals));
    d.moved = true;
    setShown(d.price);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    setShown(null);
    if (d?.moved && onDrop) setErr(onDrop(d.price));
    else if (d && !d.moved && onTap) onTap();
  };

  const live = shown ?? price;
  return (
    <div ref={root} className="absolute left-0 top-0 w-full" style={{ height: 0 }}>
      <div
        ref={lineEl}
        className="absolute left-0"
        style={{
          top: 0,
          borderTop: `1px ${dashed ? 'dashed' : 'solid'} ${color}`,
        }}
      />
      <div
        ref={axisEl}
        className="absolute right-0 -translate-y-1/2 px-1 text-center text-[11px] font-semibold tabular-nums text-white"
        style={{ background: fillAxis ? color : '#1e222d', border: `1px solid ${color}` }}
      >
        {axisText ?? live.toFixed(decimals)}
      </div>
      <div
        data-tv-ui
        className="pointer-events-auto absolute -translate-y-1/2 select-none"
        style={{
          right: 'calc(var(--axis, 56px) + 72px)',
          cursor: draggable ? 'ns-resize' : 'default',
        }}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
      >
        <div className="flex items-center gap-1">{children(live)}</div>
        {err ? (
          <div className="absolute left-0 top-[22px] z-10 w-max max-w-[220px] rounded bg-[#2a1215] px-2 py-1 text-[11px] text-[#f23645]">
            {err}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * TradingView's on-chart trading layer: the position line (P&L tag, protect / reverse / close), draggable
 * stop-loss, take-profit and working-order lines, a "+" on the price axis and a right-click order menu.
 */
export function PaperOverlay(p: Props) {
  const { chartRef, getSeries, state, symbol, last, decimals, qty, wrap } = p;
  const host = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; price: number } | null>(null);
  const [hover, setHover] = useState<{ y: number; price: number } | null>(null);
  const [ghost, setGhost] = useState<{ y: number; price: number; kind: 'sl' | 'tp' } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const pos = state.positions[symbol];
  const orders = state.orders.filter((o) => o.symbol === symbol);
  const label = symbol.replace(/\.(NS|BO)$|=[FX]$/, '');
  const fmt = (n: number) => n.toFixed(decimals);

  const toPrice = useCallback(
    (clientY: number): number | null => {
      const series = getSeries();
      const chart = chartRef.current;
      const el = host.current;
      if (!series || !chart || !el) return null;
      const y = clientY - el.getBoundingClientRect().top;
      if (y < 0 || y > (chart.panes()[0]?.getHeight() ?? 0)) return null;
      const raw = series.coordinateToPrice(y);
      return raw == null ? null : Number(Number(raw).toFixed(decimals));
    },
    [getSeries, chartRef, decimals],
  );

  // "+" on the price axis follows the pointer; right-click opens the same menu.
  useEffect(() => {
    if (!wrap || !p.enabled) {
      setHover(null);
      return;
    }
    const inPlot = (e: MouseEvent) => {
      const chart = chartRef.current;
      const el = host.current;
      if (!chart || !el) return null;
      const r = el.getBoundingClientRect();
      const axisW = chart.priceScale('right').width();
      if (e.clientX < r.left || e.clientX > r.right - axisW) return null;
      const price = toPrice(e.clientY);
      return price == null ? null : { y: e.clientY - r.top, price };
    };
    const move = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('[data-tv-ui]')) return;
      setHover(inPlot(e));
    };
    const leave = () => setHover(null);
    const ctx = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('[data-tv-ui]')) return;
      const at = inPlot(e);
      if (!at) return;
      e.preventDefault();
      const r = host.current!.getBoundingClientRect();
      setMenu({ x: e.clientX - r.left, y: at.y, price: at.price });
    };
    const down = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-tv-paper-menu]')) setMenu(null);
    };
    wrap.addEventListener('mousemove', move);
    wrap.addEventListener('mouseleave', leave);
    wrap.addEventListener('contextmenu', ctx);
    window.addEventListener('mousedown', down);
    return () => {
      wrap.removeEventListener('mousemove', move);
      wrap.removeEventListener('mouseleave', leave);
      wrap.removeEventListener('contextmenu', ctx);
      window.removeEventListener('mousedown', down);
    };
  }, [wrap, p.enabled, chartRef, toPrice]);

  const flash = (m: string | null) => {
    if (!m) return;
    setToast(m);
    window.setTimeout(() => setToast(null), 3500);
  };

  const menuItems = (price: number) => {
    if (last == null) return [];
    const above = price > last;
    const items: { text: string; icon?: string; sep?: boolean; run: () => void }[] = [];
    const add = (side: Side, type: 'limit' | 'stop') =>
      items.push({
        text: `${side === 'buy' ? 'Buy' : 'Sell'} ${qty} ${label} @ ${fmt(price)} ${type}`,
        icon: side === 'buy' ? '▲' : '▼',
        run: () => flash(p.onQuick({ side, type, price })),
      });
    if (above) {
      add('sell', 'limit');
      add('buy', 'stop');
    } else {
      add('buy', 'limit');
      add('sell', 'stop');
    }
    items.push({ text: 'Create new order…', run: () => p.onDialog(price) });
    items.push({
      text: `Draw horizontal line at ${fmt(price)}`,
      icon: '—',
      sep: true,
      run: () => p.onHLine(price),
    });
    return items;
  };

  const lv = { chartRef, getSeries, decimals };
  const dir = pos ? (pos.qty > 0 ? 1 : -1) : 0;
  const posPnl = pos ? unrealized(pos, last ?? pos.avg) : 0;

  // TradingView's "drag to add": pull a dotted TP / SL chip onto the chart and let go at the price you want.
  const startGhost = (
    e: React.PointerEvent,
    kind: 'sl' | 'tp',
    apply: (price: number) => string | null,
  ) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement;
    el.setPointerCapture(e.pointerId);
    let moved = false;
    const show = (ev: PointerEvent) => {
      const price = toPrice(ev.clientY);
      const top = host.current?.getBoundingClientRect().top ?? 0;
      if (price != null) setGhost({ y: ev.clientY - top, price, kind });
      return price;
    };
    const move = (ev: PointerEvent) => {
      moved = true;
      show(ev);
    };
    const end = (ev: PointerEvent) => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', end);
      el.removeEventListener('pointercancel', end);
      const price = toPrice(ev.clientY);
      setGhost(null);
      if (moved && price != null) flash(apply(price));
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  };
  const ghostChips = (
    has: { sl: boolean; tp: boolean },
    apply: (kind: 'sl' | 'tp', price: number) => string | null,
  ) => (
    <>
      {has.tp ? null : (
        <Chip
          color={GREEN}
          ghost
          cursor="grab"
          tip="Drag to add take profit"
          onPointerDown={(e) => startGhost(e, 'tp', (v) => apply('tp', v))}
        >
          TP
        </Chip>
      )}
      {has.sl ? null : (
        <Chip
          color={ORANGE}
          ghost
          cursor="grab"
          tip="Drag to add stop loss"
          onPointerDown={(e) => startGhost(e, 'sl', (v) => apply('sl', v))}
        >
          SL
        </Chip>
      )}
    </>
  );
  const levelTag = (kind: 'sl' | 'tp', est: number, remove: () => void) => (
    <>
      <Chip color={kind === 'tp' ? GREEN : ORANGE} filled>
        {kind === 'tp' ? 'TP' : 'SL'}
      </Chip>
      <Chip color={kind === 'tp' ? GREEN : ORANGE}>{signed(est)}</Chip>
      <Chip
        color="#363a45"
        onClick={remove}
        title={kind === 'tp' ? 'Remove take profit' : 'Remove stop loss'}
      >
        ×
      </Chip>
    </>
  );

  return (
    <div ref={host} className="pointer-events-none absolute inset-0 z-[15] overflow-hidden">
      {pos && p.showPositions ? (
        <Level price={pos.avg} color={posPnl >= 0 ? GREEN : RED} axisText={fmt(pos.avg)} {...lv}>
          {() => (
            <>
              <Chip color={posPnl >= 0 ? GREEN : RED} filled>
                {signed(posPnl)}
              </Chip>
              {ghostChips({ sl: pos.sl != null, tp: pos.tp != null }, (kind, v) =>
                kind === 'sl' ? p.onBracket(v, pos.tp) : p.onBracket(pos.sl, v),
              )}
              <Chip color={dir > 0 ? GREEN : RED}>{Math.abs(pos.qty)}</Chip>
              <Chip color="#363a45" onClick={() => flash(p.onReverse())} title="Reverse position">
                ⇅
              </Chip>
              <Chip color="#363a45" onClick={p.onClosePos} title="Close position">
                ×
              </Chip>
            </>
          )}
        </Level>
      ) : null}
      {pos && p.showPositions && pos.tp != null ? (
        <Level
          key="tp"
          price={pos.tp}
          color={GREEN}
          dashed
          draggable
          onDrop={(v) => p.onBracket(pos.sl, v)}
          {...lv}
        >
          {(shown) =>
            levelTag('tp', Math.abs(pos.qty) * (shown - pos.avg) * dir, () =>
              flash(p.onBracket(pos.sl, null)),
            )
          }
        </Level>
      ) : null}
      {pos && p.showPositions && pos.sl != null ? (
        <Level
          key="sl"
          price={pos.sl}
          color={ORANGE}
          dashed
          draggable
          onDrop={(v) => p.onBracket(v, pos.tp)}
          {...lv}
        >
          {(shown) =>
            levelTag('sl', Math.abs(pos.qty) * (shown - pos.avg) * dir, () =>
              flash(p.onBracket(null, pos.tp)),
            )
          }
        </Level>
      ) : null}
      {(p.showOrders ? orders : []).map((o) => {
        const c = o.side === 'buy' ? BLUE : RED;
        const od = o.side === 'buy' ? 1 : -1;
        return (
          <span key={o.id}>
            <Level
              price={o.price}
              color={c}
              dashed
              draggable
              onDrop={(v) => p.onMoveOrder(o.id, v)}
              onTap={() => p.onModifyOrder(o.id)}
              fillAxis={false}
              {...lv}
            >
              {() => (
                <>
                  <Chip
                    color={c}
                    filled
                    title="Execute now at market"
                    onClick={() => flash(p.onOrderMarket(o.id))}
                  >
                    {o.side === 'buy' ? 'Buy' : 'Sell'}
                  </Chip>
                  {ghostChips({ sl: o.sl != null, tp: o.tp != null }, (kind, v) =>
                    p.onUpdateOrder(o.id, { [kind]: v }),
                  )}
                  <OrderQty
                    color={c}
                    qty={o.qty}
                    onCommit={(q) => flash(p.onUpdateOrder(o.id, { qty: q }))}
                  />
                  <OrderType
                    color={c}
                    type={o.type}
                    onPick={(t) =>
                      flash(
                        t === 'market' ? p.onOrderMarket(o.id) : p.onUpdateOrder(o.id, { type: t }),
                      )
                    }
                  />
                  <Chip color={c} onClick={() => p.onCancelOrder(o.id)} title="Cancel order">
                    ×
                  </Chip>
                </>
              )}
            </Level>
            {o.tp != null ? (
              <Level
                price={o.tp}
                color={GREEN}
                dashed
                draggable
                onDrop={(v) => p.onUpdateOrder(o.id, { tp: v })}
                {...lv}
              >
                {(shown) =>
                  levelTag('tp', o.qty * (shown - o.price) * od, () =>
                    flash(p.onUpdateOrder(o.id, { tp: null })),
                  )
                }
              </Level>
            ) : null}
            {o.sl != null ? (
              <Level
                price={o.sl}
                color={ORANGE}
                dashed
                draggable
                onDrop={(v) => p.onUpdateOrder(o.id, { sl: v })}
                {...lv}
              >
                {(shown) =>
                  levelTag('sl', o.qty * (shown - o.price) * od, () =>
                    flash(p.onUpdateOrder(o.id, { sl: null })),
                  )
                }
              </Level>
            ) : null}
          </span>
        );
      })}
      {ghost ? (
        <div className="absolute left-0 w-full" style={{ top: ghost.y, height: 0 }}>
          <div
            className="absolute left-0"
            style={{
              right: (chartRef.current?.priceScale('right').width() ?? 56) + 0,
              borderTop: `1px dashed ${ghost.kind === 'tp' ? GREEN : ORANGE}`,
            }}
          />
          <div
            className="absolute right-0 -translate-y-1/2 px-1 text-[11px] font-semibold tabular-nums text-white"
            style={{ background: ghost.kind === 'tp' ? GREEN : ORANGE }}
          >
            {fmt(ghost.price)}
          </div>
        </div>
      ) : null}
      {hover && !menu ? (
        <button
          type="button"
          data-tv-ui
          title={`Trade at ${fmt(hover.price)}`}
          onClick={(e) => {
            const r = host.current!.getBoundingClientRect();
            setMenu({ x: e.clientX - r.left, y: hover.y, price: hover.price });
          }}
          className="pointer-events-auto absolute flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full border border-[#d1d4dc] bg-[#131722] text-[15px] leading-none text-white hover:bg-[#2a2e39]"
          style={{
            top: hover.y,
            right: (chartRef.current?.priceScale('right').width() ?? 56) + 4,
          }}
        >
          +
        </button>
      ) : null}
      {menu ? (
        <div
          data-tv-ui
          data-tv-paper-menu
          className="pointer-events-auto absolute z-30 min-w-[260px] overflow-hidden rounded-md border border-[#363a45] bg-[#1e222d] py-1 text-[13px] shadow-2xl"
          style={{ left: Math.max(4, menu.x - 260), top: menu.y }}
        >
          {menuItems(menu.price).map((it) => (
            <div key={it.text}>
              {it.sep ? <div className="my-1 border-t border-[#363a45]" /> : null}
              <button
                type="button"
                className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[#d1d4dc] hover:bg-[#2a2e39]"
                onClick={() => {
                  setMenu(null);
                  it.run();
                }}
              >
                <span className="w-4 text-center text-[11px] text-[#b2b5be]">{it.icon ?? ''}</span>
                {it.text}
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {toast ? (
        <div
          data-tv-ui
          className="absolute bottom-14 left-1/2 -translate-x-1/2 rounded bg-[#2a1215] px-3 py-1.5 text-[12px] text-[#f23645] shadow-lg"
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}

/** The quantity chip: click to type a new size. */
function OrderQty({
  color,
  qty,
  onCommit,
}: {
  color: string;
  qty: number;
  onCommit: (q: number) => void;
}) {
  const [edit, setEdit] = useState<string | null>(null);
  if (edit == null)
    return (
      <Chip color={color} onClick={() => setEdit(String(qty))} title="Edit quantity">
        {qty}
      </Chip>
    );
  const done = () => {
    const v = Number(edit);
    setEdit(null);
    if (v > 0 && v !== qty) onCommit(v);
  };
  return (
    <input
      autoFocus
      value={edit}
      onChange={(e) => setEdit(e.target.value)}
      onBlur={done}
      onKeyDown={(e) => {
        if (e.key === 'Enter') done();
        if (e.key === 'Escape') setEdit(null);
      }}
      className="h-[22px] w-12 rounded-[3px] border bg-[#131722] px-1 text-center text-[12px] font-semibold text-white outline-none"
      style={{ borderColor: color }}
    />
  );
}

/** The type chip: opens TradingView's Market / Limit / Stop list. */
function OrderType({
  color,
  type,
  onPick,
}: {
  color: string;
  type: 'limit' | 'stop';
  onPick: (t: 'market' | 'limit' | 'stop') => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-tv-type-menu]')) setOpen(false);
    };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [open]);
  return (
    <span className="relative" data-tv-type-menu>
      <Chip color={color} onClick={() => setOpen((v) => !v)} title="Order type">
        {type === 'limit' ? 'Limit' : 'Stop'}
      </Chip>
      {open ? (
        <div className="absolute left-1/2 top-[28px] z-30 w-[88px] -translate-x-1/2 rounded-lg bg-[#1e222d] p-1.5 shadow-2xl">
          {(['market', 'limit', 'stop'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                setOpen(false);
                if (t !== type) onPick(t);
              }}
              className={`block w-full rounded-md px-3 py-2 text-left text-[14px] capitalize ${
                t === type ? 'bg-white text-[#131722]' : 'text-[#d1d4dc] hover:bg-[#2a2e39]'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      ) : null}
    </span>
  );
}
