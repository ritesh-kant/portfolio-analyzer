#!/usr/bin/env node
/**
 * Gamma snapshot recorder (phase 5 of research/specs/ar-model-chart-strategy-plan.md).
 *
 * No free source keeps a history of gamma levels, so they have to be recorded going forward. Each run reads the
 * option chain the chart's Gamma Levels indicator reads (QQQ for NQ, from Yahoo, ~15 min delayed; the SAME code,
 * apps/web/lib/tv/gex-us.ts) and appends one JSON line to research/backtests/gamma_snapshots/<SYMBOL>.jsonl.
 * The AR strategy (chart and `pnpm bt:ar`) uses, at each candle, the newest snapshot read before that candle closed.
 *
 *   pnpm bt:gex                      NQ=F, every expiry within 45 days (the indicator's default)
 *   pnpm bt:gex --symbol ES=F --days 30
 *   pnpm bt:gex --show               print what is recorded, record nothing
 *
 * When to run: once a day before New York opens (08:30–09:15 ET), after open interest updates overnight. More
 * often is fine (a run within 10 minutes of the last one is skipped). The file is small and cannot be re-fetched
 * later, so it is NOT git-ignored: commit it.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { cacheSymbolDir } from '../../apps/web/lib/tv/bar-cache.ts';
import { gammaLevels, gammaRegime, parseSnapshots, snapFromResponse } from '../../apps/web/lib/tv/gamma-history.ts';
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
  const dir = cacheSymbolDir(symbol);
  if (!dir) {
    console.error(`${symbol}: not a plain ticker`);
    failed++;
    continue;
  }
  const file = path.join(SNAP_DIR, `${dir}.jsonl`);
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
    res = await usLevels(symbol, days, values.convention);
  } catch (err) {
    console.error(`${symbol}: ${err instanceof Error ? err.message : err}`);
    failed++;
    continue;
  }
  const snap = snapFromResponse(res, days);
  if (!snap || !Number.isFinite(snap.scale)) {
    console.error(`${symbol}: no levels (${res.reason ?? 'no futures price to scale the proxy'})`);
    failed++;
    continue;
  }
  fs.mkdirSync(SNAP_DIR, { recursive: true });
  fs.appendFileSync(file, JSON.stringify(snap) + '\n');
  console.log(`${symbol}: recorded (${have.length + 1} in ${path.relative(process.cwd(), file)})`);
  console.log(`  ${describe(snap, res.futurePrice)}`);
}
process.exit(failed ? 1 : 0);
