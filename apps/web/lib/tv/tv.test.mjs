import assert from 'node:assert/strict';
import { test } from 'node:test';
import { aggregateBars, heikinAshi, mergeBars, sessionDay, wallClock } from './timeframes.ts';
import { atr, ema, macd, relativeVolume, rsi, sma, vwap } from './indicators.ts';
import { autoStep, buildFootprints, niceStep, stepLadder } from './footprint.ts';
import { makeTimeAxis } from './drawings.ts';
import { dragPositionHandle, editPositionLevels, positionPts, positionStats } from './position.ts';
import { aliasHits } from './aliases.ts';
import { completedCount, endAfter, nextEnd } from './replay.ts';
import { matchScore, rankHits, searchQueries } from './search-rank.ts';
import { extendedMask, runs, sessionBreaks, sessionFor } from './sessions.ts';
import { buildProfile } from './volume-profile.ts';
import { cotItems, metalRoot, parseBlsCalendar, parseFedRss, zonedInstant } from './macro.ts';
import { groupByBar } from './news-markers.ts';
import { barContaining, deltaDepthDays, findDeltaDivergences, inDeltaView, DELTA_VIEW_DEFAULTS } from './delta-divergence.ts';
import { foldTrailingTick, intervalSecs } from './clean-bars.ts';

const IST = 'Asia/Kolkata';
// 2026-10-19 09:15 IST
const open = Date.parse('2026-10-19T09:15:00+05:30') / 1000;
const minute = (i, o, h, l, c, v) => ({
  time: open + i * 60,
  open: o,
  high: h,
  low: l,
  close: c,
  volume: v,
});

test('wallClock renders the exchange clock as if it were UTC', () => {
  const wall = wallClock(open, IST);
  assert.equal(new Date(wall * 1000).toISOString(), '2026-10-19T09:15:00.000Z');
});

test('aggregateBars anchors buckets to the session open and sums volume', () => {
  const bars = Array.from({ length: 7 }, (_, i) =>
    minute(i, 100 + i, 102 + i, 99 + i, 101 + i, 10),
  );
  const out = aggregateBars(bars, 180, IST);
  assert.equal(out.length, 3);
  assert.equal(out[0].time, open);
  assert.equal(out[0].open, 100);
  assert.equal(out[0].high, 104);
  assert.equal(out[0].low, 99);
  assert.equal(out[0].close, 103);
  assert.equal(out[0].volume, 30);
  assert.equal(out[2].volume, 10);
});

test('aggregateBars restarts at each session day', () => {
  const day2 = open + 86400;
  const bars = [
    minute(0, 1, 1, 1, 1, 1),
    { time: day2, open: 2, high: 2, low: 2, close: 2, volume: 1 },
  ];
  assert.equal(aggregateBars(bars, 3600, IST).length, 2);
});

test('mergeBars replaces the overlapping tail and appends newer bars', () => {
  const a = [minute(0, 1, 1, 1, 1, 1), minute(1, 2, 2, 2, 2, 2)];
  const fresh = [minute(1, 9, 9, 9, 9, 9), minute(2, 3, 3, 3, 3, 3)];
  const merged = mergeBars(a, fresh);
  assert.deepEqual(
    merged.map((b) => b.open),
    [1, 9, 3],
  );
});

test('heikinAshi first candle averages open/close', () => {
  const ha = heikinAshi([minute(0, 10, 12, 9, 11, 1)]);
  assert.equal(ha[0].open, 10.5);
  assert.equal(ha[0].close, 10.5);
});

test('sma and ema agree on a flat series and seed with the SMA', () => {
  const flat = Array(10).fill(5);
  assert.equal(sma(flat, 3)[2], 5);
  assert.equal(ema(flat, 3)[2], 5);
  assert.equal(ema(flat, 3)[1], null);
  assert.equal(ema([1, 2, 3, 4], 3)[2], 2);
});

test('rsi is 100 on a monotone rise and 0 on a monotone fall', () => {
  const up = Array.from({ length: 30 }, (_, i) => i);
  const down = [...up].reverse();
  assert.equal(rsi(up, 14).at(-1), 100);
  assert.equal(rsi(down, 14).at(-1), 0);
});

test('macd histogram is macd minus signal and starts after the slow window', () => {
  const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 5) * 5);
  const m = macd(closes);
  assert.equal(m.macd[24], null);
  assert.notEqual(m.macd[25], null);
  const i = 59;
  assert.ok(Math.abs(m.hist[i] - (m.macd[i] - m.signal[i])) < 1e-12);
});

