import assert from 'node:assert/strict';
import { test } from 'node:test';
import { firstLevelBefore, gammaLevels, gammaRegime, parseSnapshots, snapAt, snapFromResponse, snapshotFileName } from './gamma-history.ts';

const snap = (asOf, all = {}, scale = 40) => ({
  asOf,
  symbol: 'NQ=F',
  proxy: 'QQQ',
  spot: 600,
  scale,
  convention: 'dealer_long_calls',
  days: 45,
  expiries: ['2026-11-20'],
  all: { flip: 590, callWalls: [610, 620], putWalls: [580, 570], gammaWall: 600, netGex: 1e9, ...all },
  nearest: null,
});

test('parseSnapshots: sorted by time; half-written lines and proxies without a scale are dropped', () => {
  const text = [JSON.stringify(snap(300)), '{"asOf": 5', JSON.stringify(snap(100)), JSON.stringify({ ...snap(200), scale: null }), ''].join('\n');
  assert.deepEqual(parseSnapshots(text).map((s) => s.asOf), [100, 300]);
});

test('snapAt: the newest snapshot read at or before the time, if not older than the max age', () => {
  const s = [snap(100), snap(200), snap(300)];
  assert.equal(snapAt(s, 99), null);
  assert.equal(snapAt(s, 100).asOf, 100);
  assert.equal(snapAt(s, 250).asOf, 200);
  assert.equal(snapAt(s, 1000).asOf, 300);
  assert.equal(snapAt(s, 1000, 600), null);
  assert.equal(snapAt([], 1000), null);
});

test('levels and regime are in chart prices (× scale)', () => {
  const s = snap(0);
  assert.deepEqual(
    gammaLevels(s).map((l) => [l.name, l.price]),
    [['gamma flip', 23600], ['C1', 24400], ['C2', 24800], ['P1', 23200], ['P2', 22800], ['gamma wall', 24000]],
  );
  assert.deepEqual(gammaRegime(s, 23700), { regime: 'positive', basis: 'flip', flip: 23600 });
  assert.equal(gammaRegime(s, 23500).regime, 'negative');
  assert.deepEqual(gammaRegime(snap(0, { flip: null, netGex: -1 }), 1), { regime: 'negative', basis: 'net', flip: null });
});

test('firstLevelBefore: the nearest level strictly between entry and target, either direction', () => {
  const s = snap(0);
  assert.deepEqual(firstLevelBefore(s, 'long', 23700, 25000), { name: 'gamma wall', price: 24000 });
  assert.deepEqual(firstLevelBefore(s, 'short', 24500, 22000), { name: 'C1', price: 24400 });
  assert.equal(firstLevelBefore(s, 'long', 24900, 25000), null);
  assert.equal(firstLevelBefore(s, 'long', 23000, 23200), null, 'a level exactly at the target is not "before" it');
});

test('snapFromResponse keeps the first three walls and the proxy scale; unsupported responses give null', () => {
  const lv = (flip) => ({ spot: 600, netGex: 2, flip, callWalls: [1, 2, 3, 4], putWalls: [5, 6, 7, 8], range: null, gammaWall: 9, strikes: [] });
  const s = snapFromResponse(
    { supported: true, symbol: 'NQ=F', proxy: 'QQQ', asOf: 50, spot: 600, scale: 41.5, convention: 'dealer_long_calls', expiries: ['x'], all: lv(590), nearest: lv(null) },
    45,
  );
  assert.deepEqual(s.all, { flip: 590, callWalls: [1, 2, 3], putWalls: [5, 6, 7], gammaWall: 9, netGex: 2 });
  assert.equal(s.scale, 41.5);
  assert.equal(s.nearest.flip, null);
  assert.equal(snapFromResponse({ supported: false, reason: 'x' }, 45), null);
  assert.equal(snapFromResponse({ supported: true, symbol: 'SPY', asOf: 1, spot: 600, all: lv(1) }, 45).scale, 1);
});

test('snapshotFileName: NQ=F keeps its old name; NIFTY futures get a safe one; paths are refused', () => {
  assert.equal(snapshotFileName('NQ=F'), 'NQ_F.jsonl');
  assert.equal(snapshotFileName('NIFTY1!.NFO'), 'NIFTY1_.NFO.jsonl');
  assert.equal(snapshotFileName('^nsei'), '^NSEI.jsonl');
  assert.equal(snapshotFileName('../etc'), null);
  assert.equal(snapshotFileName('a/b'), null);
});

test('snapFromResponse: an NSE futures chart stores the index as proxy and futures ÷ index as scale', () => {
  const lv = { flip: 25900, callWalls: [26000, 26200], putWalls: [25800], gammaWall: 26000, netGex: 5e9 };
  const res = { supported: true, source: 'upstox', symbol: 'NIFTY', asOf: 1000, spot: 25800, futurePrice: 25929, scale: 25929 / 25800, all: lv, nearest: lv };
  const fut = snapFromResponse(res, 45, 'NIFTY1!.NFO');
  assert.equal(fut.symbol, 'NIFTY1!.NFO');
  assert.equal(fut.proxy, 'NIFTY');
  assert.ok(Math.abs(gammaLevels(fut)[0].price - 25900 * (25929 / 25800)) < 1e-9, 'flip moved onto futures prices');
  // the index chart itself: no futures price, no proxy, scale 1
  const idx = snapFromResponse({ ...res, futurePrice: undefined, scale: undefined }, 45, '^NSEI');
  assert.equal(idx.symbol, '^NSEI');
  assert.equal(idx.proxy, undefined);
  assert.equal(idx.scale, 1);
  // a futures chart without a futures price cannot be placed: no line
  assert.equal(snapFromResponse({ ...res, futurePrice: undefined, scale: undefined }, 45, 'NIFTY1!.NFO'), null);
});
