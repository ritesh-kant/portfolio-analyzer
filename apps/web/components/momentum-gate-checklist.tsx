'use client';

import type { WatchlistGateHit, WatchlistName } from '../lib/momentum-api';

/*
 * "Why wasn't this traded?" — every filter between the universe and a fill,
 * in the order the engine applies them, marked against what the engine
 * actually logged for this name.
 *
 * The thresholds below are copied from the signal engine for display only.
 * If one changes there, change it here:
 *   NSE universe      apps/signal-engine/src/momentum_trader/universe.py
 *   US screen         apps/signal-engine/src/momentum_trader/us_universe.py
 *   entry gates       apps/signal-engine/src/momentum_trader/engine.py (warrior_strict)
 *   sizing            risk.py (NSE) · us_risk.py (US)
 *   deployed switches infrastructure/ecs-scanner.yml
 */

type Market = 'NSE' | 'US';

interface Gate {
  label: string;
  detail: string;
  /** Rejection reasons that mean THIS gate refused the name. */
  reasons?: string[];
  /** Why this gate did not run for this name, if it did not. */
  skipped?: (name: WatchlistName) => string | null;
}

interface Stage {
  title: string;
  gates: Gate[];
  /** Passed by definition for anything on the watchlist (never logged). */
  implied?: boolean;
}

const cutoff: Record<Market, string> = { NSE: '11:00', US: '15:10' };

const screen: Record<Market, Stage> = {
  NSE: {
    title: '1 · Universe (before the open)',
    implied: true,
    gates: [
      { label: 'In NIFTY 500 (or an extra list)', detail: 'Base symbol list the scanner subscribes to.' },
      { label: 'Series EQ, no ASM/GSM/ESM, band 0/10/20%', detail: 'Not trade-to-trade, not under surveillance, not in a 2% or 5% circuit band. Skipped when mt_universe.csv is missing.' },
      { label: 'Free-float mcap ₹500–5,000 cr · promoter ≥ 50%', detail: 'The NSE stand-in for "low float". Skipped when mt_universe.csv is missing.' },
      { label: 'Previous close ₹60–2,000', detail: 'Price band from the Warrior guide, in rupees.' },
      { label: '20-day average turnover ₹3–50 cr', detail: 'Liquid enough to fill, not so liquid it never moves.' },
    ],
  },
  US: {
    title: '1 · Screen (every minute)',
    implied: true,
    gates: [
      { label: 'Listed on NASDAQ / NYSE / AMEX / ARCA / BATS', detail: 'No OTC or pink sheets.' },
      {
        label: 'Float under 10M shares',
        detail: 'Criterion 5 (supply). Skipped when the float is unknown.',
        skipped: (name) =>
          name.details?.find((d) => d.label === 'Float')?.value === 'unknown' ? 'Skipped — float unknown, so this rule did not run.' : null,
      },
      { label: 'Price $1–20', detail: 'Criterion 4.' },
      { label: 'Up ≥ 10% on the day', detail: 'Criterion 2.' },
      { label: 'Relative volume ≥ 5× (full-day average)', detail: 'Criterion 1.' },
      { label: 'News catalyst — not required', detail: 'Criterion 3 is OFF: no US news feed is wired in.' },
      { label: 'Real-time quote (not "delayed")', detail: 'Yahoo quotes labelled delayed are dropped.' },
    ],
  },
};

const promotion = (market: Market): Stage => ({
  title: '2 · Promoted to attention (5-minute chart)',
  gates: [
    {
      label: `Day change ≥ ${market === 'NSE' ? '1.5' : '10'}% and time-of-day RVOL ≥ 1.5×`,
      detail: 'RVOL here = volume so far today ÷ the usual volume by this clock time.',
    },
    { label: '5-min EMA9 above EMA20, close above VWAP', detail: 'Short trend up and price above the day’s average traded price.' },
    { label: 'A completed bullish/indecision candle pattern or setup', detail: 'Promotion is only "watch closely" — it never buys by itself.' },
  ],
});

