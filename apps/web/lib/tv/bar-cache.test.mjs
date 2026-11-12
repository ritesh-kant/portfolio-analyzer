import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cacheInterval, cacheSymbolDir, monthsBetween, parseCacheCsv, unionBars } from './bar-cache.ts';

const bar = (time, close, volume = 1) => ({ time, open: close, high: close, low: close, close, volume });

test('cache folder and interval names match the recorder', () => {
  assert.equal(cacheSymbolDir('NQ=F'), 'NQ_F');
  assert.equal(cacheSymbolDir('mnq=f'), 'MNQ_F');
  assert.equal(cacheSymbolDir('../etc'), null);
  assert.equal(cacheInterval('1h'), '60m');
  assert.equal(cacheInterval('60m'), '60m');
  assert.equal(cacheInterval('30m'), null);
});

test('monthsBetween lists every UTC month the span touches, across a year end', () => {
  const t = (s) => Date.parse(s) / 1000;
  assert.deepEqual(monthsBetween(t('2025-11-30T23:00:00Z'), t('2026-02-01T00:00:00Z')), [
    '2025-11',
    '2025-12',
    '2026-01',
    '2026-02',
  ]);
  assert.deepEqual(monthsBetween(t('2026-11-05T00:00:00Z'), t('2026-11-12T00:00:00Z')), ['2026-11']);
});

test('parseCacheCsv reads the recorder format and skips bad rows', () => {
  const csv = 'time,open,high,low,close,volume\n1790000000,10.5,11,10,10.75,42\nbad,row\n1790000060,10.75,10.75,10.5,10.5,0\n';
  assert.deepEqual(parseCacheCsv(csv), [
    { time: 1790000000, open: 10.5, high: 11, low: 10, close: 10.75, volume: 42 },
    { time: 1790000060, open: 10.75, high: 10.75, low: 10.5, close: 10.5, volume: 0 },
  ]);
  assert.deepEqual(parseCacheCsv('nope\n1,2'), []);
});

test('unionBars keeps cached history, lets live bars win, and keeps cached bars in live holes', () => {
  const cached = [bar(60, 1), bar(120, 2), bar(180, 3), bar(240, 4)];
  const live = [bar(180, 30), bar(300, 5)]; // 240 missing from Yahoo's answer
  assert.deepEqual(
    unionBars(cached, live).map((b) => [b.time, b.close]),
    [
      [60, 1],
      [120, 2],
      [180, 30],
      [240, 4],
      [300, 5],
    ],
  );
  assert.equal(unionBars([], live), live);
});

test('historySummary turns the manifest into the chip status and warns before 1-minute bars are lost', async () => {
  const { historySummary, isRecordableFuture } = await import('./bar-cache.ts');
  const day = 86_400_000;
  const now = Date.parse('2026-11-12T12:00:00Z');
  const manifest = (runDaysAgo) => ({
    intervals: {
      '1m': { first: '2026-10-14T05:32:00+00:00', last_run: new Date(now - runDaysAgo * day).toISOString() },
      '5m': { first: '2026-09-03T04:05:00+00:00', last_run: new Date(now - runDaysAgo * day - 1000).toISOString() },
    },
  });
  const fresh = historySummary(manifest(1), now);
  assert.deepEqual(fresh.since, { '1m': '2026-10-14', '5m': '2026-09-03' });
  assert.equal(fresh.level, 'ok');
  assert.equal(Math.round(fresh.ageDays), 1);
  assert.equal(historySummary(manifest(20), now).level, 'soon');
  assert.equal(historySummary(manifest(26), now).level, 'late');
  const none = historySummary(null, now);
  assert.equal(none.empty, true);
  assert.equal(none.level, 'late');
  assert.equal(none.lastRun, null);
  assert.ok(isRecordableFuture('NQ=F') && isRecordableFuture('mgc=f'));
  assert.ok(!isRecordableFuture('AAPL') && !isRecordableFuture('NIFTY1!.NFO') && !isRecordableFuture('../x=F'));
});
