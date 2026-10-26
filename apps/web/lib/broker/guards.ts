import { auth } from '../../auth';
import { BrokerError, type PlaceOrder } from './types';

/**
 * Every order passes through here before a broker sees it. Real orders are off until BROKER_LIVE_ORDERS=true, and
 * even then each one is capped. The caps stop a fat finger; they are not a defence against someone who already
 * holds a session, which is what the same-origin and sign-in checks are for.
 */
export function limits() {
  return {
    live: process.env.BROKER_LIVE_ORDERS === 'true',
    maxQty: Number(process.env.BROKER_MAX_QTY ?? 500),
    maxNotional: Number(process.env.BROKER_MAX_ORDER_INR ?? 25_000),
  };
}

/** The signed-in user, or a refusal. DISABLE_AUTH is the repo's local-dev switch, same as /api/token. */
export async function requireUser(): Promise<void> {
  if (process.env.DISABLE_AUTH === 'true') return;
  const session = await auth();
  if (!session?.user?.email) throw new BrokerError('Unauthorized', 401);
}

/** A cross-site page can make the browser POST here; only our own origin may. */
export function requireSameOrigin(req: Request): void {
  const origin = req.headers.get('origin');
  const host = req.headers.get('host');
  if (!origin || !host || new URL(origin).host !== host) throw new BrokerError('Cross-origin request refused', 403);
}

const SYMBOL = /^[A-Z0-9&\-_]{1,24}$/;
const SEEN_MS = 120_000;
const seen = new Map<string, number>();

export function validateOrder(o: PlaceOrder): PlaceOrder {
  const { live, maxQty, maxNotional } = limits();
  if (!live) throw new BrokerError('Live orders are off. Set BROKER_LIVE_ORDERS=true on the server to enable them.', 403);
  if (typeof o.clientId !== 'string' || o.clientId.length < 8 || o.clientId.length > 40) {
    throw new BrokerError('clientId required', 400);
  }
  const symbol = String(o.symbol ?? '').toUpperCase().replace(/\.NS$/, '');
  if (!SYMBOL.test(symbol)) throw new BrokerError('NSE symbols only', 400);
  if (o.side !== 'buy' && o.side !== 'sell') throw new BrokerError('bad side', 400);
  if (!['market', 'limit', 'stop'].includes(o.kind)) throw new BrokerError('bad order type', 400);
  if (!['intraday', 'delivery'].includes(o.product)) throw new BrokerError('bad product', 400);
  if (!Number.isInteger(o.qty) || o.qty < 1) throw new BrokerError('quantity must be a whole number', 400);
  if (o.qty > maxQty) throw new BrokerError(`quantity ${o.qty} is over the cap of ${maxQty}`, 400);
  const price = o.price == null ? undefined : Number(o.price);
  const trigger = o.trigger == null ? undefined : Number(o.trigger);
  if (o.kind === 'limit' && !(price && price > 0)) throw new BrokerError('limit price required', 400);
  if (o.kind === 'stop' && !(trigger && trigger > 0)) throw new BrokerError('trigger price required', 400);
  const ref = o.kind === 'limit' ? price : o.kind === 'stop' ? trigger : Number(o.refPrice);
  if (!(ref && ref > 0)) throw new BrokerError('no price to size the order against', 400);
  if (ref * o.qty > maxNotional) {
    throw new BrokerError(`order value ₹${Math.round(ref * o.qty)} is over the cap of ₹${maxNotional}`, 400);
  }
  const now = Date.now();
  for (const [k, t] of seen) if (now - t > SEEN_MS) seen.delete(k);
  if (seen.has(o.clientId)) throw new BrokerError('Duplicate order refused', 409);
  seen.set(o.clientId, now);
  return { ...o, symbol, price, trigger };
}
