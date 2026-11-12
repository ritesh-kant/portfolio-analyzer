import { promises as fs } from 'node:fs';
import path from 'node:path';
import type { CacheManifest } from './bar-cache';

/**
 * Where `research/backtests/yahoo_futures_history.py` (`pnpm bt:nq`) keeps futures bars. The web app runs from
 * apps/web, so the repo root is two levels up. Local only: on Vercel neither the folder nor the script exists.
 */
export const REPO_ROOT = path.resolve(process.cwd(), '../..');
export const FUTURES_CACHE =
  process.env.FUTURES_CACHE_DIR ?? path.join(REPO_ROOT, 'research/backtests/.cache_yahoo_futures');
export const RECORDER_SCRIPT = path.join(REPO_ROOT, 'research/backtests/yahoo_futures_history.py');

export async function readManifest(dir: string): Promise<CacheManifest | null> {
  try {
    return JSON.parse(await fs.readFile(path.join(FUTURES_CACHE, dir, 'manifest.json'), 'utf8')) as CacheManifest;
  } catch {
    return null;
  }
}