test('vwap resets at the session boundary', () => {
  const bars = [
    minute(0, 10, 10, 10, 10, 100),
    { time: open + 86400, open: 20, high: 20, low: 20, close: 20, volume: 50 },
  ];
  const v = vwap(bars, (t) => sessionDay(t, IST));
  assert.equal(v[0], 10);
  assert.equal(v[1], 20);
});

test('relativeVolume divides by the mean of the previous N candles', () => {
  const r = relativeVolume([10, 10, 10, 30], 3);
  assert.equal(r[3], 3);
  assert.equal(r[2], null);
});

test('niceStep and stepLadder stay on 1-2-2.5-5 rungs', () => {
  assert.equal(niceStep(0.013), 0.02);
  assert.equal(niceStep(0.04), 0.05);
  assert.equal(stepLadder(0.05, 1), 0.1);
  assert.equal(stepLadder(0.05, -1), 0.025);
  assert.ok(autoStep([minute(0, 100, 101, 99, 100, 1)]) > 0);
});

test("footprint conserves each candle's volume and sums delta correctly", () => {
  const subs = [
    minute(0, 100, 100.4, 99.8, 100.3, 1000),
    minute(1, 100.3, 100.6, 100.1, 100.5, 500),
    minute(2, 100.5, 100.5, 100.5, 100.5, 0),
  ];
  const chart = [{ time: open, open: 100, high: 100.6, low: 99.8, close: 100.5, volume: 1500 }];
  const [fp] = buildFootprints(chart, subs, 180, 0.1);
  assert.ok(fp);
  assert.ok(Math.abs(fp.total - 1500) < 1e-6);
  const asks = fp.levels.reduce((s, l) => s + l.ask, 0);
  const bids = fp.levels.reduce((s, l) => s + l.bid, 0);
  assert.ok(Math.abs(fp.delta - (asks - bids)) < 1e-6);
  assert.ok(
    fp.levels[fp.poc].ask + fp.levels[fp.poc].bid >=
      Math.max(...fp.levels.map((l) => l.ask + l.bid)) - 1e-9,
  );
});

test('footprint candles with no sub-bars are null', () => {
  const chart = [{ time: open, open: 1, high: 1, low: 1, close: 1, volume: 1 }];
  assert.deepEqual(buildFootprints(chart, [], 60, 0.1), [null]);
});

test('time axis round-trips and extrapolates past both ends', () => {
  const axis = makeTimeAxis([1000, 1060, 1120, 5000], 60);
  assert.equal(axis.toLogical(1060), 1);
  assert.equal(axis.toLogical(1090), 1.5);
  assert.equal(axis.toTime(1.5), 1090);
  assert.equal(axis.toLogical(5000 + 120), 3 + 2);
  assert.equal(axis.toTime(-1), 940);
});

test('atr uses true range incl. gaps and Wilder smoothing', () => {
  const b = (h, l, c) => ({ time: 0, open: c, high: h, low: l, close: c, volume: 1 });
  const bars = [b(10, 8, 9), b(11, 9, 10), b(15, 14, 14)]; // 3rd bar gaps up: TR = 15-10 = 5
  const out = atr(bars, 2);
  assert.equal(out[0], null);
  assert.equal(out[1], 2); // mean(2, 2)
  assert.equal(out[2], 3.5); // (2*1 + 5)/2
});

test('news attaches to the containing candle, else the next one', () => {
  const times = [1000, 1300, 1600, 5000]; // 5-minute candles with an overnight gap
  const at = (t) => ({
    headline: 'h',
    publisher: 'p',
    url: null,
    published_at: new Date(t * 1000).toISOString(),
    kind: 'news',
    text: null,
  });
  const m = groupByBar([at(1350), at(1950), at(3000), at(5100), at(900)], times, 300);
  assert.equal(m.get(1).length, 1); // inside candle 1
  assert.equal(m.get(3).length, 3); // 1950 + 3000 fall in the gap -> next candle; 5100 is inside the last
  assert.equal(m.has(0), false); // older than first candle is dropped
});

test('TradingView-style tickers map to the closest Yahoo series', () => {
  assert.equal(aliasHits('XAUUSD')[0].symbol, 'GC=F');
  assert.equal(aliasHits('oanda:xauusd')[0].symbol, 'GC=F');
  assert.equal(aliasHits('EURUSD')[0].symbol, 'EURUSD=X');
  assert.equal(aliasHits('btcusdt')[0].symbol, 'BTC-USD');
  assert.deepEqual(aliasHits('RELIANCE'), []);
  assert.deepEqual(aliasHits('USDUSD'), []);
});

