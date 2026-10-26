import { instrumentKey } from './upstox-instruments';
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

const API = 'https://api.upstox.com/v2';
const redirectUri = () =>
  process.env.UPSTOX_CHART_REDIRECT_URI ?? 'http://localhost:3000/api/broker/upstox/callback';

const ORDER_TYPE: Record<OrderKind, string> = { market: 'MARKET', limit: 'LIMIT', stop: 'SL-M' };
const KIND: Record<string, OrderKind> = { MARKET: 'market', LIMIT: 'limit', 'SL-M': 'stop', SL: 'stop' };
const PRODUCT: Record<Product, string> = { intraday: 'I', delivery: 'D' };

interface UpstoxEnvelope<T> {
  status?: string;
  data?: T;
  errors?: { message?: string }[];
  message?: string;
}

interface RawOrder {
  order_id: string;
  trading_symbol: string;
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
  trading_symbol: string;
  product: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
}

function status(raw: string): OrderStatus {
  const s = raw.toLowerCase();
  if (s === 'complete') return 'complete';
  if (s === 'rejected') return 'rejected';
  if (s === 'cancelled') return 'cancelled';
  return 'open';
}

class Upstox implements TradingBroker {
  constructor(private readonly token: string) {}

  private async call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API}${path}`, {
      ...init,
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${this.token}`,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
    const body = (await res.json().catch(() => ({}))) as UpstoxEnvelope<T>;
    if (!res.ok || body.status === 'error') {
      const msg = body.errors?.[0]?.message ?? body.message ?? `Upstox HTTP ${res.status}`;
      throw new BrokerError(msg, res.status === 401 ? 401 : 502);
    }
    return body.data as T;
  }

  async profile() {
    const d = await this.call<{ user_name?: string; user_id?: string }>('/user/profile');
    return { user: d.user_name ?? d.user_id ?? 'Upstox' };
  }

  async orders(): Promise<BrokerOrder[]> {
    const rows = (await this.call<RawOrder[]>('/order/retrieve-all')) ?? [];
    return rows.map((r) => ({
      id: r.order_id,
      symbol: r.trading_symbol,
      side: r.transaction_type === 'SELL' ? 'sell' : 'buy',
      kind: KIND[r.order_type] ?? 'limit',
      product: r.product === 'D' ? 'delivery' : 'intraday',
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
    const rows = (await this.call<RawPosition[]>('/portfolio/short-term-positions')) ?? [];
    return rows
      .filter((r) => r.quantity !== 0)
      .map((r) => ({
        symbol: r.trading_symbol,
        product: r.product === 'D' ? 'delivery' : 'intraday',
        qty: r.quantity,
        avg: r.average_price,
        ltp: r.last_price,
        pnl: r.pnl,
      }));
  }

  async place(o: PlaceOrder) {
    const key = await instrumentKey(o.symbol);
    if (!key) throw new BrokerError(`no NSE equity instrument for ${o.symbol}`, 404);
    const d = await this.call<{ order_id: string }>('/order/place', {
      method: 'POST',
      body: JSON.stringify({
        quantity: o.qty,
        product: PRODUCT[o.product],
        validity: 'DAY',
        price: o.kind === 'limit' ? o.price : 0,
        // Upstox tags are at most 40 characters
        tag: o.clientId.slice(0, 40),
        instrument_token: key,
        order_type: ORDER_TYPE[o.kind],
        transaction_type: o.side === 'buy' ? 'BUY' : 'SELL',
        disclosed_quantity: 0,
        trigger_price: o.kind === 'stop' ? o.trigger : 0,
        is_amo: false,
      }),
    });
    return { orderId: d.order_id };
  }

  async cancel(orderId: string) {
    await this.call(`/order/cancel?order_id=${encodeURIComponent(orderId)}`, { method: 'DELETE' });
  }
}

export const upstox: BrokerDefinition = {
  id: 'upstox',
  name: 'Upstox',
  configured: () => Boolean(process.env.UPSTOX_API_KEY && process.env.UPSTOX_API_SECRET),
  loginUrl: (state) =>
    `${API}/login/authorization/dialog?${new URLSearchParams({
      client_id: process.env.UPSTOX_API_KEY ?? '',
      redirect_uri: redirectUri(),
      response_type: 'code',
      state,
    })}`,
  async exchange(query) {
    const code = query.get('code');
    if (!code) throw new BrokerError('Upstox returned no code', 400);
    const res = await fetch(`${API}/login/authorization/token`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: process.env.UPSTOX_API_KEY ?? '',
        client_secret: process.env.UPSTOX_API_SECRET ?? '',
        redirect_uri: redirectUri(),
        grant_type: 'authorization_code',
      }),
    });
    const body = (await res.json().catch(() => ({}))) as { access_token?: string; errors?: { message?: string }[] };
    if (!res.ok || !body.access_token) {
      throw new BrokerError(body.errors?.[0]?.message ?? `Upstox token exchange HTTP ${res.status}`, 502);
    }
    return body.access_token;
  },
  connect: (token) => new Upstox(token),
};
