import { chmodSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { BrokerId } from './types';

/**
 * Daily access tokens, kept in a local file (mode 600, git-ignored) because both brokers expire them every morning
 * and a Next dev server forgets module state on reload. This is a single-user, run-it-on-your-own-machine store: do
 * not deploy this route to a serverless host, where the file would not survive.
 */
const FILE = process.env.BROKER_SESSION_FILE ?? path.join(process.cwd(), '.broker-sessions.json');
const IST_MS = 19_800_000;
/** Both brokers invalidate the token between 03:30 and 06:00 IST; anything issued before the last 06:00 is dead. */
const EXPIRY_HOUR_IST = 6;

type Store = Partial<Record<BrokerId, { token: string; at: number }>>;

function read(): Store {
  try {
    return existsSync(FILE) ? (JSON.parse(readFileSync(FILE, 'utf8')) as Store) : {};
  } catch {
    return {};
  }
}

/** ms epoch of the most recent 06:00 IST at or before `now`. */
export function lastExpiry(now = Date.now()): number {
  const ist = now + IST_MS;
  const day = ist - (ist % 86_400_000);
  const boundary = day + EXPIRY_HOUR_IST * 3_600_000;
  return (boundary > ist ? boundary - 86_400_000 : boundary) - IST_MS;
}

export function getToken(id: BrokerId, now = Date.now()): string | null {
  const s = read()[id];
  return s && s.at >= lastExpiry(now) ? s.token : null;
}

export function saveToken(id: BrokerId, token: string): void {
  const next = { ...read(), [id]: { token, at: Date.now() } };
  writeFileSync(FILE, JSON.stringify(next), { mode: 0o600 });
  chmodSync(FILE, 0o600);
}

export function clearToken(id: BrokerId): void {
  const next = read();
  delete next[id];
  writeFileSync(FILE, JSON.stringify(next), { mode: 0o600 });
}
