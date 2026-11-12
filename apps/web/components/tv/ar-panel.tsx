'use client';

import { useState } from 'react';
import type { AvrDay, Bias, EnvironmentRead, RangeFib } from '../../lib/tv/ar';

/** What the panel reads from the chart's AR state (tv-chart.tsx `arState`). */
export interface ArPanelState {
  prefer: Bias;
  ladder: { label: string; bias: Bias | null }[];
  env: EnvironmentRead | null;
  fib: RangeFib | null;
  avr: AvrDay[];
  dailyMissing: boolean;
}

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
}: {
  state: ArPanelState;
  price: number | undefined;
  decimals: number;
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

      <div className={`${section} text-[10px] leading-snug text-[#5d606b]`}>
        Step 4, confirmation from order flow, and the entry come with the strategy (phase 2). Nothing here is a
        signal on its own.
      </div>
    </aside>
  );
}
