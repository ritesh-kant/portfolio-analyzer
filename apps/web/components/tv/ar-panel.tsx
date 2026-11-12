'use client';

import { useState } from 'react';
import type { AvrDay, Bias, EnvironmentRead, RangeFib } from '../../lib/tv/ar';
import { type ArStrategyResult, type CheckState, STEPS, arSummary } from '../../lib/tv/ar-strategy';

/** What the panel reads from the chart's AR state (tv-chart.tsx `arState`). */
export interface ArPanelState {
  prefer: Bias;
  ladder: { label: string; bias: Bias | null }[];
  env: EnvironmentRead | null;
  fib: RangeFib | null;
  avr: AvrDay[];
  dailyMissing: boolean;
  /** The strategy's run, when "AR Strategy" is on. */
  strat: ArStrategyResult | null;
}

const MARK: Record<CheckState, { glyph: string; color: string }> = {
  pass: { glyph: '✓', color: '#089981' },
  warn: { glyph: '△', color: '#ffb74d' },
  fail: { glyph: '✕', color: '#f23645' },
  wait: { glyph: '…', color: '#787b86' },
};

/** "HH:MM" of a wall-clock timestamp (the chart's exchange-time seconds). */
const hhmm = (wall: number | undefined) => (wall == null ? '' : new Date(wall * 1000).toISOString().slice(11, 16));
const mmdd = (wall: number | undefined) => (wall == null ? '' : new Date(wall * 1000).toISOString().slice(5, 10));

const UP = '#089981';
const DOWN = '#f23645';
const MUTED = '#787b86';
const OPEN_KEY = 'tv:arPanelOpen';

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== '0';
  } catch {
    return true;
  }
}

const biasText: Record<Bias, string> = { bull: 'up', bear: 'down', neutral: 'mixed' };
const biasColor = (b: Bias | null) => (b === 'bull' ? UP : b === 'bear' ? DOWN : MUTED);
const biasArrow = (b: Bias | null) => (b === 'bull' ? '▲' : b === 'bear' ? '▼' : b === 'neutral' ? '◆' : '–');

const ENV: Record<EnvironmentRead['env'], { name: string; color: string; fits: string }> = {
  expansion: {
    name: 'Trending (expansion)',
    color: UP,
    fits: 'Continuation setups work best: buy pullbacks in an uptrend, sell rallies in a downtrend. Fades only at extreme levels.',
  },
  rotation: {
    name: 'Ranging (rotation)',
    color: '#ffb74d',
    fits: 'Fade the edges of the range. Breakouts tend to fail.',
  },
  compression: {
    name: 'Choppy (compression)',
    color: DOWN,
    fits: 'Most setups lose here: small moves, frequent stop-outs. His rule: don’t trade.',
  },
};

const STATUS: Record<RangeFib['status'], { text: string; color: string }> = {
  waiting: { text: 'waiting for price to reach the zone', color: MUTED },
  'in zone': { text: 'price is in the zone now: watch order flow for confirmation', color: '#ffb74d' },
  reacted: { text: 'touched the zone and bounced', color: UP },
  'target hit': { text: 'touched the zone, then reached the target', color: UP },
  invalidated: { text: 'a candle closed past 0.886: idea cancelled, expect the 1.1 level', color: DOWN },
  extended: { text: 'price ran past the swing before retracing: a new leg is forming', color: MUTED },
};

/** Where price sits against the latest Asia-range levels, in words. */
function avrPosition(day: AvrDay, price: number): string {
  if (price <= day.asia.high && price >= day.asia.low) return 'inside the Asia range';
  const up = price > day.asia.high;
  const levels = day.levels.filter((l) => l.side === (up ? 'up' : 'down'));
  let passed = 'Asia ' + (up ? 'high' : 'low');
  for (const l of levels) {
    const reached = up ? price >= l.lo : price <= l.hi;
    if (reached) passed = l.label;
  }
  return `${up ? 'above' : 'below'} Asia, past ${passed}`;
}