test('replay shows only candles complete at the cut-off', () => {
  const times = [0, 300, 600, 900];
  assert.equal(completedCount(times, 300, 600), 2); // 0 and 300 are done, 600 is just starting
  assert.equal(completedCount(times, 300, 899), 2);
  assert.equal(completedCount(times, 300, 900), 3);
  assert.equal(completedCount(times, 300, 5000), 4);
  assert.equal(completedCount(times, 300, -1), 0);
});

test('replay on a coarser timeframe hides the unfinished candle', () => {
  const hourly = [0, 3600, 7200];
  assert.equal(completedCount(hourly, 3600, 5400), 1); // cut-off 90 min in: the 2nd hour is incomplete
  assert.equal(completedCount(hourly, 3600, 7200), 2);
});

test('a session-final candle shorter than the interval completes once the next session starts', () => {
  const times = [0, 3600, 100000]; // 2nd candle is the last of its day
  assert.equal(completedCount(times, 14400, 3700), 1); // candle 0 ended early: candle 1 has begun
  assert.equal(completedCount(times, 14400, 3599), 0);
  assert.equal(completedCount(times, 14400, 100000), 2);
});

test('replay steps one candle at a time and ends', () => {
  const times = [0, 300, 600];
  const start = endAfter(times, 300, 0);
  assert.equal(start, 300);
  assert.equal(nextEnd(times, 300, start), 600);
  assert.equal(nextEnd(times, 300, 600), 900);
  assert.equal(nextEnd(times, 300, 900), null);
  assert.equal(endAfter(times, 300, 99), 900); // clamps
});

test('multi-word search ranks hits by words matched, expanding exchange nicknames', () => {
  const gc = {
    symbol: 'GC=F',
    name: 'Gold Dec 26',
    exchange: 'New York Commodity Exchange',
    type: 'FUTURE',
  };
  const gld = { symbol: 'GLD', name: 'SPDR Gold Shares', exchange: 'NYSEArca', type: 'ETF' };
  assert.deepEqual(searchQueries('gold comex'), ['gold comex', 'gold', 'comex']);
  assert.deepEqual(searchQueries('infosys'), ['infosys']);
  assert.equal(matchScore('gold comex', gc), 2);
  assert.equal(matchScore('gold comex', gld), 1);
  assert.deepEqual(
    rankHits('gold comex', [[gld], [gld, gc]]).map((h) => h.symbol),
    ['GC=F', 'GLD'],
  );
});

test('futures roots resolve like TradingView lists them', () => {
  const gc = aliasHits('GC')[0];
  assert.equal(gc.symbol, 'GC=F');
  assert.equal(gc.display, 'GC');
  assert.equal(gc.name, 'Gold Futures');
  assert.equal(gc.exchange, 'COMEX');
  assert.equal(aliasHits('gc1!')[0].symbol, 'GC=F');
  assert.equal(aliasHits('CL')[0].symbol, 'CL=F');
  assert.deepEqual(
    aliasHits('GCT').filter((h) => h.symbol === 'GC=F'),
    [],
  );
});

test('regular-hours windows: gold futures use COMEX RTH, NSE has none', () => {
  const gc = sessionFor({ symbol: 'GC=F', instrumentType: 'FUTURE', timezone: 'America/New_York' });
  assert.deepEqual(gc, { start: 8 * 60 + 20, end: 13 * 60 + 30 });
  assert.deepEqual(
    sessionFor({ symbol: 'AAPL', instrumentType: 'EQUITY', timezone: 'America/New_York' }),
    { start: 570, end: 960 },
  );
  assert.equal(
    sessionFor({ symbol: 'TCS.NS', instrumentType: 'EQUITY', timezone: 'Asia/Kolkata' }),
    null,
  );
  assert.equal(
    sessionFor({ symbol: 'ZZ=F', instrumentType: 'FUTURE', timezone: 'America/New_York' }),
    null,
  );
});

test('extended-hours mask and runs', () => {
  const day = 86400 * 20000;
  const at = (h, m = 0) => day + h * 3600 + m * 60; // wall-clock seconds
  const wall = [at(7), at(8, 10), at(8, 20), at(12), at(13, 30), at(15)];
  const mask = extendedMask(wall, { start: 8 * 60 + 20, end: 13 * 60 + 30 });
  assert.deepEqual(mask, [true, true, false, false, true, true]);
  assert.deepEqual(runs(mask), [
    [0, 1],
    [4, 5],
  ]);
  assert.deepEqual(runs([false, false]), []);
});

