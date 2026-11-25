/**
 * Trading hotkeys for the paper account on /chart: which key combo fires which order action.
 * Matched on `KeyboardEvent.code` (physical key), so keyboard layout and Option-modified characters don't matter.
 */

export type HotkeyAction =
  | 'buyMarket'
  | 'sellMarket'
  | 'buyLimit'
  | 'sellLimit'
  | 'flatten'
  | 'reverse'
  | 'closeHalf'
  | 'breakeven'
  | 'cancelSymbol'
  | 'cancelAll';

export interface Combo {
  code: string;
  shift?: boolean;
  alt?: boolean;
  ctrl?: boolean;
  meta?: boolean;
}

export interface HotkeySettings {
  enabled: boolean;
  /** Limit hotkeys rest this many ticks away from the last price: buys below it, sells above it. */
  limitTicks: number;
  bindings: Record<HotkeyAction, Combo | null>;
}

export const HOTKEY_ACTIONS: { id: HotkeyAction; label: string; hint: string }[] = [
  { id: 'buyMarket', label: 'Buy market', hint: 'Panel quantity at market' },
  { id: 'sellMarket', label: 'Sell market', hint: 'Panel quantity at market' },
  { id: 'buyLimit', label: 'Buy limit', hint: 'Rests N ticks below last' },
  { id: 'sellLimit', label: 'Sell limit', hint: 'Rests N ticks above last' },
  { id: 'flatten', label: 'Flatten symbol', hint: 'Cancel its orders and close the position' },
  { id: 'reverse', label: 'Reverse position', hint: 'Close and open the same size the other way' },
  { id: 'closeHalf', label: 'Close half', hint: 'Half the position at market, rounded down' },
  { id: 'breakeven', label: 'Stop to breakeven', hint: 'Move the stop to the entry price' },
  { id: 'cancelSymbol', label: 'Cancel symbol orders', hint: 'Working orders on this chart' },
  { id: 'cancelAll', label: 'Cancel all orders', hint: 'Working orders on every symbol' },
];

export const DEFAULT_HOTKEYS: HotkeySettings = {
  enabled: true,
  limitTicks: 1,
  bindings: {
    buyMarket: { code: 'KeyB', shift: true },
    sellMarket: { code: 'KeyS', shift: true },
    buyLimit: { code: 'KeyB', shift: true, alt: true },
    sellLimit: { code: 'KeyS', shift: true, alt: true },
    flatten: { code: 'KeyF', shift: true },
    reverse: { code: 'KeyR', shift: true },
    closeHalf: { code: 'KeyW', shift: true },
    breakeven: { code: 'KeyE', shift: true },
    cancelSymbol: { code: 'Escape', shift: true },
    cancelAll: { code: 'Escape', shift: true, alt: true },
  },
};

interface KeyLike {
  code: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

const MODIFIER_CODES = /^(Shift|Alt|Control|Meta|OS|CapsLock|Fn)/;

/** The combo a key event makes, or null for a lone modifier press. */
export function comboOf(e: KeyLike): Combo | null {
  if (!e.code || MODIFIER_CODES.test(e.code)) return null;
  const c: Combo = { code: e.code };
  if (e.shiftKey) c.shift = true;
  if (e.altKey) c.alt = true;
  if (e.ctrlKey) c.ctrl = true;
  if (e.metaKey) c.meta = true;
  return c;
}

export function sameCombo(a: Combo | null | undefined, b: Combo | null | undefined): boolean {
  return (
    !!a &&
    !!b &&
    a.code === b.code &&
    !!a.shift === !!b.shift &&
    !!a.alt === !!b.alt &&
    !!a.ctrl === !!b.ctrl &&
    !!a.meta === !!b.meta
  );
}

export function actionFor(e: KeyLike, s: HotkeySettings): HotkeyAction | null {
  if (!s.enabled) return null;
  const c = comboOf(e);
  if (!c) return null;
  for (const { id } of HOTKEY_ACTIONS) if (sameCombo(s.bindings[id], c)) return id;
  return null;
}

const KEY_NAMES: Record<string, string> = {
  Escape: 'Esc',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  Space: 'Space',
  Enter: 'Enter',
  Backspace: 'Backspace',
  Delete: 'Del',
  Minus: '-',
  Equal: '=',
  BracketLeft: '[',
  BracketRight: ']',
  Semicolon: ';',
  Quote: "'",
  Comma: ',',
  Period: '.',
  Slash: '/',
  Backslash: '\\',
  Backquote: '`',
};

export function keyName(code: string): string {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^Numpad/.test(code)) return `Num${code.slice(6)}`;
  return KEY_NAMES[code] ?? code;
}

