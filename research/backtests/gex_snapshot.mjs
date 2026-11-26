#!/usr/bin/env node
/**
 * Gamma snapshot recorder (phase 5 of research/specs/ar-model-chart-strategy-plan.md).
 *
 * No free source keeps a history of gamma levels, so they have to be recorded going forward. Each run reads the
 * option chain the chart's Gamma Levels indicator reads, with the SAME code, and appends one JSON line to
 * research/backtests/gamma_snapshots/<SYMBOL>.jsonl. The AR strategy (chart and `pnpm bt:ar`) uses, at each candle,
 * the newest snapshot read before that candle closed.
 *   - US (NQ=F, ES=F, …): the index/ETF proxy's chain from Yahoo, ~15 min delayed (apps/web/lib/tv/gex-us.ts).
 *   - NSE (NIFTY1!.NFO, BANKNIFTY1!.NFO, ^NSEI, …): the index's chain from Upstox, live (apps/web/lib/tv/gex-nse.ts).
 *     Needs UPSTOX_ANALYTICS_TOKEN (read from the environment, else apps/web/.env.local, else the repo .env).
 *
 *   pnpm bt:gex                          NQ=F, every expiry within 45 days (the indicator's default)
 *   pnpm bt:gex:nifty                    NIFTY futures (same as --symbol 'NIFTY1!.NFO'; quote the ! in zsh)
 *   pnpm bt:gex --symbol NQ=F --symbol 'NIFTY1!.NFO'
 *   pnpm bt:gex --symbol ES=F --days 30
 *   pnpm bt:gex --show --symbol 'NIFTY1!.NFO'   print what is recorded, record nothing
 *
 * When to run: US, once a day before New York opens (08:30–09:15 ET), after open interest updates overnight. NSE
 * open interest moves during the session, so several times a day (e.g. 09:20, 11:00, 13:00, 15:00 IST). More
 * often is fine (a run within 10 minutes of the last one is skipped). The files are small and cannot be re-fetched
 * later, so they are NOT git-ignored: commit them.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { gammaLevels, gammaRegime, parseSnapshots, snapFromResponse, snapshotFileName } from '../../apps/web/lib/tv/gamma-history.ts';
import { isNseSymbol, nseLevels } from '../../apps/web/lib/tv/gex-nse.ts';
import { usLevels } from '../../apps/web/lib/tv/gex-us.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SNAP_DIR = path.join(HERE, 'gamma_snapshots');
const MIN_GAP = 600;

const { values } = parseArgs({
  options: {
    symbol: { type: 'string', multiple: true, default: ['NQ=F'] },
    days: { type: 'string', default: '45' },
    convention: { type: 'string', default: 'dealer_long_calls' },
    show: { type: 'boolean', default: false },
  },
});
const days = Number(values.days);

/** The Upstox token for NSE chains: the environment, else the web app's .env.local, else the repo .env. Never printed. */
function upstoxToken() {
  for (const f of [path.join(HERE, '../../apps/web/.env.local'), path.join(HERE, '../../.env')]) {
    if (process.env.UPSTOX_ANALYTICS_TOKEN) break;
    try {
      process.loadEnvFile(f);
    } catch {
      /* no such file */
    }
  }
  return process.env.UPSTOX_ANALYTICS_TOKEN;
}

const fmt = (t) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 16) + 'Z';
const describe = (s, price) => {
  const lv = gammaLevels(s)
    .map((l) => `${l.name} ${l.price.toFixed(0)}`)
    .join(' · ');
  const g = price ? gammaRegime(s, price) : null;
  return `${fmt(s.asOf)}  ${s.proxy ?? s.symbol} ${s.spot.toFixed(2)} × ${s.scale.toFixed(3)}  ${lv}${g ? `  → ${g.regime} gamma at ${price.toFixed(2)}` : ''}`;
};

let failed = 0;
for (const symbol of values.symbol) {
  const name = snapshotFileName(symbol);
  if (!name) {
    console.error(`${symbol}: not a plain ticker`);
    failed++;
    continue;
  }
  const file = path.join(SNAP_DIR, name);
  const nse = isNseSymbol(symbol);
  const have = fs.existsSync(file) ? parseSnapshots(fs.readFileSync(file, 'utf8')) : [];
  if (values.show) {
    console.log(`${symbol}: ${have.length} snapshot(s) in ${path.relative(process.cwd(), file)}`);
    for (const s of have) console.log(`  ${describe(s)}`);
    continue;
  }
  const last = have[have.length - 1];
  if (last && Date.now() / 1000 - last.asOf < MIN_GAP) {
    console.log(`${symbol}: last snapshot ${fmt(last.asOf)} is under ${MIN_GAP / 60} minutes old; skipped`);
    continue;
  }
  let res;
  try {
    if (nse) {
      const token = upstoxToken();
      if (!token) throw new Error('UPSTOX_ANALYTICS_TOKEN is not set (environment, apps/web/.env.local or .env)');
      res = await nseLevels(symbol, days, values.convention, token);
    } else res = await usLevels(symbol, days, values.convention);
  } catch (err) {
    console.error(`${symbol}: ${err instanceof Error ? err.message : err}`);
    failed++;
    continue;
  }
  const snap = snapFromResponse(res, days, symbol);
  if (!snap || !Number.isFinite(snap.scale)) {
    console.error(`${symbol}: no levels (${res.reason ?? 'no futures price to scale the index/ETF levels'})`);
    failed++;
    continue;
  }
  fs.mkdirSync(SNAP_DIR, { recursive: true });
  fs.appendFileSync(file, JSON.stringify(snap) + '\n');
  console.log(`${symbol}: recorded (${have.length + 1} in ${path.relative(process.cwd(), file)})`);
  console.log(`  ${describe(snap, res.futurePrice)}`);
}
process.exit(failed ? 1 : 0);