test('session breaks sit on the first candle after a gap', () => {
  const t = [0, 300, 600, 4200, 4500, 100000];
  assert.deepEqual(sessionBreaks(t, 300), [3, 5]);
});

test('volume profile conserves volume and finds the point of control', () => {
  const b = (o, h, l, c, v) => ({ time: 0, open: o, high: h, low: l, close: c, volume: v });
  // heavy volume tightly around 100, light volume spread up to 110
  const bars = [
    b(100, 101, 99, 100.5, 9000),
    b(100.5, 110, 100, 109, 1000),
    b(100, 100.5, 99.5, 100, 5000),
  ];
  const p = buildProfile(bars, 0, 2, 11);
  assert.ok(Math.abs(p.total - 15000) < 1e-6);
  assert.equal(p.rows.length, 11);
  assert.ok(p.pocPrice > 99 && p.pocPrice < 101.5);
  assert.equal(p.lo, 99);
  assert.equal(p.hi, 110);
  const held = p.rows.slice(p.vaLo, p.vaHi + 1).reduce((s, r) => s + r.up + r.down, 0);
  assert.ok(held >= 0.7 * p.total - 1e-6);
  assert.ok(p.vaLo <= p.poc && p.poc <= p.vaHi);
  assert.ok(p.vah > p.val);
});

test('volume profile splits up/down volume and clamps the range', () => {
  const b = (o, c, v) => ({
    time: 0,
    open: o,
    high: Math.max(o, c) + 1,
    low: Math.min(o, c) - 1,
    close: c,
    volume: v,
  });
  const bars = [b(100, 102, 100), b(102, 100, 300)];
  const p = buildProfile(bars, -5, 99, 4);
  const up = p.rows.reduce((s, r) => s + r.up, 0);
  const down = p.rows.reduce((s, r) => s + r.down, 0);
  assert.ok(Math.abs(up - 100) < 1e-9 && Math.abs(down - 300) < 1e-9);
  assert.equal(buildProfile([], 0, 1), null);
});

test('volume profile of one flat candle is a single row', () => {
  const p = buildProfile([{ time: 0, open: 50, high: 50, low: 50, close: 50, volume: 70 }], 0, 0);
  assert.equal(p.rows.length, 1);
  assert.equal(p.total, 70);
});

test('macro news applies to precious-metal futures only', () => {
  assert.equal(metalRoot('GC=F'), 'GC');
  assert.equal(metalRoot('si=f'), 'SI');
  assert.equal(metalRoot('CL=F'), null);
  assert.equal(metalRoot('GLD'), null);
});

test('zonedInstant is DST-aware', () => {
  assert.equal(
    new Date(zonedInstant('2026-07-10', '08:30', 'America/New_York')).toISOString(),
    '2026-07-10T12:30:00.000Z',
  );
  assert.equal(
    new Date(zonedInstant('2026-12-04', '08:30', 'America/New_York')).toISOString(),
    '2026-12-04T13:30:00.000Z',
  );
});

test('Fed RSS parses titles, links and publish times', () => {
  const xml = `<rss><channel><item><title><![CDATA[Federal Reserve issues FOMC statement]]></title>
    <link><![CDATA[https://www.federalreserve.gov/x.htm]]></link><pubDate><![CDATA[Wed, 16 Sep 2026 18:00:00 GMT]]></pubDate></item>
    <item><title>Minutes of the Board&#39;s meetings</title><link>https://f/y</link><pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate></item></channel></rss>`;
  const items = parseFedRss(xml);
  assert.equal(items.length, 2);
  assert.equal(items[0].headline, 'Federal Reserve issues FOMC statement');
  assert.equal(items[0].published_at, '2026-09-16T18:00:00.000Z');
  assert.equal(items[1].headline, "Minutes of the Board's meetings");
});

test('BLS calendar keeps jobs, CPI and PPI at their Eastern release time', () => {
  const ics = [
    'BEGIN:VEVENT',
    'DTSTART;TZID=US-Eastern:20261014T083000',
    'SUMMARY:Consumer Price Index',
    'END:VEVENT',
    'BEGIN:VEVENT',
    'DTSTART;TZID=US-Eastern:20261002T083000',
    'SUMMARY:Employment Situation',
    'END:VEVENT',
    'BEGIN:VEVENT',
    'DTSTART;TZID=US-Eastern:20260428T100000',
    'SUMMARY:Employment Situation of Veterans',
    'END:VEVENT',
    'BEGIN:VEVENT',
    'DTSTART;TZID=US-Eastern:20260107T100000',
    'SUMMARY:Job Openings and Labor Turnover Survey',
    'END:VEVENT',
  ].join('\r\n');
  const items = parseBlsCalendar(ics);
  assert.deepEqual(
    items.map((i) => i.headline),
    ['Jobs report (Employment Situation) released', 'Consumer Price Index (CPI) released'],
  );
  assert.equal(items[1].published_at, '2026-10-14T12:30:00.000Z');
});

