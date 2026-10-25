'use client';

import { useEffect, useState } from 'react';
import {
  equityOf,
  realizedOf,
  tradeStats,
  unrealized,
  MAX_LEVERAGE,
  type OrderType,
  type Tif,
  type PaperState,
  type Side,
} from '../../lib/tv/paper';

const GREEN = '#089981';
const RED = '#f23645';
const BLUE = '#2962ff';

const money = (n: number, d = 2) =>
  n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n: number) => `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}`;
const tone = (n: number) => (n > 0 ? GREEN : n < 0 ? RED : '#b2b5be');

const input =
  'h-7 w-full rounded border border-[#363a45] bg-[#131722] px-2 text-[13px] text-[#d1d4dc] outline-none focus:border-[#2962ff]';

export interface TradingPrefs {
  showButtons: boolean;
  instant: boolean;
  showPositions: boolean;
  showOrders: boolean;
}

export const DEFAULT_PREFS: TradingPrefs = {
  showButtons: true,
  instant: false,
  showPositions: true,
  showOrders: true,
};

export interface OrderRequest {
  side: Side;
  type: OrderType;
  qty: number;
  price?: number;
  sl?: number;
  tp?: number;
  tif?: Tif;
}

export interface ModifyPatch {
  type?: 'limit' | 'stop';
  qty?: number;
  price?: number;
  tif?: Tif;
  sl?: number | null;
  tp?: number | null;
}

/** TradingView's chart-top ticket: SELL price │ quantity │ BUY price, plus a button for the full order dialog. */
export function PaperTicket({
  price,
  decimals,
  shifted,
  qty,
  onQty,
  oneClick,
  onSide,
  onDialog,
}: {
  price: number | null;
  decimals: number;
  shifted: boolean;
  qty: string;
  onQty: (v: string) => void;
  oneClick: boolean;
  onSide: (side: Side) => void;
  onDialog: () => void;
}) {
  const px = price != null ? price.toFixed(decimals) : '—';
  const side = (s: Side, bg: string, text: string) => (
    <button
      type="button"
      onClick={() => onSide(s)}
      title={oneClick ? `${text} at market` : `Open the ${text.toLowerCase()} order dialog`}
      className="flex min-w-[78px] flex-col items-center px-3 py-1 text-white hover:brightness-110"
      style={{ background: bg }}
    >
      <span className="text-[10px] font-semibold uppercase leading-3">{text}</span>
      <span className="text-[13px] font-semibold tabular-nums">{px}</span>
    </button>
  );
  return (
    <div
      className={`absolute left-1/2 z-20 -translate-x-1/2 ${shifted ? 'top-12' : 'top-2'}`}
      data-tv-ui
    >
      <div className="flex items-stretch overflow-hidden rounded-md border border-[#363a45] bg-[#1e222d] shadow-lg">
        {side('sell', RED, 'Sell')}
        <input
          aria-label="Quantity"
          value={qty}
          onChange={(e) => onQty(e.target.value)}
          inputMode="decimal"
          className="w-14 bg-transparent px-1 text-center text-[13px] tabular-nums text-[#d1d4dc] outline-none"
        />
        {side('buy', BLUE, 'Buy')}
        <button
          type="button"
          title="Order dialog"
          onClick={onDialog}
          className="px-2 text-[14px] text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
        >
          ≡
        </button>
      </div>
    </div>
  );
}

export interface DialogInit {
  side: Side;
  type: OrderType;
  price?: number;
  /** Modify an existing working order instead of placing a new one. */
  edit?: { id: number; qty: number; tp?: number; sl?: number; tif: Tif };
}

type Field = 'ticks' | 'price' | 'money' | 'pct';

/**
 * TradingView's order panel: split SELL / BUY header, Market / Limit / Stop tabs, price with its distance from the
 * market in ticks, units with $ / % risk, take profit and stop loss (ticks, price, $, %), time in force, and a
 * submit button that reads like the order ("BUY 5 RELIANCE @ 1189.66 LMT"). Opened on a working order it modifies it.
 */
