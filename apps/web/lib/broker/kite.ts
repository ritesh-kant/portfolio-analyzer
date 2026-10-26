import { createHash } from 'node:crypto';
import {
  BrokerError,
  type BrokerDefinition,
  type BrokerOrder,
  type BrokerPosition,
  type OrderKind,
  type OrderStatus,
  type PlaceOrder,
  type Product,
  type TradingBroker,
} from './types';

const API = 'https://api.kite.trade';

const ORDER_TYPE: Record<OrderKind, string> = { market: 'MARKET', limit: 'LIMIT', stop: 'SL-M' };
const KIND: Record<string, OrderKind> = { MARKET: 'market', LIMIT: 'limit', 'SL-M': 'stop', SL: 'stop' };
const PRODUCT: Record<Product, string> = { intraday: 'MIS', delivery: 'CNC' };

interface KiteEnvelope<T> {
  status?: string;
  data?: T;
  message?: string;
}

interface RawOrder {
  order_id: string;
  exchange: string;
  tradingsymbol: string;
  transaction_type: string;
  order_type: string;
  product: string;
  quantity: number;
  filled_quantity: number;
  price: number;
  average_price: number;
  status: string;
  status_message?: string | null;
  order_timestamp?: string;
}

interface RawPosition {
  exchange: string;
  tradingsymbol: string;
  product: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
}

function status(raw: string): OrderStatus {
  const s = raw.toUpperCase();
  if (s === 'COMPLETE') return 'complete';
  if (s === 'REJECTED') return 'rejected';
  if (s === 'CANCELLED') return 'cancelled';
  return 'open';
}

class Kite implements TradingBroker {
  constructor(private readonly token: string) {}

  private async call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API}${path}`, {
      ...init,
      cache: 'no-store',
      headers: {
        'X-Kite-Version': '3',
        Authorization: `token ${process.env.KITE_API_KEY ?? ''}:${this.token}`,
        ...(init.body ? { 'Content-Type': 'application/x-www-form-urlencoded' } : {}),
      },
    });
    const body = (await res.json().catch(() => ({}))) as KiteEnvelope<T>;
    if (!res.ok || body.status === 'error') {
      // 403 is Kite's expired / invalid token
      throw new BrokerError(body.message ?? `Kite HTTP ${res.status}`, res.status === 403 ? 401 : 502);
    }
    return body.data as T;
  }

  async profile() {
    const d = await this.call<{ user_name?: string; user_id?: string }>('/user/profile');
    return { user: d.user_name ?? d.user_id ?? 'Zerodha' };
  }

  async orders(): Promise<BrokerOrder[]> {
    const rows = (await this.call<RawOrder[]>('/orders')) ?? [];
    return rows
      .filter((r) => r.exchange === 'NSE')
      .map((r) => ({
        id: r.order_id,
        symbol: r.tradingsymbol,
        side: r.transaction_type === 'SELL' ? 'sell' : 'buy',
        kind: KIND[r.order_type] ?? 'limit',
        product: r.product === 'CNC' ? 'delivery' : 'intraday',
        qty: r.quantity,
        filledQty: r.filled_quantity,
        price: r.price,
        avgPrice: r.average_price,
        status: status(r.status),
        rawStatus: r.status,
        message: r.status_message ?? undefined,
        time: r.order_timestamp ? Math.floor(Date.parse(`${r.order_timestamp.replace(' ', 'T')}+05:30`) / 1000) : 0,
      }));
  }

  async positions(): Promise<BrokerPosition[]> {
    const d = await this.call<{ net?: RawPosition[] }>('/portfolio/positions');
    return (d.net ?? [])
      .filter((r) => r.exchange === 'NSE' && r.quantity !== 0)
      .map((r) => ({
        symbol: r.tradingsymbol,
        product: r.product === 'CNC' ? 'delivery' : 'intraday',
        qty: r.quantity,
        avg: r.average_price,
        ltp: r.last_price,
        pnl: r.pnl,
      }));
  }

  async place(o: PlaceOrder) {
    const form = new URLSearchParams({
      exchange: 'NSE',
      tradingsymbol: o.symbol,
      transaction_type: o.side === 'buy' ? 'BUY' : 'SELL',
      order_type: ORDER_TYPE[o.kind],
      quantity: String(o.qty),
      product: PRODUCT[o.product],
      validity: 'DAY',
      // Kite tags are alphanumeric, at most 20 characters
      tag: o.clientId.replace(/[^A-Za-z0-9]/g, '').slice(0, 20),
    });
    if (o.kind === 'limit') form.set('price', String(o.price));
    if (o.kind === 'stop') form.set('trigger_price', String(o.trigger));
    const d = await this.call<{ order_id: string }>('/orders/regular', { method: 'POST', body: form });
    return { orderId: d.order_id };
  }

  async cancel(orderId: string) {
    await this.call(`/orders/regular/${encodeURIComponent(orderId)}`, { method: 'DELETE' });
  }
}

export const kite: BrokerDefinition = {
  id: 'kite',
  name: 'Zerodha Kite',
  configured: () => Boolean(process.env.KITE_API_KEY && process.env.KITE_API_SECRET),
  // Kite's redirect URL is fixed in the developer console; redirect_params carries our CSRF state back to it.
  loginUrl: (state) =>
    `https://kite.zerodha.com/connect/login?${new URLSearchParams({
      v: '3',
      api_key: process.env.KITE_API_KEY ?? '',
      redirect_params: `state=${state}`,
    })}`,
  async exchange(query) {
    const requestToken = query.get('request_token');
    if (!requestToken || query.get('status') !== 'success') throw new BrokerError('Kite login was not completed', 400);
    const key = process.env.KITE_API_KEY ?? '';
    const checksum = createHash('sha256')
      .update(key + requestToken + (process.env.KITE_API_SECRET ?? ''))
      .digest('hex');
    const res = await fetch(`${API}/session/token`, {
      method: 'POST',
      headers: { 'X-Kite-Version': '3', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ api_key: key, request_token: requestToken, checksum }),
    });
    const body = (await res.json().catch(() => ({}))) as KiteEnvelope<{ access_token?: string }>;
    if (!res.ok || !body.data?.access_token) {
      throw new BrokerError(body.message ?? `Kite token exchange HTTP ${res.status}`, 502);
    }
    return body.data.access_token;
  },
  connect: (token) => new Kite(token),
};