test('COT item reports net positioning and is stamped at Friday 15:30 ET', () => {
  const [item] = cotItems(
    [
      {
        report_date_as_yyyy_mm_dd: '2026-10-13T00:00:00.000',
        m_money_positions_long_all: '124747',
        m_money_positions_short_all: '17658',
        change_in_m_money_long_all: '-2354',
        change_in_m_money_short_all: '1544',
      },
    ],
    'gold',
    'https://cftc/x',
  );
  assert.match(item.headline, /net long in gold: 107,089 contracts \(−3,898 on the week\)/);
  assert.equal(item.published_at, '2026-10-16T19:30:00.000Z');
});

const cndl = (i, o, h, l, c, v) => minute(i * 15, o, h, l, c, v);
const quiet = (n) => Array.from({ length: n }, (_, i) => cndl(i, 100, 100.4, 99.6, 100.1, 1000));
const cfgD = { wick: 40, dominance: 2, delta: 5, rvol: 1, within: 3, missed: false, closeBreak: true };

test('absorption: upper wick + positive delta, then a red candle with negative delta breaking the low', () => {
  const c = quiet(12);
  c.push(cndl(12, 100, 102, 99.8, 100.2, 3000)); // upper wick 1.8 of 2.2 (82%), delta +
  c.push(cndl(13, 100.1, 100.3, 99.0, 99.2, 2000)); // red, low 99.0 < 99.8, delta -
  const d = [...Array(12).fill(0), 600, -300];
  const got = findDeltaDivergences(c, d, cfgD);
  assert.equal(got.length, 1);
  assert.deepEqual([got[0].index, got[0].kind, got[0].status, got[0].confirmIndex], [12, 'buyers', 'confirmed', 13]);
  assert.ok(Math.abs(got[0].wick - (1.8 / 2.2) * 100) < 1e-6);
  assert.equal(got[0].high, 102);
  assert.equal(got[0].coarse, false);
  // a coarse delta on either the signal or the confirming candle is carried through
  const coarseSig = findDeltaDivergences(c, d, cfgD, [...Array(12).fill(false), true, false]);
  const coarseConf = findDeltaDivergences(c, d, cfgD, [...Array(13).fill(false), true]);
  assert.deepEqual([coarseSig[0].coarse, coarseConf[0].coarse], [true, true]);
  assert.equal(got[0].low, 99.8);
});

test('absorption: lower wick + negative delta, then a green candle with positive delta breaking the high', () => {
  const c = quiet(12);
  c.push(cndl(12, 100, 100.2, 98, 99.9, 3000)); // lower wick 1.9 of 2.2, delta -
  c.push(cndl(13, 99.9, 101, 99.8, 100.9, 2500)); // green, high 101 > 100.2, delta +
  const got = findDeltaDivergences(c, [...Array(12).fill(0), -700, 500], cfgD);
  assert.deepEqual([got[0].kind, got[0].status, got[0].confirmIndex], ['sellers', 'confirmed', 13]);
});

test('absorption: delta on the wrong side, small wicks, thin delta and quiet volume do not qualify', () => {
  const base = quiet(12);
  const zero = Array(12).fill(0);
  // upper wick but NEGATIVE delta: aggressive sellers, nothing trapped
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 100, 102, 99.8, 100.2, 3000)], [...zero, -600], cfgD), []);
  // red candle with positive delta but no dominant wick (the old body-vs-delta rule would have flagged it)
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 101, 101.05, 99, 99.1, 3000)], [...zero, 600], cfgD), []);
  // wicks at both ends: 42% above, 31% below, 27% body (the 21 Oct 07:15 gold candle) is not one-sided
  const twoSided = cndl(12, 100.5, 102.3, 99.7, 101.2, 3000);
  assert.deepEqual(findDeltaDivergences([...base, twoSided], [...zero, 600], cfgD), []);
  assert.equal(findDeltaDivergences([...base, twoSided], [...zero, 600], { ...cfgD, dominance: 1 }).length, 1);
  // wick too small a share of the range
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 100, 101.1, 99.0, 100.7, 3000)], [...zero, 600], cfgD), []);
  // |delta| only 0.1% of volume
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 100, 102, 99.8, 100.2, 3000)], [...zero, 3], cfgD), []);
  // volume no higher than usual
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 100, 102, 99.8, 100.2, 900)], [...zero, 50], cfgD), []);
  // no footprint under it
  assert.deepEqual(findDeltaDivergences([...base, cndl(12, 100, 102, 99.8, 100.2, 3000)], [...zero, null], cfgD), []);
});