const stages = (market: Market): Stage[] => [
  screen[market],
  promotion(market),
  {
    title: '3 · Still in trend (checked every minute)',
    gates: [
      {
        label: 'Trend context holds',
        detail: 'Falling below VWAP or EMA9 under EMA20 drops the name back off attention; it must be promoted again.',
        reasons: ['attention_removed:below_vwap', 'attention_removed:ema_down', 'attention_removed:ema_warmup'],
      },
    ],
  },
  {
    title: '4 · 1-minute confirmation candle',
    gates: [
      { label: 'Enough bars', detail: 'At least 3 one-minute bars today.', reasons: ['attention_insufficient_bars'] },
      { label: 'Candle is green', detail: 'Close above open.', reasons: ['attention_red_or_flat'] },
      { label: 'Closes in the top 40% of its range', detail: 'Buyers held the high into the close.', reasons: ['attention_weak_close'] },
      { label: 'Volume ≥ 2.5× recent 1-min volume', detail: 'The push has real participation.', reasons: ['attention_low_1m_volume'] },
      {
        label: 'Rising price and volume (4 bars)',
        detail: 'Only when MT_REQUIRE_RISING_PRICE_VOLUME is on (off in prod).',
        reasons: ['attention_price_volume_insufficient', 'attention_price_volume_not_confirmed'],
      },
    ],
  },
  {
    title: '5 · Warrior checklist',
    gates: [
      { label: 'A micro pullback', detail: '2+ green bars, a 1–2 bar pause, then the break. Buy-stop goes at the pause high.', reasons: ['attention_no_micro_pullback'] },
      { label: 'Pullback on light volume', detail: 'The pause traded lighter than the push (sellers absent).', reasons: ['attention_heavy_pullback_volume'] },
      {
        label: '1-min MACD positive and still widening',
        detail: 'Histogram > 0 and not shrinking vs the previous bar.',
        reasons: ['attention_macd_warmup', 'attention_macd_not_positive', 'attention_macd_not_open'],
      },
      { label: 'Stop below the buy price', detail: 'Pullback low must sit under the trigger.', reasons: ['attention_invalid_stop'] },
      {
        label: 'Room to the next resistance ≥ 1R',
        detail: 'Distance to the next ceiling must be at least the stop distance, or it waits for the break.',
        reasons: ['attention_wait_resistance_break', 'attention_wait_next_resistance_break'],
      },
      { label: '1st or 2nd pullback of the move', detail: 'Later pullbacks are refused.', reasons: ['pullback_no_anchor', 'pullback_not_allowed'] },
    ],
  },
  {
    title: '6 · Order and size',
    gates: [
      { label: 'Buy-stop armed', detail: 'All of the above passed on one candle; a buy-stop sits at the trigger for 3 minutes.' },
      { label: 'Price reached the trigger within 3 minutes', detail: 'Otherwise the order expires.', reasons: ['pending_expired'] },
      { label: 'Not chased > 1% above the trigger', detail: 'A fill more than 1% above the plan is cancelled.', reasons: ['chased'] },
      market === 'NSE'
        ? { label: 'Stop 0.3–3% below entry', detail: 'Tighter is noise; wider is not a low-risk entry.', reasons: ['stop_not_sane'] }
        : {
            label: 'Stop ≥ 2 ticks and ≤ 10%, costs ≤ 25% of risk',
            detail: 'Round-trip cost over dollars at risk; above 0.25 the 2:1 target needs > 41.7% wins.',
            reasons: ['degenerate', 'stop_inside_tick', 'stop_too_wide', 'size_zero', 'cost_over_risk'],
          },
    ],
  },
  {
    title: '7 · Account and clock',
    gates: [
      {
        label: `Before the ${cutoff[market]} entry cutoff`,
        detail: market === 'NSE'
          ? 'warrior_strict only enters in the morning peak (09:15–11:00 IST). After that nothing is evaluated or logged.'
          : 'Entries 09:30–15:10 ET (13:00 cutoff on early-close days).',
        reasons: ['entry_cutoff'],
      },
      {
        label: `Fewer than ${market === 'NSE' ? 20 : 10} open positions`,
        detail: 'Concurrent paper-position cap.',
        reasons: ['max_positions'],
      },
      {
        label: 'Day not halted',
        detail: '3 losses in a row stops the day (discipline on).',
        reasons: ['halted:three_strikes', 'halted:profit_giveback'],
      },
      ...(market === 'US'
        ? [{
            label: 'Not LULD-halted, still on the screen',
            detail: 'No entry while halted or for 5 minutes after it resumes; an armed order is cancelled if the name leaves the screen.',
            reasons: ['halted', 'halt_cooldown', 'left_screen'],
          }]
        : []),
    ],
  },
];