export function PaperOrderDialog({
  init,
  symbol,
  last,
  decimals,
  currency,
  equity,
  qty: qty0,
  oneClick,
  onOneClick,
  onSubmit,
  onModify,
  onClose,
}: {
  init: DialogInit;
  symbol: string;
  last: number;
  decimals: number;
  currency: string;
  equity: number;
  qty: string;
  oneClick: boolean;
  onOneClick: (v: boolean) => void;
  onSubmit: (o: OrderRequest, qty: string) => string | null;
  onModify: (id: number, patch: ModifyPatch) => string | null;
  onClose: () => void;
}) {
  const edit = init.edit;
  const fix = (n: number) => n.toFixed(decimals);
  const [side, setSide] = useState<Side>(init.side);
  const [type, setType] = useState<OrderType>(init.type);
  const [price, setPrice] = useState(init.price != null ? fix(init.price) : '');
  const [qty, setQty] = useState(edit ? String(edit.qty) : qty0);
  const [tpOn, setTpOn] = useState(edit?.tp != null);
  const [slOn, setSlOn] = useState(edit?.sl != null);
  const [tp, setTp] = useState(edit?.tp != null ? fix(edit.tp) : '');
  const [sl, setSl] = useState(edit?.sl != null ? fix(edit.sl) : '');
  const [tif, setTif] = useState<Tif>(edit?.tif ?? 'week');
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ key: string; text: string } | null>(null);

  const tick = 10 ** -decimals;
  const q = Number(qty) > 0 ? Number(qty) : 0;
  const entry = type === 'market' ? last : Number(price) > 0 ? Number(price) : last;
  const dir = side === 'buy' ? 1 : -1;
  const color = side === 'buy' ? BLUE : RED;
  const shortName = symbol.replace(/\.(NS|BO)$|=[FX]$/, '');

  const toggle = (kind: 'tp' | 'sl', on: boolean) => {
    const v = fix(entry * (1 + (kind === 'tp' ? dir : -dir) * 0.01));
    if (kind === 'tp') {
      setTpOn(on);
      if (on && !tp) setTp(v);
    } else {
      setSlOn(on);
      if (on && !sl) setSl(v);
    }
  };

  const derived = (v: string): Record<Field, string> => {
    const n = Number(v);
    if (v.trim() === '' || !Number.isFinite(n)) return { ticks: '', price: v, money: '', pct: '' };
    const dist = Math.abs(n - entry);
    return {
      ticks: String(Math.round(dist / tick)),
      price: v,
      money: money(q * dist),
      pct: ((dist / entry) * 100).toFixed(2),
    };
  };
  const apply = (kind: 'tp' | 'sl', field: Field, text: string) => {
    const set = kind === 'tp' ? setTp : setSl;
    setDraft({ key: `${kind}${field}`, text });
    const n = Number(text.replace(/,/g, ''));
    if (text.trim() === '' || !Number.isFinite(n)) return;
    const sign = kind === 'tp' ? dir : -dir;
    let dist: number;
    if (field === 'price') return set(text);
    if (field === 'ticks') dist = n * tick;
    else if (field === 'money') dist = q ? n / q : 0;
    else dist = (entry * n) / 100;
    set(fix(entry + sign * dist));
  };
  const cellInput = (kind: 'tp' | 'sl', field: Field, value: string) => {
    const key = `${kind}${field}`;
    return (
      <input
        className={input}
        value={draft?.key === key ? draft.text : value}
        onChange={(e) => apply(kind, field, e.target.value)}
        onBlur={() => setDraft(null)}
      />
    );
  };

  const slNum = slOn && Number(sl) > 0 ? Number(sl) : undefined;
  const tpNum = tpOn && Number(tp) > 0 ? Number(tp) : undefined;
  const riskAmt = slNum != null ? q * Math.abs(entry - slNum) : null;
  const rewardAmt = tpNum != null ? q * Math.abs(tpNum - entry) : null;

  const priceTicks =
    type === 'market' || !(Number(price) > 0)
      ? ''
      : String(Math.round((Number(price) - last) / tick));

  const submit = () => {
    if (edit) {
      const e = onModify(edit.id, {
        type: type === 'market' ? undefined : type,
        qty: q,
        price: Number(price),
        tif,
        sl: slOn ? (slNum ?? null) : null,
        tp: tpOn ? (tpNum ?? null) : null,
      });
      setErr(e);
      if (!e) onClose();
      return;
    }
    const e = onSubmit(
      {
        side,
        type,
        qty: q,
        price: type === 'market' ? undefined : Number(price),
        sl: slNum,
        tp: tpNum,
        tif: type === 'market' ? undefined : tif,
      },
      qty,
    );
    setErr(e);
    if (!e) onClose();
  };

  const group = (kind: 'tp' | 'sl', on: boolean, value: string) => {
    const d = derived(value);
    return (
      <div className="border-t border-[#2a2e39] pt-2.5">
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-[#d1d4dc]">
          <input
            type="checkbox"
            checked={on}
            onChange={(e) => toggle(kind, e.target.checked)}
            className="accent-[#2962ff]"
          />
          {kind === 'tp' ? 'Take Profit' : 'Stop Loss'}
        </label>
        {on ? (
          <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px] text-[#787b86]">
            <label className="flex flex-col gap-1">
              Ticks
              {cellInput(kind, 'ticks', d.ticks)}
            </label>
            <label className="flex flex-col gap-1">
              Price
              {cellInput(kind, 'price', d.price)}
            </label>
            <label className="flex flex-col gap-1">
              {currency || '$'}
              {cellInput(kind, 'money', d.money)}
            </label>
            <label className="flex flex-col gap-1">%{cellInput(kind, 'pct', d.pct)}</label>
          </div>
        ) : null}
      </div>
    );
  };

  const sideBtn = (s: Side) => (
    <button
      type="button"
      disabled={!!edit}
      onClick={() => setSide(s)}
      className={`flex flex-1 flex-col items-center py-1.5 text-white transition-colors disabled:cursor-default ${
        side === s ? '' : 'opacity-40 hover:opacity-70'
      }`}
      style={{ background: s === 'buy' ? BLUE : RED }}
    >
      <span className="text-[10px] font-semibold uppercase leading-3">
        {s === 'buy' ? 'Buy' : 'Sell'}
      </span>
      <span className="text-[14px] font-semibold tabular-nums">{fix(last)}</span>
    </button>
  );

  return (
    <div
      data-tv-ui
      className="absolute right-[76px] top-2 z-30 max-h-[calc(100%-16px)] w-[300px] overflow-y-auto rounded-lg border border-[#363a45] bg-[#1e222d] p-3 shadow-2xl"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[14px] font-semibold text-white">
          {edit ? `Modify order · ${shortName}` : shortName}
        </span>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="text-[16px] text-[#b2b5be] hover:text-white"
        >
          ×
        </button>
      </div>
      <div className="mb-2.5 flex overflow-hidden rounded">
        {sideBtn('sell')}
        {sideBtn('buy')}
      </div>
      <div className="mb-3 flex gap-1 border-b border-[#2a2e39]">
        {(['market', 'limit', 'stop'] as const).map((t) => (
          <button
            key={t}
            type="button"
            disabled={!!edit && t === 'market'}
            onClick={() => {
              setType(t);
              if (t !== 'market' && !price) setPrice(fix(last));
            }}
            className={`px-2.5 py-1.5 text-[13px] font-medium capitalize disabled:opacity-30 ${
              type === t
                ? 'border-b-2 border-[#2962ff] text-white'
                : 'text-[#b2b5be] hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="mb-2.5 grid grid-cols-2 gap-2 text-[11px] text-[#787b86]">
        <label className="flex flex-col gap-1">
          Price
          <input
            className={input}
            disabled={type === 'market'}
            value={type === 'market' ? fix(last) : price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          Ticks from last
          <input
            className={input}
            disabled={type === 'market'}
            value={type === 'market' ? '0' : priceTicks}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) setPrice(fix(last + n * tick));
            }}
          />
        </label>
        <label className="flex flex-col gap-1">
          Units
          <input className={input} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <div className="flex flex-col gap-1">
            {currency || '$'} Risk
            <span className={`${input} flex items-center text-[#d1d4dc]`}>
              {riskAmt != null ? money(riskAmt) : '—'}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            % Risk
            <span className={`${input} flex items-center text-[#d1d4dc]`}>
              {riskAmt != null && equity > 0 ? ((riskAmt / equity) * 100).toFixed(2) : '—'}
            </span>
          </div>
        </div>
      </div>
      <div className="space-y-2.5">
        {group('tp', tpOn, tp)}
        {group('sl', slOn, sl)}
      </div>
      {riskAmt != null && rewardAmt != null && riskAmt > 0 ? (
        <div className="mt-2 text-[12px] text-[#b2b5be]">
          Reward : risk{' '}
          <span className="tabular-nums text-[#d1d4dc]">
            1 : {(rewardAmt / riskAmt).toFixed(2)}
          </span>
        </div>
      ) : null}
      {type !== 'market' ? (
        <label className="mt-3 flex flex-col gap-1 text-[11px] text-[#787b86]">
          Time in Force
          <select className={input} value={tif} onChange={(e) => setTif(e.target.value as Tif)}>
            <option value="day">Day</option>
            <option value="week">Week</option>
            <option value="gtc">Good till cancelled</option>
          </select>
        </label>
      ) : null}
      <div className="mt-3 flex justify-between border-t border-[#2a2e39] pt-2.5 text-[12px] text-[#b2b5be]">
        <span>Order value</span>
        <span className="tabular-nums text-[#d1d4dc]">
          {money(q * entry)} {currency}
        </span>
      </div>
      <div className="flex justify-between pb-2.5 text-[12px] text-[#b2b5be]">
        <span>Margin ({MAX_LEVERAGE}×)</span>
        <span className="tabular-nums text-[#d1d4dc]">
          {money((q * entry) / MAX_LEVERAGE)} {currency}
        </span>
      </div>
      {err ? (
        <div className="mb-2 rounded bg-[#2a1215] px-2 py-1 text-[12px] text-[#f23645]">{err}</div>
      ) : null}
      <button
        type="button"
        onClick={submit}
        className="h-9 w-full rounded text-[13px] font-bold uppercase text-white hover:brightness-110"
        style={{ background: color }}
      >
        {edit
          ? 'Modify order'
          : `${side} ${q || ''} ${shortName} @ ${
              type === 'market' ? 'MKT' : `${price || '—'} ${type === 'limit' ? 'LMT' : 'STP'}`
            }`}
      </button>
      {edit ? null : (
        <label className="mt-2.5 flex cursor-pointer items-center gap-2 text-[12px] text-[#b2b5be]">
          <input
            type="checkbox"
            checked={oneClick}
            onChange={(e) => onOneClick(e.target.checked)}
            className="accent-[#2962ff]"
          />
          Instant orders placement
        </label>
      )}
    </div>
  );
}

type Tab = 'positions' | 'orders' | 'history' | 'account';

const th = 'px-3 py-1.5 text-left text-[11px] font-medium uppercase tracking-wide text-[#787b86]';
const td = 'px-3 py-1.5 text-[13px] tabular-nums whitespace-nowrap';

export function PaperPanel({
  state,
  symbol,
  replay,
  prices,
  decimals,
  currency,
  tz,
  onClose,
  onClosePos,
  onCancel,
  onBracket,
  onSettings,
  onReset,
  prefs,
  onPrefs,
}: {
  state: PaperState;
  symbol: string;
  replay: boolean;
  prices: Record<string, number>;
  decimals: number;
  currency: string;
  tz: string;
  onClose: () => void;
  onClosePos: (symbol: string) => void;
  onCancel: (id: number) => void;
  onBracket: (symbol: string, sl?: number, tp?: number) => string | null;
  onSettings: (initial: number, commissionPct: number) => void;
  onReset: () => void;
  prefs: TradingPrefs;
  onPrefs: (p: TradingPrefs) => void;
}) {
  const [tab, setTab] = useState<Tab>('positions');
  const [filter, setFilter] = useState<'all' | 'working' | 'filled' | 'cancelled' | 'rejected'>(
    'all',
  );
  // The header row scrolls sideways, which would clip a popover inside it, so the popover is fixed to the viewport.
  const [settingsAt, setSettingsAt] = useState<{ right: number; bottom: number } | null>(null);
  useEffect(() => {
    if (!settingsAt) return;
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-tv-settings]')) setSettingsAt(null);
    };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [settingsAt]);
  const [err, setErr] = useState<string | null>(null);
  const [initial, setInitial] = useState(String(state.initial));
  const [comm, setComm] = useState(String(state.commissionPct));
  const positions = Object.values(state.positions);
  const equity = equityOf(state);
  const open = positions.reduce(
    (a, p) => a + unrealized(p, prices[p.symbol] ?? state.prices[p.symbol] ?? p.avg),
    0,
  );
  const stats = tradeStats(state.history);
  const rows = [
    ...state.orders.map((o) => ({
      id: o.id,
      symbol: o.symbol,
      side: o.side,
      type: o.type as string,
      qty: o.qty,
      price: o.price,
      tp: o.tp,
      sl: o.sl,
      status: 'working' as const,
      time: 0,
    })),
    ...(state.log ?? []).map((l) => ({
      id: l.id,
      symbol: l.symbol,
      side: l.side,
      type: l.type as string,
      qty: l.qty,
      price: l.price,
      tp: undefined as number | undefined,
      sl: undefined as number | undefined,
      status: l.status as string,
      time: l.time,
    })),
  ].filter((r) => filter === 'all' || r.status === filter);
  const fmtTime = (t: number) => {
    try {
      return new Intl.DateTimeFormat('en-GB', {
        timeZone: tz,
        dateStyle: 'short',
        timeStyle: 'short',
        hour12: false,
      }).format(new Date(t * 1000));
    } catch {
      return String(t);
    }
  };
  const tabBtn = (t: Tab, label: string, n?: number) => (
    <button
      type="button"
      onClick={() => setTab(t)}
      className={`px-3 py-2 text-[13px] font-medium ${
        tab === t ? 'border-b-2 border-[#2962ff] text-white' : 'text-[#b2b5be] hover:text-white'
      }`}
    >
      {label}
      {n ? <span className="ml-1 text-[#787b86]">{n}</span> : null}
    </button>
  );
  const cell = (label: string, value: string, color?: string) => (
    <div className="flex flex-col px-3">
      <span className="text-[10px] uppercase tracking-wide text-[#787b86]">{label}</span>
      <span
        className="text-[13px] font-semibold tabular-nums"
        style={{ color: color ?? '#d1d4dc' }}
      >
        {value}
      </span>
    </div>
  );

  return (
    <div
      className="flex h-[240px] shrink-0 flex-col border-t border-[#2a2e39] bg-[#131722]"
      data-tv-ui
    >
      <div className="scrollbar-none flex shrink-0 items-center overflow-x-auto border-b border-[#2a2e39] [&>*]:shrink-0">
        {tabBtn('positions', 'Positions', positions.length)}
        {tabBtn('orders', 'Orders', state.orders.length)}
        {tabBtn('history', 'History', state.history.length)}
        {tabBtn('account', 'Account')}
        <div className="flex-1" />
        <span className="mr-2 rounded bg-[#2a2e39] px-2 py-0.5 text-[11px] font-semibold uppercase text-[#b2b5be]">
          {replay ? 'Replay account' : 'Paper account'}
        </span>
        {cell('Account balance', `${money(state.cash)} ${currency}`)}
        {cell('Equity', `${money(equity)} ${currency}`)}
        {cell('Open P&L', signed(open), tone(open))}
        {cell('Realized', signed(realizedOf(state)), tone(realizedOf(state)))}
        <div data-tv-settings>
          <button
            type="button"
            aria-label="Trading settings"
            title="Trading settings"
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              setSettingsAt((v) =>
                v
                  ? null
                  : { right: window.innerWidth - r.right, bottom: window.innerHeight - r.top + 6 },
              );
            }}
            className="px-2 text-[15px] text-[#b2b5be] hover:text-white"
          >
            ⚙
          </button>
          {settingsAt ? (
            <div
              className="fixed z-[60] w-[300px] rounded-lg border border-[#363a45] bg-[#1e222d] p-3 shadow-2xl"
              style={{ right: settingsAt.right, bottom: settingsAt.bottom }}
            >
              <div className="mb-2 text-[13px] font-semibold text-white">Trading</div>
              {(
                [
                  ['showButtons', 'Buy/sell buttons'],
                  ['instant', 'Instant orders placement'],
                  ['showPositions', 'Positions'],
                  ['showOrders', 'Orders'],
                ] as const
              ).map(([k, label]) => (
                <label
                  key={k}
                  className="flex cursor-pointer items-center gap-2 py-1 text-[13px] text-[#d1d4dc]"
                >
                  <input
                    type="checkbox"
                    className="accent-[#2962ff]"
                    checked={prefs[k]}
                    onChange={(e) => onPrefs({ ...prefs, [k]: e.target.checked })}
                  />
                  {label}
                </label>
              ))}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          aria-label="Close panel"
          onClick={onClose}
          className="px-3 text-[16px] text-[#b2b5be] hover:text-white"
        >
          ×
        </button>
      </div>
      {err ? <div className="bg-[#2a1215] px-3 py-1 text-[12px] text-[#f23645]">{err}</div> : null}
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === 'positions' ? (
          positions.length ? (
            <table className="w-full">
              <thead>
                <tr>
                  {[
                    'Symbol',
                    'Side',
                    'Qty',
                    'Avg fill price',
                    'Take profit',
                    'Stop loss',
                    'Last price',
                    'Profit',
                    '',
                  ].map((h) => (
                    <th key={h} className={th}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const last = prices[p.symbol] ?? state.prices[p.symbol] ?? p.avg;
                  const pnl = unrealized(p, last);
                  return (
                    <tr key={p.symbol} className="border-t border-[#1e222d]">
                      <td className={td}>{p.symbol.replace(/\.(NS|BO)$|=[FX]$/, '')}</td>
                      <td className={td} style={{ color: p.qty > 0 ? GREEN : RED }}>
                        {p.qty > 0 ? 'Long' : 'Short'}
                      </td>
                      <td className={td}>{Math.abs(p.qty)}</td>
                      <td className={td}>{p.avg.toFixed(decimals)}</td>
                      {(['tp', 'sl'] as const).map((k) => (
                        <td key={k} className={td}>
                          <LevelInput
                            key={`${p.symbol}${k}${p[k] ?? ''}`}
                            value={p[k]}
                            decimals={decimals}
                            onCommit={(v) =>
                              setErr(
                                onBracket(p.symbol, k === 'sl' ? v : p.sl, k === 'tp' ? v : p.tp),
                              )
                            }
                          />
                        </td>
                      ))}
                      <td className={td}>{last.toFixed(decimals)}</td>
                      <td className={td} style={{ color: tone(pnl) }}>
                        {signed(pnl)}
                      </td>
                      <td className={td}>
                        <button
                          type="button"
                          onClick={() => onClosePos(p.symbol)}
                          className="rounded border border-[#363a45] px-2 py-0.5 text-[12px] text-[#d1d4dc] hover:bg-[#2a2e39]"
                        >
                          Close
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <Empty>No open positions. Use Buy / Sell at the top of the chart.</Empty>
          )
        ) : null}
        {tab === 'orders' ? (
          <>
            <div className="flex gap-1 border-b border-[#1e222d] px-3 py-1.5">
              {(['all', 'working', 'filled', 'cancelled', 'rejected'] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={`rounded-full px-2.5 py-0.5 text-[12px] font-medium capitalize ${
                    filter === f
                      ? 'bg-[#2962ff] text-white'
                      : 'bg-[#2a2e39] text-[#b2b5be] hover:text-white'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
            {rows.length ? (
              <table className="w-full">
                <thead>
                  <tr>
                    {[
                      'Symbol',
                      'Side',
                      'Type',
                      'Qty',
                      'Price',
                      'Take profit',
                      'Stop loss',
                      'Status',
                      'Time',
                      '',
                    ].map((h) => (
                      <th key={h} className={th}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.status}${r.id}`} className="border-t border-[#1e222d]">
                      <td className={td}>{r.symbol.replace(/\.(NS|BO)$|=[FX]$/, '')}</td>
                      <td className={td} style={{ color: r.side === 'buy' ? GREEN : RED }}>
                        {r.side === 'buy' ? 'Buy' : 'Sell'}
                      </td>
                      <td className={`${td} capitalize`}>{r.type}</td>
                      <td className={td}>{r.qty}</td>
                      <td className={td}>{r.price.toFixed(decimals)}</td>
                      <td className={td}>{r.tp?.toFixed(decimals) ?? '—'}</td>
                      <td className={td}>{r.sl?.toFixed(decimals) ?? '—'}</td>
                      <td
                        className={`${td} capitalize`}
                        style={{
                          color:
                            r.status === 'filled'
                              ? GREEN
                              : r.status === 'rejected'
                                ? RED
                                : r.status === 'working'
                                  ? '#2962ff'
                                  : '#b2b5be',
                        }}
                      >
                        {r.status}
                      </td>
                      <td className={td}>{r.time ? fmtTime(r.time) : '—'}</td>
                      <td className={td}>
                        {r.status === 'working' ? (
                          <button
                            type="button"
                            onClick={() => onCancel(r.id)}
                            className="rounded border border-[#363a45] px-2 py-0.5 text-[12px] text-[#d1d4dc] hover:bg-[#2a2e39]"
                          >
                            Cancel
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No {filter === 'all' ? '' : `${filter} `}orders.</Empty>
            )}
          </>
        ) : null}
        {tab === 'history' ? (
          state.history.length ? (
            <>
              <div className="flex gap-4 border-b border-[#1e222d] px-3 py-1.5 text-[12px] text-[#b2b5be]">
                <span>{stats.count} trades</span>
                <span>Win rate {stats.winRate.toFixed(0)}%</span>
                <span>Avg win {signed(stats.avgWin)}</span>
                <span>Avg loss {signed(stats.avgLoss)}</span>
              </div>
              <table className="w-full">
                <thead>
                  <tr>
                    {[
                      'Closed',
                      'Symbol',
                      'Side',
                      'Qty',
                      'Entry',
                      'Exit',
                      'Net P&L',
                      'Fees',
                      'Reason',
                    ].map((h) => (
                      <th key={h} className={th}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {state.history.map((t) => (
                    <tr key={t.id} className="border-t border-[#1e222d]">
                      <td className={td}>{fmtTime(t.closedAt)}</td>
                      <td className={td}>{t.symbol.replace(/\.(NS|BO)$|=[FX]$/, '')}</td>
                      <td className={td} style={{ color: t.dir === 'long' ? GREEN : RED }}>
                        {t.dir === 'long' ? 'Long' : 'Short'}
                      </td>
                      <td className={td}>{t.qty}</td>
                      <td className={td}>{t.entry.toFixed(decimals)}</td>
                      <td className={td}>{t.exit.toFixed(decimals)}</td>
                      <td className={td} style={{ color: tone(t.pnl) }}>
                        {signed(t.pnl)}
                      </td>
                      <td className={td}>{money(t.fees)}</td>
                      <td className={td}>
                        {
                          {
                            manual: 'Manual',
                            sl: 'Stop loss',
                            tp: 'Take profit',
                            flip: 'Reversed',
                          }[t.reason]
                        }
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <Empty>No closed trades yet.</Empty>
          )
        ) : null}
        {tab === 'account' ? (
          <div className="flex flex-wrap items-end gap-4 p-3 text-[12px] text-[#b2b5be]">
            <label className="flex flex-col gap-1">
              Starting capital ({currency})
              <input
                className={`${input} w-36`}
                value={initial}
                onChange={(e) => setInitial(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              Commission (% per fill)
              <input
                className={`${input} w-28`}
                value={comm}
                onChange={(e) => setComm(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="h-7 rounded bg-[#2962ff] px-3 text-[13px] font-medium text-white hover:brightness-110"
              onClick={() => {
                const i = Number(initial);
                const c = Number(comm);
                if (i > 0 && c >= 0) onSettings(i, c);
                else setErr('Capital must be positive and commission zero or more');
              }}
            >
              Apply
            </button>
            <button
              type="button"
              className="h-7 rounded border border-[#363a45] px-3 text-[13px] text-[#f23645] hover:bg-[#2a2e39]"
              onClick={() => {
                if (
                  window.confirm('Reset the account? All positions, orders and history are erased.')
                ) {
                  onReset();
                  setInitial(String(state.initial));
                }
              }}
            >
              Reset account
            </button>
            <div className="basis-full text-[11px] leading-4 text-[#787b86]">
              Fills use the chart&apos;s own candles (market orders at the last price; limit / stop
              when a candle reaches them; stop wins if one candle touches both stop and target). No
              spread, slippage or margin interest is simulated. In Replay the account is separate
              and starts fresh each time.
              {` Symbol on chart: ${symbol}.`}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-6 text-center text-[13px] text-[#787b86]">{children}</div>;
}

/** A level that commits on blur / Enter; empty clears it. */
function LevelInput({
  value,
  decimals,
  onCommit,
}: {
  value: number | undefined;
  decimals: number;
  onCommit: (v: number | undefined) => void;
}) {
  const [text, setText] = useState(value != null ? value.toFixed(decimals) : '');
  const commit = () => {
    const v = text.trim() === '' ? undefined : Number(text);
    if (v !== undefined && !Number.isFinite(v)) return;
    if (v !== value) onCommit(v);
  };
  return (
    <input
      className={`${input} w-24`}
      value={text}
      placeholder="—"
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
    />
  );
}