test('absorption: a wick through the level is not a break unless closeBreak is off', () => {
  const c = quiet(12);
  c.push(cndl(12, 100, 102, 99.8, 100.2, 3000)); // upper wick, low 99.8
  c.push(cndl(13, 100.3, 100.4, 99.0, 100.0, 2000)); // red, wicks to 99.0 but CLOSES at 100.0, back above 99.8
  c.push(cndl(14, 100.0, 100.2, 99.9, 100.1, 1000));
  c.push(cndl(15, 100.1, 100.2, 99.9, 100.0, 1000));
  const d = [...Array(12).fill(0), 600, -300, 0, 0];
  const first = (cfg) => findDeltaDivergences(c, d, cfg).filter((x) => x.index === 12).map((x) => [x.status, x.confirmIndex]);
  assert.deepEqual(first(cfgD), [['missed', null]]);
  assert.deepEqual(first({ ...cfgD, closeBreak: false }), [['confirmed', 13]]);
  // the same candle closing below the level does confirm
  const closed = c.map((x, i) => (i === 13 ? cndl(13, 100.3, 100.4, 99.0, 99.2, 2000) : x));
  assert.equal(findDeltaDivergences(closed, d, cfgD).find((x) => x.index === 12).status, 'confirmed');
});

test('absorption: waiting, no follow-through, and failed setups', () => {
  const sig = () => [...quiet(12), cndl(12, 100, 102, 99.8, 100.2, 3000)];
  const z = Array(12).fill(0);
  // the signal is the newest candle: still waiting
  assert.equal(findDeltaDivergences(sig(), [...z, 600], cfgD)[0].status, 'pending');
  // three further candles that never break the low: no follow-through
  const calm = [...sig(), cndl(13, 100.2, 100.5, 100, 100.3, 1000), cndl(14, 100.3, 100.5, 100, 100.2, 1000), cndl(15, 100.2, 100.5, 100, 100.4, 1000)];
  assert.equal(findDeltaDivergences(calm, [...z, 600, 0, 0, 0], cfgD)[0].status, 'missed');
  // breaks the low but with a green body or positive delta: not a confirmation
  const weak = [...sig(), cndl(13, 99.5, 100.4, 99.0, 100.3, 1000)];
  assert.equal(findDeltaDivergences(weak, [...z, 600, 200], cfgD)[0].status, 'pending');
  // closes above the wick tip first: the trapped buyers were right after all
  const failed = [...sig(), cndl(13, 100.2, 102.6, 100.1, 102.4, 2000), cndl(14, 102.4, 102.5, 98.0, 98.5, 2000)];
  const got = findDeltaDivergences(failed, [...z, 600, 400, -900], cfgD)[0];
  assert.deepEqual([got.status, got.confirmIndex], ['missed', null]);
  // only candles up to the confirmation matter: later ones never change an earlier verdict
  const conf = [...sig(), cndl(13, 100.1, 100.3, 99.0, 99.2, 2000)];
  const dd = [...z, 600, -300];
  const before = findDeltaDivergences(conf, dd, cfgD);
  const after = findDeltaDivergences([...conf, cndl(14, 99.2, 105, 99, 105, 9e9)], [...dd, 5000], cfgD);
  assert.deepEqual(after, before);
});

test('barContaining finds the chart bar that holds an instant', () => {
  const times = [0, 900, 1800, 7200];
  assert.equal(barContaining(times, 840, 900), 0);
  assert.equal(barContaining(times, 900, 900), 1);
  assert.equal(barContaining(times, 2699, 900), 2);
  assert.equal(barContaining(times, 2700, 900), -1); // in the gap after the 1800 bar
  assert.equal(barContaining(times, 3600, 3600), 2); // a 1h chart holds the 15m candle's last minute
  assert.equal(barContaining(times, -1, 900), -1);
});

test('autoStep follows ATR(14): steady 1.4 ranges give 14 rows of 0.1, and volatility scales it', () => {
  const calm = Array.from({ length: 30 }, (_, i) => minute(i, 100, 100.7, 99.3, 100, 1000));
  assert.equal(autoStep(calm), 0.1);
  const wild = Array.from({ length: 30 }, (_, i) => minute(i, 100, 107, 93, 100, 1000));
  assert.equal(autoStep(wild), 1);
});