const REENTRY_PREFIX = 'reclaim_';

type Status = 'pass' | 'final' | 'sometimes' | 'unreached' | 'info' | 'skipped';

const badge: Record<Status, { icon: string; className: string; text: string }> = {
  pass: { icon: '✓', className: 'bg-emerald-100 text-emerald-800', text: 'Passed' },
  final: { icon: '✗', className: 'bg-rose-100 text-rose-800', text: 'Blocked here' },
  sometimes: { icon: '!', className: 'bg-amber-100 text-amber-800', text: 'Blocked some minutes, passed others' },
  unreached: { icon: '○', className: 'bg-black/5 text-ink/45', text: 'Never reached' },
  info: { icon: '·', className: 'bg-black/5 text-ink/55', text: '' },
  skipped: { icon: '–', className: 'bg-amber-100 text-amber-800', text: 'Did not run' },
};

const hhmm = (iso: string, timeZone: string) =>
  new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));

export function GateChecklist({
  market,
  timeZone,
  name,
}: {
  market: Market;
  timeZone: string;
  name: WatchlistName;
}) {
  const hits = name.gates ?? [];
  const byReason = new Map(hits.map((h) => [h.reason, h]));
  const traded = name.trades.length > 0;
  const armed = name.armed ?? 0;
  // A US name can pass the screen and never be promoted; an NSE watchlist
  // name is, by construction, one that was.
  const promoted = market === 'NSE' || name.flags.some((f) => f.reason !== 'passed_screen');

  const plan = stages(market);
  const flat = plan.flatMap((stage, s) => stage.gates.map((gate, g) => ({ stage, s, gate, g })));
  const hitsFor = (gate: Gate) => (gate.reasons ?? []).map((r) => byReason.get(r)).filter((h): h is WatchlistGateHit => !!h);
  const armedIndex = flat.findIndex((x) => x.gate.label === 'Buy-stop armed');
  const clockIndex = flat.findIndex((x) => x.s === plan.length - 1);

  // The furthest gate the name demonstrably reached: the deepest one that
  // logged a refusal, or the arming step if an order was armed. Account/clock
  // gates can fire from anywhere, so they don't count as progress.
  let furthest = promoted ? flat.findIndex((x) => x.s === 2) : 1;
  flat.forEach((x, i) => {
    if (i < clockIndex && hitsFor(x.gate).length) furthest = Math.max(furthest, i);
  });
  if (armed > 0) furthest = Math.max(furthest, armedIndex);
  if (traded) furthest = flat.length;

  const statusOf = (i: number, gate: Gate): Status => {
    const { stage, s } = flat[i]!;
    if (gate.skipped?.(name)) return 'skipped';
    if (stage.implied) return 'pass';
    if (s === 1) return promoted ? 'pass' : 'final';
    if (traded) return hitsFor(gate).length ? 'sometimes' : 'pass';
    if (s === plan.length - 1) return hitsFor(gate).length ? 'final' : 'info';
    if (i === armedIndex) return armed > 0 ? 'pass' : 'unreached';
    if (i > furthest) return 'unreached';
    if (!hitsFor(gate).length) return 'pass';
    return i === furthest ? 'final' : 'sometimes';
  };

  const reentry = hits.filter((h) => h.reason.startsWith(REENTRY_PREFIX));
  const known = new Set([...flat.flatMap((x) => x.gate.reasons ?? []), ...reentry.map((h) => h.reason)]);
  const other = hits.filter((h) => !known.has(h.reason));

  const blockers = flat
    .map((x, i) => ({ ...x, i, status: statusOf(i, x.gate) }))
    .filter((x) => x.status === 'final');
  const verdict = traded
    ? 'Traded — every gate passed on at least one candle.'
    : !promoted
      ? 'Passed the screen but was never promoted: the 5-minute trend/pattern check (step 2) never passed. That step is not logged per minute.'
      : blockers.length
        ? `Closest it got: blocked at ${blockers.map((b) => `"${b.gate.label.toLowerCase()}" (${hitsFor(b.gate).reduce((n, h) => n + h.count, 0)} min)`).join('; ')}.`
        : hits.length === 0
          ? `Promoted, but no refusal was logged afterwards — the ${cutoff[market]} entry cutoff likely arrived first, or the name stopped printing bars.`
          : 'See the checklist below.';

  const minutes = (list: WatchlistGateHit[]) => {
    const count = list.reduce((sum, h) => sum + h.count, 0);
    const first = list.map((h) => h.first).sort()[0]!;
    const last = list.map((h) => h.last).sort().at(-1)!;
    return `${count} min · ${hhmm(first, timeZone)}${first !== last ? `–${hhmm(last, timeZone)}` : ''}`;
  };

  return (
    <section className="rounded-xl border border-black/10 p-4">
      <h3 className="font-display text-lg">Why it was / wasn&apos;t traded</h3>
      <p className="text-xs text-ink/55">
        Every filter in the order the engine applies it ({market === 'NSE' ? 'warrior_strict' : 'us_warrior_strict'}), marked against what it logged for {name.symbol} this session. Minutes = how many 1-minute checks that gate refused.
      </p>
      <p className={`mt-3 rounded-lg p-2.5 text-sm font-medium ${traded ? 'bg-emerald-50 text-emerald-900' : 'bg-rose-50 text-rose-900'}`}>
        {verdict}
        {armed > 0 && !traded ? ` A buy-stop was armed ${armed}× but never filled.` : ''}
      </p>

      <div className="mt-3 space-y-3">
        {plan.map((stage, s) => (
          <div key={stage.title}>
            <p className="text-xs font-bold uppercase tracking-wide text-ink/50">
              {stage.title}
              {stage.implied && <span className="ml-1 normal-case tracking-normal font-medium">— passed (it is on the watchlist)</span>}
            </p>
            <ul className="mt-1 divide-y divide-black/5 rounded-lg border border-black/5">
              {stage.gates.map((gate) => {
                const i = flat.findIndex((x) => x.s === s && x.gate === gate);
                const status = statusOf(i, gate);
                const b = badge[status];
                const gateHits = hitsFor(gate);
                return (
                  <li key={gate.label} className="flex items-start gap-2 px-2.5 py-2 text-sm">
                    <span
                      title={b.text}
                      className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${b.className}`}
                    >
                      {b.icon}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={status === 'unreached' ? 'text-ink/45' : 'font-medium'}>{gate.label}</p>
                      <p className="text-xs text-ink/55">{gate.detail}</p>
                      {status === 'skipped' && <p className="mt-0.5 text-xs font-medium text-amber-800">{gate.skipped?.(name)}</p>}
                      {gateHits.length > 0 && (
                        <p className="mt-0.5 text-xs text-ink/70 tabular-nums">
                          Refused {minutes(gateHits)}
                          {gateHits.length > 1 && ` (${gateHits.map((h) => `${h.reason.split(':').at(-1)!.replace(/^attention_/, '').replaceAll('_', ' ')} ${h.count}`).join(', ')})`}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}

        {reentry.length > 0 && (
          <div>
            <p className="text-xs font-bold uppercase tracking-wide text-ink/50">Re-entry after a false break</p>
            <p className="text-xs text-ink/55">After a trade exits on a false break, one re-entry is allowed if price closes back through the lost level with the same candle checks.</p>
            <ul className="mt-1 space-y-0.5 text-xs text-ink/70 tabular-nums">
              {reentry.map((h) => (
                <li key={h.reason}>{h.reason.replace(REENTRY_PREFIX, '').replaceAll('_', ' ')} — {minutes([h])}</li>
              ))}
            </ul>
          </div>
        )}

        {other.length > 0 && (
          <div>
            <p className="text-xs font-bold uppercase tracking-wide text-ink/50">Other refusals</p>
            <ul className="mt-1 space-y-0.5 text-xs text-ink/70 tabular-nums">
              {other.map((h) => (
                <li key={h.reason}>{h.reason.replaceAll('_', ' ')} — {minutes([h])}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
