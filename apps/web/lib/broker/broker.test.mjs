import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';

process.env.BROKER_LIVE_ORDERS = 'true';
process.env.BROKER_MAX_QTY = '100';
process.env.BROKER_MAX_ORDER_INR = '10000';
process.env.UPSTOX_API_KEY = 'ukey';
process.env.UPSTOX_API_SECRET = 'usecret';
process.env.KITE_API_KEY = 'kkey';
process.env.KITE_API_SECRET = 'ksecret';

const { validateOrder } = await import('./guards.ts');
const { lastExpiry } = await import('./session-store.ts');
const { kite } = await import('./kite.ts');
const { upstox } = await import('./upstox.ts');

const base = { symbol: 'RELIANCE.NS', side: 'buy', kind: 'limit', qty: 5, price: 100, product: 'intraday' };
let n = 0;
const order = (o = {}) => ({ clientId: `client-id-${++n}-abcdef`, ...base, ...o });

test('validateOrder strips .NS and passes a sane order', () => {
  const v = validateOrder(order());
  assert.equal(v.symbol, 'RELIANCE');
});

test('validateOrder enforces the quantity and value caps', () => {
  assert.throws(() => validateOrder(order({ qty: 101 })), /over the cap of 100/);
  assert.throws(() => validateOrder(order({ qty: 90, price: 200 })), /over the cap of ₹10000/);
});

test('validateOrder sizes a market order from the reference price', () => {
  assert.throws(() => validateOrder(order({ kind: 'market', price: undefined })), /no price/);
  assert.throws(() => validateOrder(order({ kind: 'market', price: undefined, refPrice: 5000 })), /over the cap/);
  assert.ok(validateOrder(order({ kind: 'market', price: undefined, refPrice: 100 })));
});

test('validateOrder refuses a repeated clientId, fractional qty and a missing stop trigger', () => {
  const o = order();
  validateOrder(o);
  assert.throws(() => validateOrder(o), /Duplicate/);
  assert.throws(() => validateOrder(order({ qty: 1.5 })), /whole number/);
  assert.throws(() => validateOrder(order({ kind: 'stop', price: undefined })), /trigger/);
});

test('validateOrder refuses everything while live orders are off', async () => {
  process.env.BROKER_LIVE_ORDERS = 'false';
  assert.throws(() => validateOrder(order()), /Live orders are off/);
  process.env.BROKER_LIVE_ORDERS = 'true';
});

test('lastExpiry is the most recent 06:00 IST', () => {
  const at = (iso) => lastExpiry(Date.parse(iso));
  assert.equal(at('2026-10-26T05:59:00+05:30'), Date.parse('2026-10-25T06:00:00+05:30'));
  assert.equal(at('2026-10-26T06:00:00+05:30'), Date.parse('2026-10-26T06:00:00+05:30'));
  assert.equal(at('2026-10-26T15:00:00+05:30'), Date.parse('2026-10-26T06:00:00+05:30'));
});

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const { status = 200, body } = handler(String(url), init);
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  };
  return calls;
}

test('Kite places a limit order as form data with its auth header', async () => {
  const calls = mockFetch(() => ({ body: { status: 'success', data: { order_id: '2210' } } }));
  const res = await kite.connect('tok').place({ ...base, symbol: 'RELIANCE', clientId: 'ab-cd-1234-xyz' });
  assert.deepEqual(res, { orderId: '2210' });
  const { url, init } = calls[0];
  assert.equal(url, 'https://api.kite.trade/orders/regular');
  assert.equal(init.headers.Authorization, 'token kkey:tok');
  const f = new URLSearchParams(init.body);
  assert.equal(f.get('tradingsymbol'), 'RELIANCE');
  assert.equal(f.get('product'), 'MIS');
  assert.equal(f.get('order_type'), 'LIMIT');
  assert.equal(f.get('price'), '100');
  assert.equal(f.get('tag'), 'abcd1234xyz');
});

test('Kite maps orders and positions, and turns a 403 into a re-login', async () => {
  mockFetch((url) =>
    url.endsWith('/orders')
      ? { body: { status: 'success', data: [
          { order_id: '1', exchange: 'NSE', tradingsymbol: 'TCS', transaction_type: 'SELL', order_type: 'SL-M', product: 'CNC',
            quantity: 3, filled_quantity: 0, price: 0, average_price: 0, status: 'TRIGGER PENDING', order_timestamp: '2026-10-26 10:00:00' },
          { order_id: '2', exchange: 'BSE', tradingsymbol: 'X', transaction_type: 'BUY', order_type: 'LIMIT', product: 'MIS',
            quantity: 1, filled_quantity: 0, price: 1, average_price: 0, status: 'OPEN' },
        ] } }
      : { body: { status: 'success', data: { net: [
          { exchange: 'NSE', tradingsymbol: 'TCS', product: 'MIS', quantity: -2, average_price: 10, last_price: 9, pnl: 2 },
          { exchange: 'NSE', tradingsymbol: 'FLAT', product: 'MIS', quantity: 0, average_price: 10, last_price: 9, pnl: 0 },
        ] } } },
  );
  const api = kite.connect('tok');
  const orders = await api.orders();
  assert.equal(orders.length, 1);
  assert.deepEqual([orders[0].side, orders[0].kind, orders[0].product, orders[0].status], ['sell', 'stop', 'delivery', 'open']);
  const pos = await api.positions();
  assert.equal(pos.length, 1);
  assert.equal(pos[0].qty, -2);
  mockFetch(() => ({ status: 403, body: { status: 'error', message: 'Incorrect api_key or access_token' } }));
  await assert.rejects(api.orders(), (e) => e.status === 401);
});

test('Kite token exchange signs api_key + request_token + secret', async () => {
  const calls = mockFetch(() => ({ body: { status: 'success', data: { access_token: 'acc' } } }));
  const token = await kite.exchange(new URLSearchParams({ request_token: 'rt', status: 'success' }));
  assert.equal(token, 'acc');
  const f = new URLSearchParams(calls[0].init.body);
  assert.equal(f.get('checksum'), createHash('sha256').update('kkeyrtksecret').digest('hex'));
  await assert.rejects(kite.exchange(new URLSearchParams({ request_token: 'rt', status: 'error' })), /not completed/);
});

test('Upstox cancels by order id and exchanges the login code', async () => {
  const calls = mockFetch((url) =>
    url.includes('/login/authorization/token')
      ? { body: { access_token: 'uacc' } }
      : { body: { status: 'success', data: { order_id: '9' } } },
  );
  assert.equal(await upstox.exchange(new URLSearchParams({ code: 'c1' })), 'uacc');
  const f = new URLSearchParams(calls[0].init.body);
  assert.equal(f.get('grant_type'), 'authorization_code');
  assert.equal(f.get('client_id'), 'ukey');
  await upstox.connect('t').cancel('230101000001');
  assert.equal(calls[1].url, 'https://api.upstox.com/v2/order/cancel?order_id=230101000001');
  assert.equal(calls[1].init.method, 'DELETE');
  assert.equal(calls[1].init.headers.Authorization, 'Bearer t');
});

test('Upstox surfaces the broker error message', async () => {
  mockFetch(() => ({ status: 400, body: { status: 'error', errors: [{ message: 'Insufficient funds' }] } }));
  await assert.rejects(upstox.connect('t').orders(), /Insufficient funds/);
});

test('login URLs carry the state', () => {
  assert.match(upstox.loginUrl('abc'), /state=abc/);
  assert.match(decodeURIComponent(kite.loginUrl('abc')), /redirect_params=state=abc/);
});