test('foldTrailingTick folds Yahoo\'s zero-volume last-trade bar into the forming candle', () => {
  const b = (t, o, h, l, c, v) => ({ time: t, open: o, high: h, low: l, close: c, volume: v });
  const real = [b(0, 10, 11, 9, 10, 100), b(900, 10, 12, 10, 11, 50)];
  const tick = b(900 + 704, 12.5, 12.5, 12.5, 12.5, 0);
  const got = foldTrailingTick([...real, tick], 900);
  assert.equal(got.length, 2);
  assert.deepEqual(got[1], b(900, 10, 12.5, 10, 12.5, 50));
  // the tick's own timestamp no longer matters: two polls give identical bars
  assert.deepEqual(foldTrailingTick([...real, { ...tick, time: 900 + 790 }], 900), got);
  // a real next candle (starts a full interval later) and daily data are untouched
  const next = [...real, b(1800, 11, 11, 11, 11, 5)];
  assert.equal(foldTrailingTick(next, 900), next);
  assert.equal(foldTrailingTick([...real, tick], null).length, 3);
  assert.equal(intervalSecs('15m'), 900);
  assert.equal(intervalSecs('60m'), 3600);
  assert.equal(intervalSecs('1h'), 3600);
  assert.equal(intervalSecs('1d'), null);
  assert.equal(intervalSecs('1wk'), null);
});

test('inDeltaView: range presets, custom dates and the minimum delta', () => {
  const day = 86400;
  const newest = 100 * day;
  const dayOf = (t) => new Date(t * 1000).toISOString().slice(0, 10);
  const at = (daysAgo, delta = 500) => ({ time: newest - daysAgo * day, delta });
  const v = (o) => ({ ...DELTA_VIEW_DEFAULTS, ...o });
  assert.ok(inDeltaView(at(3), v({ range: '1w' }), newest, dayOf));
  assert.ok(!inDeltaView(at(9), v({ range: '1w' }), newest, dayOf));
  assert.ok(inDeltaView(at(9), v({ range: '2w' }), newest, dayOf));
  assert.ok(!inDeltaView(at(20), v({ range: '2w' }), newest, dayOf));
  assert.ok(inDeltaView(at(28), v({ range: '1m' }), newest, dayOf));
  assert.ok(!inDeltaView(at(45), v({ range: '1m' }), newest, dayOf));
  assert.ok(inDeltaView(at(45), v({ range: '2m' }), newest, dayOf));
  assert.ok(!inDeltaView(at(70), v({ range: '2m' }), newest, dayOf));
  assert.ok(inDeltaView(at(80), v({ range: '3m' }), newest, dayOf));
  assert.ok(!inDeltaView(at(95), v({ range: '3m' }), newest, dayOf));
  assert.ok(inDeltaView(at(95), v({ range: '6m' }), newest, dayOf));
  // |delta| counts either sign; the bound itself is allowed
  assert.ok(inDeltaView(at(1, -500), v({ minDelta: 500 }), newest, dayOf));
  assert.ok(!inDeltaView(at(1, -499), v({ minDelta: 500 }), newest, dayOf));
  assert.ok(inDeltaView(at(1, 80), v({ minDelta: 0 }), newest, dayOf));
  // custom: inclusive on both ends, and either end may be left open
  const a = at(10), b = at(5), c = at(1);
  const custom = v({ range: 'custom', from: dayOf(a.time), to: dayOf(b.time) });
  assert.deepEqual([a, b, c].map((x) => inDeltaView(x, custom, newest, dayOf)), [true, true, false]);
  assert.ok(inDeltaView(c, v({ range: 'custom', from: dayOf(b.time) }), newest, dayOf));
  assert.ok(inDeltaView(a, v({ range: 'custom', to: dayOf(b.time) }), newest, dayOf));
  assert.ok(inDeltaView(a, v({ range: 'custom' }), newest, dayOf));
});

test('deltaDepthDays: 3 months at least, then 6 months or a year, custom reaches its start date', () => {
  const now = Date.UTC(2026, 9, 21) / 1000;
  const v = (o) => ({ ...DELTA_VIEW_DEFAULTS, ...o });
  assert.equal(DELTA_VIEW_DEFAULTS.range, '3m');
  for (const range of ['1w', '2w', '1m', '2m', '3m']) assert.equal(deltaDepthDays(v({ range }), now), 93);
  assert.equal(deltaDepthDays(v({ range: '6m' }), now), 186);
  assert.equal(deltaDepthDays(v({ range: 'custom' }), now), 93);
  assert.equal(deltaDepthDays(v({ range: 'custom', from: '2026-09-01' }), now), 93);
  assert.equal(deltaDepthDays(v({ range: 'custom', from: '2026-05-01' }), now), 186);
  assert.equal(deltaDepthDays(v({ range: 'custom', from: '2025-12-01' }), now), 366);
});

