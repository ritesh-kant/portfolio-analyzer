import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEFAULT_HOTKEYS,
  actionFor,
  comboLabel,
  comboOf,
  findClashes,
  mergeHotkeys,
  passiveLimit,
} from './hotkeys.ts';
import {
  breakevenStop,
  cancelOrders,
  closePart,
  flattenSymbol,
  newAccount,
  placeMarket,
  placePending,
  step,
} from './paper.ts';

const key = (code, mods = {}) => ({
  code,
  shiftKey: !!mods.shift,
  altKey: !!mods.alt,
  ctrlKey: !!mods.ctrl,
  metaKey: !!mods.meta,
});

test('hotkeys: default combos map to actions, modifiers must match exactly', () => {
  const s = DEFAULT_HOTKEYS;
  assert.equal(actionFor(key('KeyB', { shift: true }), s), 'buyMarket');
  assert.equal(actionFor(key('KeyB', { shift: true, alt: true }), s), 'buyLimit');
  assert.equal(actionFor(key('KeyS', { shift: true }), s), 'sellMarket');
  assert.equal(actionFor(key('Escape', { shift: true }), s), 'cancelSymbol');
  assert.equal(actionFor(key('Escape', { shift: true, alt: true }), s), 'cancelAll');
  // bare B is the brush tool, Cmd+Shift+B is the browser's
  assert.equal(actionFor(key('KeyB'), s), null);
  assert.equal(actionFor(key('KeyB', { shift: true, meta: true }), s), null);
  assert.equal(actionFor(key('KeyB', { shift: true }), { ...s, enabled: false }), null);
});

test('hotkeys: a lone modifier is not a combo; labels read like the keyboard', () => {
  assert.equal(comboOf(key('ShiftLeft', { shift: true })), null);
  assert.equal(comboOf(key('AltRight', { alt: true })), null);
  assert.equal(comboLabel({ code: 'KeyB', shift: true, alt: true }, true), 'Option+Shift+B');
  assert.equal(comboLabel({ code: 'Escape', shift: true }, false), 'Shift+Esc');
  assert.equal(comboLabel(null), '—');
});

test('hotkeys: mergeHotkeys keeps valid saved values and falls back on junk', () => {
  const m = mergeHotkeys({
    enabled: false,
    limitTicks: 3,
    bindings: { buyMarket: { code: 'KeyQ', alt: true }, sellMarket: null, flatten: 'x', bogus: { code: 'KeyZ' } },
  });
  assert.equal(m.enabled, false);
  assert.equal(m.limitTicks, 3);
  assert.deepEqual(m.bindings.buyMarket, { code: 'KeyQ', alt: true });
  assert.equal(m.bindings.sellMarket, null);
  assert.deepEqual(m.bindings.flatten, DEFAULT_HOTKEYS.bindings.flatten);
  assert.equal('bogus' in m.bindings, false);
  assert.equal(mergeHotkeys({ limitTicks: 0 }).limitTicks, 1);
  assert.deepEqual(mergeHotkeys(null), DEFAULT_HOTKEYS);
});

test('hotkeys: clashes with other hotkeys and with Alt-tolerant chart keys', () => {
  const reserved = [{ combo: { code: 'KeyB' }, label: 'brush', anyAlt: true }];
  assert.deepEqual(findClashes(DEFAULT_HOTKEYS, reserved), {});
  const s = {
    ...DEFAULT_HOTKEYS,
    bindings: { ...DEFAULT_HOTKEYS.bindings, buyMarket: { code: 'KeyB', alt: true }, reverse: { code: 'KeyS', shift: true } },
  };
  const c = findClashes(s, reserved);
  assert.deepEqual(c.buyMarket, ['brush (B)']);
  assert.deepEqual(c.reverse, ['hotkey "Sell market"']);
  assert.deepEqual(c.sellMarket, ['hotkey "Reverse position"']);
});

test('hotkeys: passive limit rests on the right side of last, at least one tick', () => {
  assert.equal(passiveLimit('buy', 100, 2, 2), 99.98);
  assert.equal(passiveLimit('sell', 100, 2, 2), 100.02);
  assert.equal(passiveLimit('buy', 100, 0, 1), 99.9);
});

test('paper: closePart halves (rounded down) and refuses a 1-unit position', () => {
  let s = placeMarket(newAccount(100000), 'X', 'buy', 5, 100, 60).state;
  const r = closePart(s, 'X', 0.5, 110, 120);
  assert.equal(r.error, undefined);
  assert.equal(r.state.positions.X.qty, 3);
  assert.equal(r.state.history.at(-1).qty, 2);
  s = placeMarket(newAccount(100000), 'X', 'sell', 1, 100, 60).state;
  assert.match(closePart(s, 'X', 0.5, 99, 120).error, /nothing to split/);
  assert.match(closePart(newAccount(), 'X', 0.5, 99, 120).error, /No open position/);
});

test('paper: breakevenStop only once price is beyond entry, then stops out at entry', () => {
  let s = placeMarket(newAccount(100000), 'X', 'buy', 10, 100, 60, { sl: 95 }).state;
  assert.match(breakevenStop(s, 'X', 99).error, /above entry/);
  const r = breakevenStop(s, 'X', 103);
  assert.equal(r.error, undefined);
  assert.equal(r.state.positions.X.sl, 100);
  assert.match(breakevenStop(r.state, 'X', 104).error, /already/);
  s = step(r.state, 'X', [{ time: 120, open: 101, high: 101, low: 99.5, close: 100, volume: 1 }], 60);
  assert.equal(s.positions.X, undefined);
  assert.equal(s.history.at(-1).exit, 100);
  // short: price must be below entry
  const sh = placeMarket(newAccount(100000), 'Y', 'sell', 2, 50, 60).state;
  assert.match(breakevenStop(sh, 'Y', 51).error, /below entry/);
  assert.equal(breakevenStop(sh, 'Y', 49).state.positions.Y.sl, 50);
});

test('paper: cancelOrders by symbol or all; flattenSymbol cancels and closes', () => {
  let s = newAccount(100000);
  s = placePending(s, 'X', 'buy', 'limit', 1, 90, 100, 60).state;
  s = placePending(s, 'X', 'sell', 'limit', 1, 110, 100, 60).state;
  s = placePending(s, 'Y', 'buy', 'limit', 1, 40, 50, 60).state;
  const one = cancelOrders(s, 'X', 70);
  assert.equal(one.n, 2);
  assert.deepEqual(one.state.orders.map((o) => o.symbol), ['Y']);
  assert.equal(cancelOrders(s, null, 70).state.orders.length, 0);

  s = placeMarket(s, 'X', 'buy', 4, 100, 60).state;
  const f = flattenSymbol(s, 'X', 105, 120);
  assert.equal(f.error, undefined);
  assert.equal(f.closed, 4);
  assert.equal(f.cancelled, 2);
  assert.equal(f.state.positions.X, undefined);
  assert.equal(f.state.orders.length, 1);
  assert.match(flattenSymbol(f.state, 'X', 105, 130).error, /Nothing to flatten/);
});