export function ArPanel({
  state,
  price,
  decimals,
  hoverIndex,
  times,
  onFocusTrade,
  onGoTo,
}: {
  state: ArPanelState;
  price: number | undefined;
  decimals: number;
  /** Candle under the crosshair: the checklist shows that candle instead of the latest. */
  hoverIndex: number | null;
  /** Wall-clock time of each candle, for labels. */
  times: number[];
  onFocusTrade: (id: number | null) => void;
  onGoTo: (index: number) => void;
}) {
  const [open, setOpenState] = useState(readOpen);
  const setOpen = (v: boolean) => {
    setOpenState(v);
    try {
      localStorage.setItem(OPEN_KEY, v ? '1' : '0');
    } catch {
      /* private window */
    }
  };
  const fmt = (p: number) => p.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const env = state.env ? ENV[state.env.env] : null;
  const day = state.avr[state.avr.length - 1];

  if (!open) {
    return (
      <aside data-tv-ui className="flex w-7 shrink-0 flex-col items-center border-l border-[#2a2e39] bg-[#131722]">
        <button
          type="button"
          onClick={() => setOpen(true)}
          title="Show the AR model panel"
          className="mt-2 flex flex-col items-center gap-2 rounded px-1 py-2 text-[11px] text-[#787b86] hover:bg-[#2a2e39] hover:text-[#d1d4dc]"
        >
          <span>◂</span>
          <span className="[writing-mode:vertical-rl]">
            AR ·{' '}
            <span style={{ color: biasColor(state.prefer) }}>trend {biasText[state.prefer]}</span>
          </span>
        </button>
      </aside>
    );
  }

  const section = 'border-t border-[#2a2e39] px-2.5 py-2';
  const head = 'mb-1 text-[10px] font-semibold uppercase tracking-wide text-[#787b86]';
  const strat = state.strat;
  const readIndex = strat ? (hoverIndex != null && strat.reads[hoverIndex] ? hoverIndex : strat.reads.length - 1) : -1;
  const read = strat?.reads[readIndex];
  const skipHere = strat?.skips.filter((k) => k.index === readIndex) ?? [];
  const tradeHere = read?.tradeId != null ? strat?.trades.find((t) => t.id === read.tradeId) : null;
  const sum = strat ? arSummary(strat.trades) : null;
  const fmtR = (r: number | null) => (r == null ? '—' : `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`);
  return (
    <aside
      data-tv-ui
      className="w-64 shrink-0 select-none overflow-y-auto border-l border-[#2a2e39] bg-[#131722] text-[11px] text-[#b2b5be]"
    >
      <div className="flex items-center justify-between px-2.5 py-2">
        <span className="font-semibold text-[#d1d4dc]">
          AR model
          <span className="ml-1.5 font-normal text-[#787b86]">location, not entries</span>
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          title="Hide the AR model panel"
          className="rounded px-1 text-[#787b86] hover:bg-[#2a2e39] hover:text-[#d1d4dc]"
        >
          ▸
        </button>
      </div>

      {strat && read ? (
        <div className={section}>
          <div className={head}>
            Checklist · {mmdd(times[readIndex])} {hhmm(times[readIndex])}{' '}
            <span className="normal-case tracking-normal">
              {hoverIndex != null && readIndex === hoverIndex ? '(candle under the cursor)' : '(latest candle)'}
            </span>
          </div>
          <ul className="space-y-1">
            {STEPS.map((s, n) => {
              const c = read.checks[s.key];
              return (
                <li key={s.key} className="flex gap-1.5 leading-snug">
                  <span className="w-3 shrink-0 text-center font-semibold" style={{ color: MARK[c.state].color }}>
                    {MARK[c.state].glyph}
                  </span>
                  <span>
                    <span className="text-[#d1d4dc]">
                      {n + 1} {s.label}
                    </span>{' '}
                    <span className="text-[#9598a1]">{c.text}</span>
                  </span>
                </li>
              );
            })}
          </ul>
          {skipHere.map((k, n) => (
            <div key={n} className="mt-1 rounded bg-[#2a2e39] px-1.5 py-1 leading-snug text-[#b2b5be]">
              ✕ Skipped {k.dir}: {k.reason}
            </div>
          ))}
          {tradeHere ? (
            <div className="mt-1 rounded bg-[#1e222d] px-1.5 py-1 leading-snug">
              In a {tradeHere.dir} from {fmt(tradeHere.entry)} · stop {fmt(tradeHere.stop)} · target {fmt(tradeHere.target)}
            </div>
          ) : null}
        </div>
      ) : null}

      {strat && sum ? (
        <div className={section}>
          <div className={head}>Trades on this chart · before costs</div>
          {sum.trades ? (
            <div className="mb-1.5 grid grid-cols-3 gap-1 text-center tabular-nums">
              <div className="rounded bg-[#1e222d] py-1">
                <div className="text-[13px] text-[#d1d4dc]">{sum.trades}</div>
                <div className="text-[10px] text-[#787b86]">
                  trades · {Math.round((100 * sum.wins) / sum.trades)}% won
                </div>
              </div>
              <div className="rounded bg-[#1e222d] py-1">
                <div className="text-[13px]" style={{ color: sum.points >= 0 ? UP : DOWN }}>
                  {sum.points >= 0 ? '+' : ''}
                  {fmt(sum.points)}
                </div>
                <div className="text-[10px] text-[#787b86]">points</div>
              </div>
              <div className="rounded bg-[#1e222d] py-1">
                <div className="text-[13px] text-[#d1d4dc]">{fmtR(sum.medianR)}</div>
                <div className="text-[10px] text-[#787b86]">median · avg {fmtR(sum.avgR)}</div>
              </div>
            </div>
          ) : (
            <div className="mb-1 text-[#787b86]">No finished trades in the loaded candles yet.</div>
          )}
          <ul className="max-h-40 space-y-0.5 overflow-y-auto">
            {[...strat.trades].reverse().slice(0, 30).map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between rounded px-1 py-0.5 text-left tabular-nums hover:bg-[#2a2e39]"
                  onMouseEnter={() => onFocusTrade(t.id)}
                  onMouseLeave={() => onFocusTrade(null)}
                  onClick={() => onGoTo(t.entryIndex)}
                  title={t.warnings.length ? `Taken with warnings:\n${t.warnings.join('\n')}` : 'All four steps passed'}
                >
                  <span>
                    <span style={{ color: t.dir === 'long' ? UP : DOWN }}>{t.dir === 'long' ? '▲' : '▼'}</span>{' '}
                    {mmdd(times[t.entryIndex])} {hhmm(times[t.entryIndex])}
                    {t.warnings.length ? <span className="text-[#ffb74d]"> △</span> : null}
                  </span>
                  <span>
                    <span className="text-[#787b86]">{t.outcome} </span>
                    <span style={{ color: t.r == null ? MUTED : t.r >= 0 ? UP : DOWN }}>{fmtR(t.r)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-[10px] leading-snug text-[#5d606b]">
            {strat.skips.length} setups skipped (✕ on the chart; hover one to read why). Confirmation is estimated
            from candles, not real order flow. Exploratory only: proper tests and costs come in phase 3.
          </div>
        </div>
      ) : null}

      {state.ladder.length ? (
        <div className={section}>
          <div className={head}>1 · Trend (market structure)</div>
          <div className="mb-1 flex gap-1.5">
            {state.ladder.map((r) => (
              <span
                key={r.label}
                className="flex-1 rounded bg-[#1e222d] py-1 text-center"
                title={r.bias ? `${r.label}: ${biasText[r.bias]} (last swing high and low)` : `${r.label}: finer than this chart`}
              >
                <span className="text-[#787b86]">{r.label} </span>
                <span style={{ color: biasColor(r.bias) }}>{biasArrow(r.bias)}</span>
              </span>
            ))}
          </div>
          <div className="leading-snug">
            Trend {biasText[state.prefer]}:{' '}
            {state.prefer === 'bull'
              ? 'look for longs in discount only.'
              : state.prefer === 'bear'
                ? 'look for shorts in premium only.'
                : 'no clear direction, so either side, with care.'}
          </div>
        </div>
      ) : null}

      {state.env && env ? (
        <div className={section}>
          <div className={head}>1 · Environment</div>
          <div className="font-semibold" style={{ color: env.color }}>
            {env.name}
          </div>
          <div className="mt-0.5 leading-snug">{env.fits}</div>
          <div
            className="mt-1 text-[10px] text-[#5d606b]"
            title="Our definition (he gives no numbers): efficiency = net move ÷ path over the window; range = window range ÷ its usual size."
          >
            efficiency {state.env.efficiency.toFixed(2)} · range {state.env.rangeRatio.toFixed(2)}× usual
          </div>
        </div>
      ) : null}

      {state.fib ? (
        <div className={section}>
          <div className={head}>2 · Location (range fib zone)</div>
          <div className="font-semibold" style={{ color: state.fib.dir === 'up' ? UP : DOWN }}>
            {state.fib.dir === 'up' ? 'Discount zone → long idea' : 'Premium zone → short idea'}
          </div>
          <div className="mt-0.5 leading-snug" style={{ color: STATUS[state.fib.status].color }}>
            {STATUS[state.fib.status].text}
          </div>
          <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 tabular-nums">
            <dt className="text-[#787b86]">Zone</dt>
            <dd className="text-right">
              {fmt(state.fib.zoneBottom)} – {fmt(state.fib.zoneTop)}
            </dd>
            <dt className="text-[#787b86]">Target</dt>
            <dd className="text-right">{fmt(state.fib.target)}</dd>
            <dt className="text-[#787b86]">Cancel if close past</dt>
            <dd className="text-right">{fmt(state.fib.invalidation)}</dd>
            {price != null ? (
              <>
                <dt className="text-[#787b86]">Price</dt>
                <dd className="text-right">{fmt(price)}</dd>
              </>
            ) : null}
          </dl>
          {state.fib.outsideValue != null ? (
            <div className="mt-1 leading-snug" style={{ color: state.fib.outsideValue ? UP : '#ffb74d' }}>
              {state.fib.outsideValue
                ? `✓ zone is outside value (${state.fib.dir === 'up' ? 'below VAL' : 'above VAH'}), as he requires`
                : `△ zone overlaps the value area: weaker location`}
            </div>
          ) : null}
        </div>
      ) : null}

      {day || state.dailyMissing ? (
        <div className={section}>
          <div className={head}>3 · Path (Asia range)</div>
          {day ? (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 tabular-nums">
                <dt className="text-[#787b86]">Asia high / low</dt>
                <dd className="text-right">
                  {fmt(day.asia.high)} / {fmt(day.asia.low)}
                </dd>
                <dt className="text-[#787b86]">Daily ATR 20</dt>
                <dd className="text-right">{fmt(day.atr)}</dd>
                <dt className="text-[#787b86]">Unit up / down</dt>
                <dd className="text-right">
                  {fmt(day.up)} / {fmt(day.down)}
                </dd>
              </dl>
              {price != null ? <div className="mt-1 leading-snug">Price is {avrPosition(day, price)}.</div> : null}
              <div className="mt-1 text-[10px] leading-snug text-[#5d606b]">
                Levels are where a move away from Asia tends to tire: use them to take profit or to spot exhaustion
                in the trend’s direction, never to trade against it. Rebuilt from his screen, approximate.
              </div>
            </>
          ) : (
            <div className="text-[#787b86]">Daily candles unavailable, so no levels.</div>
          )}
        </div>
      ) : null}

      {!strat ? (
        <div className={`${section} text-[10px] leading-snug text-[#5d606b]`}>
          Add “AR Strategy” for the 4-step checklist, entries and exits. Nothing here is a signal on its own.
        </div>
      ) : null}
    </aside>
  );
}