export function comboLabel(c: Combo | null | undefined, mac = false): string {
  if (!c) return '—';
  const parts: string[] = [];
  if (c.ctrl) parts.push('Ctrl');
  if (c.meta) parts.push(mac ? 'Cmd' : 'Win');
  if (c.alt) parts.push(mac ? 'Option' : 'Alt');
  if (c.shift) parts.push('Shift');
  parts.push(keyName(c.code));
  return parts.join('+');
}

function asCombo(v: unknown): Combo | null | undefined {
  if (v === null) return null;
  if (!v || typeof v !== 'object') return undefined;
  const o = v as Record<string, unknown>;
  if (typeof o.code !== 'string' || !o.code) return undefined;
  const c: Combo = { code: o.code };
  if (o.shift === true) c.shift = true;
  if (o.alt === true) c.alt = true;
  if (o.ctrl === true) c.ctrl = true;
  if (o.meta === true) c.meta = true;
  return c;
}

/** Saved settings over the defaults: unknown actions dropped, bad values ignored, new actions get defaults. */
export function mergeHotkeys(saved: unknown): HotkeySettings {
  const o = saved && typeof saved === 'object' ? (saved as Record<string, unknown>) : {};
  const b = o.bindings && typeof o.bindings === 'object' ? (o.bindings as Record<string, unknown>) : {};
  const bindings = { ...DEFAULT_HOTKEYS.bindings };
  for (const { id } of HOTKEY_ACTIONS) {
    const c = asCombo(b[id]);
    if (c !== undefined) bindings[id] = c;
  }
  const ticks = Number(o.limitTicks);
  return {
    enabled: typeof o.enabled === 'boolean' ? o.enabled : DEFAULT_HOTKEYS.enabled,
    limitTicks: Number.isInteger(ticks) && ticks >= 1 && ticks <= 1000 ? ticks : DEFAULT_HOTKEYS.limitTicks,
    bindings,
  };
}

/** A chart shortcut a trading hotkey can shadow. `anyAlt`: it also fires with Alt/Option held. */
export interface ReservedKey {
  combo: Combo;
  label: string;
  anyAlt?: boolean;
}

function hits(r: ReservedKey, c: Combo): boolean {
  return r.anyAlt ? sameCombo({ ...r.combo, alt: c.alt }, c) : sameCombo(r.combo, c);
}

/** Per action: the other hotkeys and chart shortcuts its combo collides with. */
export function findClashes(
  s: HotkeySettings,
  reserved: ReservedKey[],
  mac = false,
): Partial<Record<HotkeyAction, string[]>> {
  const out: Partial<Record<HotkeyAction, string[]>> = {};
  for (const { id } of HOTKEY_ACTIONS) {
    const c = s.bindings[id];
    if (!c) continue;
    const list: string[] = [];
    for (const other of HOTKEY_ACTIONS)
      if (other.id !== id && sameCombo(s.bindings[other.id], c)) list.push(`hotkey "${other.label}"`);
    for (const r of reserved)
      if (hits(r, c)) list.push(`${r.label} (${comboLabel(r.combo, mac)})`);
    if (list.length) out[id] = list;
  }
  return out;
}

/** Price `ticks` ticks on the passive side of `last`: below for a buy, above for a sell, rounded to the tick. */
export function passiveLimit(side: 'buy' | 'sell', last: number, ticks: number, decimals: number): number {
  const tick = 10 ** -decimals;
  const n = Math.max(1, ticks);
  const p = side === 'buy' ? last - n * tick : last + n * tick;
  return Number(p.toFixed(decimals));
}