// ── long / short position tool ──────────────────────────────────────────

const pos = (kind, extra = {}) => ({
  id: 'p',
  kind,
  color: '#fff',
  width: 2,
  pts: positionPts(kind, { t: open, p: 100 }, 600, 5, 10),
  ...extra,
});
const pbar = (i, h, l, c) => minute(i, c, h, l, c, 1);

test('positionPts puts target and stop on the correct side', () => {
  const long = pos('long').pts.map((p) => p.p);
  assert.deepEqual(long, [100, 100, 110, 95]);
  const short = pos('short').pts.map((p) => p.p);
  assert.deepEqual(short, [100, 100, 90, 105]);
});

test('positionStats: risk model, ticks and percent', () => {
  const st = positionStats(pos('long'), [], 0.1);
  assert.equal(st.reward, 10);
  assert.equal(st.risk, 5);
  assert.equal(st.rr, 2);
  assert.equal(st.targetTicks, 100);
  assert.equal(st.stopTicks, 50);
  assert.equal(st.targetPct, 10);
  // 1000 account x 25% risk / 5 per unit = 50 units
  assert.equal(st.qty, 50);
  assert.equal(st.status, 'open');
  assert.equal(st.pnl, 0);
  assert.equal(positionStats(pos('long', { account: 10000, lot: 10 }), [], 0.1).qty, 500);
});

test('positionStats: first touch closes the trade; the entry candle itself is ignored', () => {
  const bars = [
    pbar(0, 120, 80, 100), // entry candle: its own extremes do not count
    pbar(1, 104, 99, 103),
    pbar(2, 111, 102, 110), // target
    pbar(3, 100, 90, 92), // later stop is irrelevant
  ];
  const st = positionStats(pos('long'), bars, 0.1);
  assert.equal(st.status, 'target');
  assert.equal(st.pnl, 10 * 50);
  const short = positionStats(pos('short'), bars, 0.1);
  assert.equal(short.status, 'stop'); // bar 2 high 111 >= 105
  assert.equal(short.pnl, -5 * 50);
});

test('positionStats: a candle spanning both levels counts as the stop; open P&L marks to the last close', () => {
  const both = positionStats(pos('long'), [pbar(0, 100, 100, 100), pbar(1, 111, 94, 100)], 0.1);
  assert.equal(both.status, 'stop');
  const open = positionStats(pos('long'), [pbar(0, 100, 100, 100), pbar(1, 103, 99, 102)], 0.1);
  assert.equal(open.status, 'open');
  assert.equal(open.pnl, 2 * 50);
  // candles after the right edge of the box are not part of the trade
  const late = positionStats(pos('long'), [pbar(0, 100, 100, 100), pbar(20, 130, 90, 100)], 0.1);
  assert.equal(late.status, 'open');
});

test('dragPositionHandle keeps the levels on their own side of the entry', () => {
  const d = pos('long');
  const target = dragPositionHandle(d, 2, { t: open, p: 90 });
  assert.ok(target.pts[2].p > 100);
  const stop = dragPositionHandle(d, 3, { t: open, p: 120 });
  assert.ok(stop.pts[3].p < 100);
  const wide = dragPositionHandle(d, 1, { t: open + 5000, p: 777 });
  assert.equal(wide.pts[1].t, open + 5000);
  assert.equal(wide.pts[1].p, 100);
  const entry = dragPositionHandle(d, 0, { t: open - 60, p: 102 });
  assert.deepEqual(
    entry.pts.map((p) => p.p),
    [102, 102, 110, 95],
  );
  assert.equal(entry.pts[2].t, open - 60);
  const shortStop = dragPositionHandle(pos('short'), 3, { t: open, p: 80 });
  assert.ok(shortStop.pts[3].p > 100);
});

test('editPositionLevels rejects levels on the wrong side', () => {
  const d = pos('long');
  assert.equal(editPositionLevels(d, { target: 90 }), d);
  assert.equal(editPositionLevels(d, { target: 120 }).pts[2].p, 120);
  assert.equal(editPositionLevels(d, { stop: Number.NaN }), d);
  assert.equal(editPositionLevels(pos('short'), { stop: 110 }).pts[3].p, 110);
});
