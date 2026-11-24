'use client';

import { useSyncExternalStore } from 'react';
import { load, save } from './storage';

/**
 * The time zone the chart's time axis, crosshair and clock are drawn in — TradingView's bottom-right clock menu.
 * Only labels move: session days, VWAP resets, session shading and every other calculation stay on the exchange's
 * zone. 'exchange' follows the symbol; 'local' follows this browser (TradingView's usual setup).
 */
export type DisplayTzSetting = 'exchange' | 'local' | string;

const KEY = 'tv:displayTz';
const EVENT = 'tv:displayTz';

export const DISPLAY_TZ_CHOICES: { id: DisplayTzSetting; label: string }[] = [
  { id: 'exchange', label: 'Exchange' },
  { id: 'local', label: 'Local (this computer)' },
  { id: 'UTC', label: 'UTC' },
  { id: 'Asia/Kolkata', label: 'Kolkata' },
  { id: 'America/New_York', label: 'New York' },
  { id: 'America/Chicago', label: 'Chicago' },
  { id: 'Europe/London', label: 'London' },
  { id: 'Asia/Tokyo', label: 'Tokyo' },
];

function read(): DisplayTzSetting {
  return load<DisplayTzSetting>(KEY, 'local');
}

export function setDisplayTz(v: DisplayTzSetting): void {
  save(KEY, v);
  // Same-tab listeners (every chart pane); the 'storage' event only reaches other tabs.
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(EVENT, cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener('storage', cb);
  };
}

export function useDisplayTzSetting(): DisplayTzSetting {
  return useSyncExternalStore(subscribe, read, () => 'exchange');
}

export function localTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** The IANA zone a setting means for a symbol trading in `exchangeTz`. */
export function resolveDisplayTz(setting: DisplayTzSetting, exchangeTz: string): string {
  if (setting === 'exchange') return exchangeTz;
  if (setting === 'local') return localTz();
  return setting;
}
