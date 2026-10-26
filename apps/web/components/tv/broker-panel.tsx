'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { BrokerOrder, BrokerPosition, OrderKind, Product, Side } from '../../lib/broker/types';

const GREEN = '#089981';
const RED = '#f23645';
const RED_BG = '#3a1d22';
const input =
  'h-7 w-full rounded border border-[#363a45] bg-[#131722] px-2 text-[13px] text-[#d1d4dc] outline-none focus:border-[#2962ff] disabled:opacity-40';
const POLL_MS = 3000;

interface BrokerInfo {
  id: string;
  name: string;
  configured: boolean;
  connected: boolean;
}
interface Overview {
  live: boolean;
  maxQty: number;
  maxNotional: number;
  brokers: BrokerInfo[];
}
interface Snapshot {
  user: string;
  orders: BrokerOrder[];
  positions: BrokerPosition[];
}

const money = (n: number) => n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const tone = (n: number) => (n > 0 ? GREEN : n < 0 ? RED : '#b2b5be');
const clientId = () => `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/**
 * Real-money ticket for the broker you pick. Unlike the paper panel it keeps no account of its own: orders and
 * positions are whatever the broker reports, polled while the panel is open.
 */
export function BrokerPanel({
  selected,
  symbol,
  lastPrice,
  onClose,
}: {
  /** Broker the Trade dialog picked; overrides the remembered one. */
  selected?: string | null;
  symbol: string;
  lastPrice: number | null;
  onClose: () => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [brokerId, setBrokerId] = useState<string>('');
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [side, setSide] = useState<Side>('buy');
  const [kind, setKind] = useState<OrderKind>('limit');
  const [product, setProduct] = useState<Product>('intraday');
  const [qty, setQty] = useState('1');
  const [price, setPrice] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);

  const nse = symbol.endsWith('.NS');
  const ticker = symbol.replace(/\.NS$/, '');
  const broker = overview?.brokers.find((b) => b.id === brokerId);

  useEffect(() => {
    let alive = true;
    fetch('/api/broker', { cache: 'no-store' })
      .then(async (r) => {
        const body = (await r.json()) as Overview & { error?: string };
        if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
        if (!alive) return;
        setOverview(body);
        let saved = '';
        try {
          saved = localStorage.getItem('tv:broker') ?? '';
        } catch {
          /* storage blocked: fall back to the first broker */
        }
        const wanted = selected && body.brokers.some((b) => b.id === selected) ? selected : saved;
        setBrokerId(body.brokers.some((b) => b.id === wanted) ? wanted : (body.brokers[0]?.id ?? ''));
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (selected) setBrokerId(selected);
  }, [selected]);

  const refresh = useCallback(async () => {
    if (!brokerId || pending.current) return;
    pending.current = true;
    try {
      const r = await fetch(`/api/broker/${brokerId}/snapshot`, { cache: 'no-store' });
      const body = (await r.json()) as Snapshot & { error?: string };
      if (r.status === 401) {
        setSnap(null);
        setOverview((o) => o && { ...o, brokers: o.brokers.map((b) => (b.id === brokerId ? { ...b, connected: false } : b)) });
        return;
      }
      if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
      setSnap(body);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
    }
  }, [brokerId]);

  useEffect(() => {
    setSnap(null);
    if (!broker?.connected) return;
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [broker?.connected, brokerId, refresh]);

  const pick = (id: string) => {
    setBrokerId(id);
    try {
      localStorage.setItem('tv:broker', id);
    } catch {
      /* ignore */
    }
  };

  const needsPrice = kind === 'limit' || kind === 'stop';
  const n = Number(qty);
  const p = Number(price);
  const ref = needsPrice ? p : (lastPrice ?? 0);
  const valid = nse && Number.isInteger(n) && n > 0 && (!needsPrice || p > 0) && ref > 0;

  const place = async () => {
    if (!valid || !broker) return;
    const what = `${side.toUpperCase()} ${n} ${ticker} ${kind}${needsPrice ? ` @ ${p}` : ''} (${product}) on ${broker.name}`;
    if (!window.confirm(`LIVE ORDER with real money:\n\n${what}\n\nSend it?`)) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetch(`/api/broker/${brokerId}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clientId: clientId(),
          symbol: ticker,
          side,
          kind,
          qty: n,
          product,
          ...(kind === 'limit' ? { price: p } : {}),
          ...(kind === 'stop' ? { trigger: p } : {}),
          ...(lastPrice != null ? { refPrice: lastPrice } : {}),
        }),
      });
      const body = (await r.json()) as { orderId?: string; error?: string };
      if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
      setNotice(`Order sent: ${body.orderId}`);
      void refresh();
    } catch (e) {
      setNotice(`Refused: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (id: string) => {
    setBusy(true);
    try {
      const r = await fetch(`/api/broker/${brokerId}/orders?orderId=${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(((await r.json()) as { error?: string }).error ?? `HTTP ${r.status}`);
      setNotice(`Cancel sent for ${id}`);
      void refresh();
    } catch (e) {
      setNotice(`Cancel failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const live = overview?.live ?? false;

  return (
    <div className="relative flex w-[340px] shrink-0 flex-col overflow-y-auto border-l border-[#2a2e39] bg-[#131722] text-[12px] text-[#b2b5be]">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-[#2a2e39] px-3">
        <span className="text-[14px] font-medium text-white">Broker</span>
        <span
          className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
          style={{ background: live ? RED_BG : '#2a2e39', color: live ? RED : '#787b86' }}
          title={live ? 'Orders sent from this panel are real' : 'Server has live orders switched off'}
        >
          {live ? 'LIVE' : 'READ-ONLY'}
        </span>
        <button type="button" onClick={onClose} className="text-[#787b86] hover:text-white" aria-label="Close broker panel">
          ✕
        </button>
      </div>

      <div className="space-y-2 border-b border-[#2a2e39] px-3 py-2.5">
        <div className="flex gap-2">
          <select className={input} value={brokerId} onChange={(e) => pick(e.target.value)} aria-label="Broker">
            {overview?.brokers.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
          {broker && !broker.connected ? (
            broker.configured ? (
              <a
                href={`/api/broker/${broker.id}/login`}
                className="flex h-7 shrink-0 items-center rounded bg-[#2962ff] px-3 text-white hover:bg-[#1e53e5]"
              >
                Connect
              </a>
            ) : null
          ) : null}
        </div>
        {broker && !broker.configured ? (
          <div className="text-[#ffb74d]">
            {broker.name} API key and secret are not set on the server (see .env.example).
          </div>
        ) : null}
        {broker?.connected && snap ? <div>Connected as {snap.user}</div> : null}
        {broker?.configured && !broker.connected ? (
          <div>Not connected today. Tokens expire every morning; Connect signs in again.</div>
        ) : null}
        {!live ? (
          <div className="text-[#ffb74d]">
            Orders are off. Set BROKER_LIVE_ORDERS=true on the server and restart to send real orders. Caps:{' '}
            {overview ? `${overview.maxQty} shares, ₹${money(overview.maxNotional)} per order` : ''}.
          </div>
        ) : null}
        {error ? <div style={{ color: RED }}>{error}</div> : null}
      </div>

      {broker?.connected ? (
        <>
          <div className="space-y-2 border-b border-[#2a2e39] px-3 py-2.5">
            <div className="flex items-center justify-between text-[#d1d4dc]">
              <span className="font-medium">{nse ? ticker : symbol}</span>
              <span>{lastPrice != null ? money(lastPrice) : '—'}</span>
            </div>
            {!nse ? <div className="text-[#ffb74d]">These brokers trade NSE symbols only (…NS).</div> : null}
            <div className="grid grid-cols-2 overflow-hidden rounded-md border border-[#2a2e39]">
              {(['buy', 'sell'] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSide(s)}
                  className="py-1.5 font-semibold uppercase"
                  style={side === s ? { background: s === 'buy' ? GREEN : RED, color: '#fff' } : undefined}
                >
                  {s}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select className={input} value={kind} onChange={(e) => setKind(e.target.value as OrderKind)} aria-label="Order type">
                <option value="limit">Limit</option>
                <option value="market">Market</option>
                <option value="stop">Stop (SL-M)</option>
              </select>
              <select className={input} value={product} onChange={(e) => setProduct(e.target.value as Product)} aria-label="Product">
                <option value="intraday">Intraday</option>
                <option value="delivery">Delivery</option>
              </select>
              <input className={input} inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} aria-label="Quantity" placeholder="Qty" />
              <input
                className={input}
                inputMode="decimal"
                value={price}
                disabled={!needsPrice}
                onChange={(e) => setPrice(e.target.value)}
                aria-label={kind === 'stop' ? 'Trigger price' : 'Limit price'}
                placeholder={kind === 'stop' ? 'Trigger' : kind === 'limit' ? 'Price' : 'Market'}
              />
            </div>
            <button
              type="button"
              disabled={!valid || !live || busy}
              onClick={() => void place()}
              className="h-8 w-full rounded font-semibold text-white disabled:opacity-40"
              style={{ background: side === 'buy' ? GREEN : RED }}
            >
              {valid ? `${side === 'buy' ? 'Buy' : 'Sell'} ${n} ${ticker}${ref ? ` · ≈ ₹${money(ref * n)}` : ''}` : 'Fill in the order'}
            </button>
            {notice ? <div className="text-[#d1d4dc]">{notice}</div> : null}
          </div>

          <div className="border-b border-[#2a2e39] px-3 py-2">
            <div className="mb-1 font-medium text-[#787b86]">Positions</div>
            {snap?.positions.length ? (
              snap.positions.map((x) => (
                <div key={`${x.symbol}-${x.product}`} className="flex justify-between py-0.5">
                  <span className="text-[#d1d4dc]">
                    {x.symbol} <span className="text-[#787b86]">{x.product === 'delivery' ? 'CNC' : 'MIS'}</span>
                  </span>
                  <span>
                    {x.qty} @ {money(x.avg)} <span style={{ color: tone(x.pnl) }}>{money(x.pnl)}</span>
                  </span>
                </div>
              ))
            ) : (
              <div className="text-[#787b86]">None open</div>
            )}
          </div>

          <div className="px-3 py-2">
            <div className="mb-1 font-medium text-[#787b86]">Orders today</div>
            {snap?.orders.length ? (
              snap.orders.map((o) => (
                <div key={o.id} className="border-b border-[#1e222d] py-1">
                  <div className="flex justify-between text-[#d1d4dc]">
                    <span style={{ color: o.side === 'buy' ? GREEN : RED }}>
                      {o.side.toUpperCase()} {o.qty} {o.symbol}
                    </span>
                    <span>{o.kind === 'market' ? 'MKT' : money(o.price)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span style={{ color: o.status === 'rejected' ? RED : undefined }}>
                      {o.rawStatus}
                      {o.filledQty ? ` · ${o.filledQty}/${o.qty}` : ''}
                    </span>
                    {o.status === 'open' ? (
                      <button type="button" disabled={busy} onClick={() => void cancel(o.id)} className="text-[#2962ff] hover:underline disabled:opacity-40">
                        Cancel
                      </button>
                    ) : null}
                  </div>
                  {o.message ? <div className="text-[11px] text-[#787b86]">{o.message}</div> : null}
                </div>
              ))
            ) : (
              <div className="text-[#787b86]">No orders</div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
