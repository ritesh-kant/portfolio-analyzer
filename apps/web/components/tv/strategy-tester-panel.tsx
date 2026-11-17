'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  type ArStrategyCfg,
  type ArStrategyResult,
  type ArTrade,
  STEPS,
  arStrategyDefaults,
} from '../../lib/tv/ar-strategy';
import type { IndicatorCfg } from '../../lib/tv/indicator-defs';
import {
  COST_DEFAULTS,
  type ContractInfo,
  type CostCfg,
  INSTRUMENTS,
  type TesterStats,
  type TradeResult,
  costPoints,
  groupSkips,
  instrumentFor,
  contractSpec,
  instrumentOf,
  roundTripCost,
  testerStats,
  tradeOverlap,
  tradeResults,
} from '../../lib/tv/strategy-tester';
import { ArStrategyFields, NumberField } from './indicator-dialog';

/**
 * Strategy Tester (bottom panel, like TradingView's): the AR Strategy's trades on the loaded candles, after costs.
 * Overview · Trades (click to jump the chart) · Compare (re-run with settings changed, side by side) · Costs.
 * Maths in lib/tv/strategy-tester.ts, shared with the command-line runner `pnpm bt:ar`.
 */

interface Props {
  symbol: string;
  /** The chart's exchange time zone: the strategy's default window follows its market. */
  timeZone: string;
  decimals: number;
  /** The chart's quote currency (INR, USD …): money for contracts without a preset. */
  currency?: string;
  /** NSE futures: lot and tick size of the contract (from the exchange's instrument list). */
  contract?: ContractInfo;
  /** Latest price: what the costs line prices a round trip at. */
  price?: number;
  /** Wall-clock seconds of each candle (the chart's exchange time). */
  times: number[];
  /** The AR Strategy indicator on the chart, or null when it is not added. */
  cfg: IndicatorCfg | null;
  /** It is added but hidden, so it is not running. */
  hidden: boolean;
  result: ArStrategyResult | null;
  /** Run the strategy on the same candles with other settings. */
  runVariant: (arStrat: Partial<ArStrategyCfg>) => ArStrategyResult | null;
  onCfg: (cfg: IndicatorCfg) => void;
  onAdd: () => void;
  onShow: () => void;
  onGoTo: (index: number) => void;
  onFocusTrade: (id: number | null) => void;
  onClose: () => void;
}

type Tab = 'overview' | 'trades' | 'compare' | 'costs';

const UP = '#089981';
const DOWN = '#f23645';
const MUTED = '#787b86';
const COSTS_KEY = 'tv:testerCosts';
const tone = (x: number) => (x > 0 ? UP : x < 0 ? DOWN : '#d1d4dc');

const th = 'sticky top-0 bg-[#131722] px-3 py-1.5 text-left text-[11px] font-medium text-[#787b86]';
const td = 'whitespace-nowrap px-3 py-1 text-[12px] tabular-nums text-[#d1d4dc]';

const ymd = (wall: number | undefined) => (wall == null ? '' : new Date(wall * 1000).toISOString().slice(0, 10));
const when = (wall: number | undefined) =>
  wall == null ? '' : new Date(wall * 1000).toISOString().slice(5, 16).replace('T', ' ');

function loadCosts(symbol: string): CostCfg {
  const fallback = { ...COST_DEFAULTS, instrument: instrumentFor(symbol).id };
  try {
    const all = JSON.parse(localStorage.getItem(COSTS_KEY) ?? '{}') as Record<string, CostCfg>;
    return all[symbol] ? { ...fallback, ...all[symbol] } : fallback;
  } catch {
    return fallback;
  }
}
function saveCosts(symbol: string, cfg: CostCfg) {
  try {
    const all = JSON.parse(localStorage.getItem(COSTS_KEY) ?? '{}') as Record<string, CostCfg>;
    all[symbol] = cfg;
    localStorage.setItem(COSTS_KEY, JSON.stringify(all));
  } catch {
    /* storage blocked: settings last for this visit only */
  }
}

/** Plain-word names of the strategy settings, for "what changed" in Compare. */
const SETTING: Record<keyof ArStrategyCfg, string> = {
  entry: 'Entry',
  entryWait: 'Order waits',
  windowStart: 'Window from',
  windowEnd: 'Window to',
  flatAt: 'Flat by',
  minRR: 'Min reward ÷ risk',
  swingMult: 'Swing size',
  confirmWithin: 'Confirm within',
  stopBuffer: 'Stop buffer',
  allowWarnings: 'Take warned setups',
  requireOutsideValue: 'Zone outside value',
  requireStack: 'Fill in a stacked-imbalance zone',
  confirm: 'Confirmation',
  absorbVol: 'Absorption volume ×',
  flipShare: 'Delta flip share',
  zone: 'Zone',
  gamma: 'Gamma',
  valueRows: 'Value-area rows',
  envWindow: 'Environment window',
};
const settingText = (k: keyof ArStrategyCfg, v: unknown) =>
  k === 'windowStart' || k === 'windowEnd' || k === 'flatAt'
    ? `${String(Math.floor(Number(v) / 60)).padStart(2, '0')}:${String(Number(v) % 60).padStart(2, '0')}`
    : typeof v === 'boolean'
      ? v
        ? 'on'
        : 'off'
      : v === 'pullback'
        ? 'pull-back'
        : v === 'orderflow'
          ? 'order flow'
          : v === 'lvn'
            ? 'LVN'
            : v === 'fib'
              ? '0.705–0.886'
              : v === 'decoder'
                ? 'decoder (fitted)'
                : v === 'targets'
                  ? 'take profit at gamma levels'
                  : String(v);

function changes(defaults: ArStrategyCfg, a: Partial<ArStrategyCfg> | undefined, b: Partial<ArStrategyCfg>) {
  const fa = { ...defaults, ...a };
  const fb = { ...defaults, ...b };
  return (Object.keys(SETTING) as (keyof ArStrategyCfg)[])
    .filter((k) => fa[k] !== fb[k])
    .map((k) => `${SETTING[k]} ${settingText(k, fa[k])} → ${settingText(k, fb[k])}`);
}

export function StrategyTesterPanel(p: Props) {
  const [tab, setTab] = useState<Tab>('overview');
  const [costs, setCosts] = useState<CostCfg>(() => ({ ...COST_DEFAULTS, instrument: instrumentFor(p.symbol).id }));
  useEffect(() => setCosts(loadCosts(p.symbol)), [p.symbol]);
  const updateCosts = (patch: Partial<CostCfg>) => {
    const next = { ...costs, ...patch };
    setCosts(next);
    saveCosts(p.symbol, next);
  };
  const ins = instrumentOf(costs);
  const money = useMemo(() => {
    const code = ins.id === 'custom' ? p.currency : ins.currency;
    const cur = code === 'USD' ? '$' : code === 'INR' ? '₹' : '';
    return (x: number, signed = true) => {
      const abs = Math.abs(x);
      const s = abs.toLocaleString('en-US', { maximumFractionDigits: abs >= 1000 ? 0 : 2, minimumFractionDigits: 0 });
      return `${x < 0 ? '−' : signed && x > 0 ? '+' : ''}${cur}${s}`;
    };
  }, [ins.id, ins.currency, p.currency]);

  const results = useMemo(() => (p.result ? tradeResults(p.result.trades, costs, p.contract) : []), [p.result, costs, p.contract]);
  const stats = useMemo(() => testerStats(results), [results]);
  const open = p.result?.trades.filter((t) => t.outcome === 'open') ?? [];

  // Compare: B starts as a copy of the chart's settings.
  const [b, setB] = useState<Partial<ArStrategyCfg> | null>(null);
  const { runVariant } = p;
  const bResult = useMemo(() => (tab === 'compare' && b ? runVariant(b) : null), [tab, b, runVariant]);
  const bResults = useMemo(() => (bResult ? tradeResults(bResult.trades, costs, p.contract) : []), [bResult, costs, p.contract]);
  const bStats = useMemo(() => testerStats(bResults), [bResults]);

  const first = p.times[0];
  const last = p.times[p.times.length - 1];
  const sessions = useMemo(() => new Set(p.times.map((t) => Math.floor(t / 86400))).size, [p.times]);

  const tabBtn = (t: Tab, label: string, n?: number) => (
    <button
      type="button"
      onClick={() => {
        setTab(t);
        if (t === 'compare' && !b) setB({ ...p.cfg?.arStrat });
      }}
      className={`px-3 py-2 text-[13px] font-medium ${
        tab === t ? 'border-b-2 border-[#2962ff] text-white' : 'text-[#b2b5be] hover:text-white'
      }`}
    >
      {label}
      {n ? <span className="ml-1 text-[#787b86]">{n}</span> : null}
    </button>
  );
  const cell = (label: string, value: string, color?: string, title?: string) => (
    <div className="flex flex-col px-3" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-[#787b86]">{label}</span>
      <span className="text-[13px] font-semibold tabular-nums" style={{ color: color ?? '#d1d4dc' }}>
        {value}
      </span>
    </div>
  );

  const body = () => {
    if (!p.cfg)
      return (
        <Empty>
          The tester runs a strategy over the candles on this chart.{' '}
          <button type="button" onClick={p.onAdd} className="text-[#7ea4ff] hover:underline">
            Add the AR Strategy
          </button>
        </Empty>
      );
    if (p.hidden)
      return (
        <Empty>
          The AR Strategy is hidden, so it is not running.{' '}
          <button type="button" onClick={p.onShow} className="text-[#7ea4ff] hover:underline">
            Show it
          </button>
        </Empty>
      );
    if (!p.result)
      return <Empty>The AR Strategy needs an intraday chart (1h or shorter), with at least a few days loaded.</Empty>;
    if (tab === 'trades')
      return (
        <TradesTable
          results={results}
          open={open}
          times={p.times}
          decimals={p.decimals}
          money={money}
          onGoTo={p.onGoTo}
          onFocus={p.onFocusTrade}
        />
      );
    if (tab === 'costs')
      return <CostsForm costs={costs} onChange={updateCosts} contract={p.contract} price={p.price ?? 0} money={money} />;
    if (tab === 'compare' && b)
      return (
        <Compare
          a={stats}
          b={bStats}
          aTrades={p.result.trades}
          bTrades={bResult?.trades ?? []}
          changed={changes(arStrategyDefaults(p.timeZone), p.cfg.arStrat, b)}
          money={money}
          editor={
            <ArStrategyFields
              cfg={{ ...p.cfg, arStrat: b }}
              timeZone={p.timeZone}
              onChange={(c) => setB({ ...(c.arStrat ?? {}) })}
            />
          }
          onReset={() => setB({ ...p.cfg?.arStrat })}
          onUse={() => {
            if (p.cfg) p.onCfg({ ...p.cfg, arStrat: { ...b } });
          }}
        />
      );
    return <Overview stats={stats} result={p.result} results={results} money={money} />;
  };

  return (
    <div className="flex h-[300px] max-h-[40vh] shrink-0 flex-col border-t border-[#2a2e39] bg-[#131722]" data-tv-ui>
      <div className="scrollbar-none flex shrink-0 items-center overflow-x-auto border-b border-[#2a2e39] [&>*]:shrink-0">
        <span className="px-3 text-[13px] font-semibold text-white">AR Strategy</span>
        {tabBtn('overview', 'Overview')}
        {tabBtn('trades', 'Trades', results.length + open.length)}
        {tabBtn('compare', 'Compare')}
        {tabBtn('costs', 'Costs')}
        <div className="flex-1" />
        {p.result ? (
          <>
            {cell('Net after costs', money(stats.net), tone(stats.net), `Before costs ${money(stats.gross)}`)}
            {cell('Trades', String(stats.trades))}
            {cell('Win rate', stats.trades ? `${Math.round(stats.winRate * 100)}%` : '–')}
            {cell(
              'Median / trade',
              stats.trades ? money(stats.medianNet) : '–',
              tone(stats.medianNet),
              `Average ${money(stats.avgNet)}`,
            )}
            {cell('Max drawdown', stats.trades ? money(-stats.maxDrawdown) : '–', stats.maxDrawdown ? DOWN : undefined)}
          </>
        ) : null}
        <button
          type="button"
          aria-label="Close panel"
          onClick={p.onClose}
          className="px-3 text-[16px] text-[#b2b5be] hover:text-white"
        >
          ×
        </button>
      </div>
      {p.result ? (
        <div className="shrink-0 border-b border-[#1e222d] px-3 py-1 text-[11px] text-[#787b86]">
          {ymd(first)} → {ymd(last)} · {sessions} days on the chart ·{' '}
          {ins.id === 'custom'
            ? `${costs.contracts} unit${costs.contracts === 1 ? '' : 's'}`
            : ins.id === 'NSEFUT'
              ? `${costs.contracts} lot${costs.contracts === 1 ? '' : 's'} of ${contractSpec(costs, p.contract).pointValue}`
              : `${ins.id} × ${costs.contracts}`}{' '}
          · costs {money(roundTripCost(costs, 'long', p.price ?? 0, p.price ?? 0, p.contract).total, false)} a round
          trip ({costPoints(costs, p.price ?? 0, p.contract).toFixed(2)} pts) ·{' '}
          {ins.id === 'custom' && !costs.commission ? (
            <>
              <button type="button" onClick={() => setTab('costs')} className="text-[#f23645] hover:underline">
                no fees set for this contract
              </button>{' '}
              ·{' '}
            </>
          ) : null}
          <span className="text-[#ffb74d]">
            exploratory: confirmation is estimated from{' '}
            {p.cfg?.arStrat?.confirm === 'orderflow'
              ? p.result?.trades.some((t) => t.flow?.source === 'ticks')
                ? 'a footprint (recorded ticks on some trades, 1-minute bars elsewhere)'
                : 'a footprint built from 1-minute bars'
              : 'candles'}
            , and nothing here is a pre-registered test
          </span>
        </div>
      ) : null}
      <div className="min-h-0 flex-1 overflow-auto">{body()}</div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="p-4 text-[13px] text-[#b2b5be]">{children}</div>;
}

type Money = (x: number, signed?: boolean) => string;

function Overview({
  stats: s,
  result,
  results,
  money,
}: {
  stats: TesterStats;
  result: ArStrategyResult;
  results: TradeResult<ArTrade>[];
  money: Money;
}) {
  const r = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}R`;
  const pf = s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2);
  const rows: [string, string, string?][] = [
    ['Before costs', money(s.gross), tone(s.gross)],
    ['Costs', money(-s.cost), s.cost ? DOWN : undefined],
    ['Net after costs', money(s.net), tone(s.net)],
    ['Average / median per trade', `${money(s.avgNet)} / ${money(s.medianNet)}`],
    ['Average / median in R', s.trades ? `${r(s.avgR)} / ${r(s.medianR)}` : '–'],
    ['Profit factor', s.trades ? pf : '–'],
    ['Average win / loss', `${money(s.avgWin)} / ${money(s.avgLoss)}`],
    ['Best / worst trade', s.trades ? `${money(s.best)} / ${money(s.worst)}` : '–'],
    ['Net without the best 3', s.trades > 3 ? money(s.netWithoutTop3) : '–', s.trades > 3 ? tone(s.netWithoutTop3) : undefined],
  ];
  // Why setups did not trade: grouped by step and reason, numbers taken out.
  const skips = useMemo(() => groupSkips(result.skips), [result.skips]);
  // Gamma regime when each trade was confirmed (only when snapshots covered any of them)
  const byGamma = useMemo(() => {
    if (!results.some((x) => x.trade.gamma)) return null;
    return (['positive', 'negative', null] as const)
      .map((regime) => {
        const rs = results.filter((x) => (x.trade.gamma?.regime ?? null) === regime);
        return { regime, n: rs.length, net: rs.reduce((a, x) => a + x.net, 0) };
      })
      .filter((g) => g.n);
  }, [results]);
  const stepLabel = (k: string) => STEPS.find((x) => x.key === k)?.label ?? 'Entry';
  return (
    <div className="flex min-h-full flex-wrap gap-x-6 gap-y-3 p-3">
      <div className="min-w-[260px] flex-[2]">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-[#787b86]">Net after costs, trade by trade</div>
        <Equity values={s.equity} money={money} />
      </div>
      <table className="flex-1 self-start">
        <tbody>
          {rows.map(([k, v, c]) => (
            <tr key={k}>
              <td className="py-0.5 pr-4 text-[12px] text-[#b2b5be]">{k}</td>
              <td className="py-0.5 text-right text-[12px] font-medium tabular-nums" style={{ color: c ?? '#d1d4dc' }}>
                {v}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="min-w-[220px] flex-1 self-start">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-[#787b86]">How trades ended</div>
        {(['target', 'stop', 'time'] as const).map((o) => (
          <div key={o} className="flex justify-between text-[12px] text-[#b2b5be]">
            <span>{o === 'target' ? 'Target hit' : o === 'stop' ? 'Stopped out' : 'Closed at end of day'}</span>
            <span className="tabular-nums">
              {s.byOutcome[o]?.n ?? 0} ·{' '}
              <span style={{ color: tone(s.byOutcome[o]?.net ?? 0) }}>{money(s.byOutcome[o]?.net ?? 0)}</span>
            </span>
          </div>
        ))}
        {byGamma?.map((g) => (
          <div
            key={g.regime ?? 'none'}
            className="flex justify-between text-[12px] text-[#b2b5be]"
            title="Gamma regime when the trade was confirmed, from the newest recorded snapshot read before that candle closed"
          >
            <span>{g.regime === 'positive' ? 'Positive gamma' : g.regime === 'negative' ? 'Negative gamma' : 'No gamma snapshot'}</span>
            <span className="tabular-nums">
              {g.n} · <span style={{ color: tone(g.net) }}>{money(g.net)}</span>
            </span>
          </div>
        ))}
        {(['long', 'short'] as const).map((d) => (
          <div key={d} className="flex justify-between text-[12px] text-[#b2b5be]">
            <span>{d === 'long' ? 'Longs' : 'Shorts'}</span>
            <span className="tabular-nums">
              {s.byDir[d].n} · {s.byDir[d].n ? `${Math.round((s.byDir[d].wins / s.byDir[d].n) * 100)}% won · ` : ''}
              <span style={{ color: tone(s.byDir[d].net) }}>{money(s.byDir[d].net)}</span>
            </span>
          </div>
        ))}
      </div>
      <div className="min-w-[260px] flex-[1.5] self-start">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-[#787b86]">
          Setups that did not trade ({result.skips.length})
        </div>
        {skips.length ? (
          skips.slice(0, 7).map((k) => (
            <div key={`${k.step}|${k.reason}`} className="flex justify-between gap-3 text-[12px] text-[#b2b5be]">
              <span className="truncate" title={k.reason}>
                <span className="text-[#787b86]">{stepLabel(k.step)}:</span> {k.reason}
              </span>
              <span className="tabular-nums">{k.n}</span>
            </div>
          ))
        ) : (
          <div className="text-[12px] text-[#787b86]">None</div>
        )}
      </div>
    </div>
  );
}

/** Running net total after each trade, with the zero line. */
function Equity({ values, money }: { values: number[]; money: Money }) {
  if (!values.length) return <div className="py-6 text-[12px] text-[#787b86]">No closed trades yet.</div>;
  const W = 600;
  const H = 150;
  const pts = [0, ...values];
  const lo = Math.min(0, ...pts);
  const hi = Math.max(0, ...pts);
  const span = hi - lo || 1;
  const x = (i: number) => (i / Math.max(1, pts.length - 1)) * W;
  const y = (v: number) => 6 + (1 - (v - lo) / span) * (H - 12);
  const end = pts[pts.length - 1]!;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[150px] w-full">
        <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="#363a45" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        <polyline
          fill="none"
          stroke={end >= 0 ? UP : DOWN}
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          points={pts.map((v, i) => `${x(i)},${y(v)}`).join(' ')}
        />
        {pts.slice(1).map((v, i) => (
          <circle key={i} cx={x(i + 1)} cy={y(v)} r={3} fill={end >= 0 ? UP : DOWN} vectorEffect="non-scaling-stroke">
            <title>{`after trade ${i + 1}: ${money(v)}`}</title>
          </circle>
        ))}
      </svg>
      <span className="absolute right-1 top-0 text-[11px] tabular-nums" style={{ color: tone(hi) }}>
        {money(hi)}
      </span>
      <span className="absolute bottom-0 right-1 text-[11px] tabular-nums" style={{ color: tone(lo) }}>
        {money(lo)}
      </span>
    </div>
  );
}

function TradesTable({
  results,
  open,
  times,
  decimals,
  money,
  onGoTo,
  onFocus,
}: {
  results: TradeResult<ArTrade>[];
  open: ArTrade[];
  times: number[];
  decimals: number;
  money: Money;
  onGoTo: (index: number) => void;
  onFocus: (id: number | null) => void;
}) {
  const px = (x: number | null) => (x == null ? '–' : x.toFixed(decimals));
  const rows = [
    ...open.map((t) => ({ t, r: null as TradeResult<ArTrade> | null })),
    ...[...results].reverse().map((r) => ({ t: r.trade, r })),
  ];
  if (!rows.length) return <Empty>No trades on the loaded candles. The Overview lists why setups were skipped.</Empty>;
  const OUT: Record<string, string> = { target: 'Target', stop: 'Stop', time: 'End of day', open: 'Open' };
  return (
    <table className="w-full" onMouseLeave={() => onFocus(null)}>
      <thead>
        <tr>
          {['#', 'Side', 'Entry time', 'Entry', 'Stop', 'Target', 'Exit time', 'Exit', 'Result', 'Points', 'Net R', 'Net', 'Confirmation', 'Gamma', 'Warnings'].map(
            (h) => (
              <th key={h} className={th}>
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map(({ t, r }) => (
          <tr
            key={t.id}
            className="cursor-pointer border-t border-[#1e222d] hover:bg-[#1e222d]"
            onMouseEnter={() => onFocus(t.id)}
            onClick={() => onGoTo(t.entryIndex)}
            title="Show this trade on the chart"
          >
            <td className={td}>{t.id}</td>
            <td className={td} style={{ color: t.dir === 'long' ? UP : DOWN }}>
              {t.dir === 'long' ? 'Long' : 'Short'}
            </td>
            <td className={td}>{when(times[t.entryIndex])}</td>
            <td className={td}>{px(t.entry)}</td>
            <td className={td}>{px(t.stop)}</td>
            <td className={td}>{px(t.target)}</td>
            <td className={td}>{t.exitIndex != null ? when(times[t.exitIndex]) : '–'}</td>
            <td className={td}>{px(t.exit)}</td>
            <td className={td}>{OUT[t.outcome] ?? t.outcome}</td>
            <td className={td} style={{ color: r ? tone(r.grossPts) : MUTED }}>
              {r ? `${r.grossPts >= 0 ? '+' : '−'}${Math.abs(r.grossPts).toFixed(2)}` : '–'}
            </td>
            <td className={td} style={{ color: r ? tone(r.netR) : MUTED }}>
              {r ? `${r.netR >= 0 ? '+' : '−'}${Math.abs(r.netR).toFixed(2)}` : '–'}
            </td>
            <td className={`${td} font-medium`} style={{ color: r ? tone(r.net) : MUTED }}>
              {r ? money(r.net) : '–'}
            </td>
            <td
              className={`${td} text-[#787b86]`}
              title={
                t.flow
                  ? `Absorption ${t.flow.volRatio.toFixed(1)}× usual volume, delta flip ${Math.round(t.flow.flipShare * 100)}% of volume, from ${t.flow.source === 'ticks' ? 'recorded ticks' : 'a footprint estimated from 1-minute bars'}`
                  : 'Candle close beyond the extreme candle (estimated)'
              }
            >
              {t.flow ? `order flow${t.flow.source === 'ticks' ? '' : ' (est.)'}` : 'candles'}
              {t.entryRule === 'lvn' ? ' · LVN' : ''}
            </td>
            <td
              className={td}
              style={{ color: t.gamma ? (t.gamma.regime === 'positive' ? UP : DOWN) : MUTED }}
              title={
                t.gamma
                  ? `${t.gamma.regime === 'positive' ? 'Positive' : 'Negative'} gamma: ${
                      t.gamma.flip != null ? `price ${t.gamma.regime === 'positive' ? 'above' : 'below'} the flip ${px(t.gamma.flip)}` : 'no flip in range, net GEX sign'
                    }. Snapshot ${new Date(t.gamma.asOf * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC.${
                      t.gamma.level ? ` Target moved from the leg's end ${px(t.gamma.legTarget)} to ${t.gamma.level}.` : ''
                    }`
                  : 'No gamma snapshot covers this trade (recording started 2026-11-14)'
              }
            >
              {t.gamma ? `${t.gamma.regime === 'positive' ? '+' : '−'}${t.gamma.level ? ` · ${t.gamma.level}` : ''}` : '–'}
            </td>
            <td className={`${td} max-w-[280px] truncate text-[#787b86]`} title={t.warnings.join('\n')}>
              {t.warnings.length ? t.warnings.map((w) => w.split(':')[0]).join(', ') : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CostsForm({
  costs,
  onChange,
  contract,
  price,
  money,
}: {
  costs: CostCfg;
  onChange: (patch: Partial<CostCfg>) => void;
  contract?: ContractInfo;
  price: number;
  money: Money;
}) {
  const spec = contractSpec(costs, contract);
  const { ins } = spec;
  const nse = ins.id === 'NSEFUT';
  const c = roundTripCost(costs, 'long', price, price, contract);
  const pct = (x: number) => `${+(x * 100).toFixed(5)}%`;
  const parts: [string, number][] = nse
    ? [
        ['Slippage', c.slippage],
        ['Brokerage', c.commission],
        ['STT (sell side)', c.stt],
        ['Exchange charge', c.exchange],
        ['SEBI fee', c.sebi],
        ['Stamp duty (buy side)', c.stamp],
        ['GST', c.gst],
      ]
    : [
        ['Slippage', c.slippage],
        ['Commission + fees', c.commission],
      ];
  return (
    <div className="flex flex-wrap gap-x-10 p-3">
      <div className="w-[340px]">
        <label className="flex items-center justify-between gap-6 py-1.5 text-[13px] text-[#d1d4dc]">
          Contract
          <select
            value={costs.instrument}
            onChange={(e) => onChange({ instrument: e.target.value, commission: null, lot: null })}
            className="w-48 rounded border border-[#363a45] bg-[#131722] px-2 py-1 text-[#d1d4dc] outline-none focus:border-[#2962ff]"
          >
            {INSTRUMENTS.map((i) => (
              <option key={i.id} value={i.id}>
                {i.label}
              </option>
            ))}
          </select>
        </label>
        <NumberField
          label={nse ? 'Lots' : 'Contracts'}
          value={costs.contracts}
          min={1}
          onChange={(n) => onChange({ contracts: Math.round(n) })}
        />
        {nse || ins.id === 'custom' ? (
          <NumberField
            label={nse ? `Lot size (units)${contract?.lotSize && costs.lot == null ? ' · from the contract' : ''}` : 'Units'}
            value={spec.pointValue}
            min={1}
            onChange={(n) => onChange({ lot: Math.round(n) })}
          />
        ) : null}
        <NumberField
          label={nse ? 'Brokerage per order (₹)' : `Commission + fees per side${ins.currency ? ` (${ins.currency})` : ''}`}
          value={spec.commission}
          min={0}
          onChange={(n) => onChange({ commission: n })}
        />
        <NumberField
          label="Slippage per side (ticks)"
          value={costs.slipTicks}
          min={0}
          onChange={(n) => onChange({ slipTicks: n })}
        />
        <button
          type="button"
          onClick={() => onChange({ ...COST_DEFAULTS, instrument: costs.instrument, lot: null })}
          className="mt-1 text-[12px] text-[#7ea4ff] hover:underline"
        >
          Reset to defaults
        </button>
      </div>
      <div className="max-w-[460px] text-[12px] leading-relaxed text-[#b2b5be]">
        <p>
          Every trade pays these on the way in and on the way out, whether it hit the target or the stop. One round
          trip{price ? ` at ${price.toLocaleString('en-US')}` : ''}: <b className="text-white">{money(c.total, false)}</b> (
          {costPoints(costs, price, contract).toFixed(2)} points).
        </p>
        <table className="mt-1">
          <tbody>
            {parts.map(([k, v]) => (
              <tr key={k}>
                <td className="pr-6">{k}</td>
                <td className="text-right tabular-nums">{money(v, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[#787b86]">
          {nse ? (
            <>
              Lot {spec.pointValue} units, price step ₹{spec.tick}
              {contract?.lotSize ? ' (from the exchange’s contract list)' : ' (no contract data: set the lot size)'}.
              Rates since 1 April 2026: STT {pct(ins.levies!.sttSell)} of the sell value (it was 0.02 % before the
              2026 budget), exchange {pct(ins.levies!.exchange)} a side, SEBI ₹10 a crore, stamp duty{' '}
              {pct(ins.levies!.stampBuy)} of the buy value, GST 18 % on brokerage + exchange + SEBI. Brokerage is your
              broker&apos;s (₹20 an order at most discount brokers).
            </>
          ) : (
            <>
              One {ins.id === 'custom' ? 'unit' : ins.id} point is worth {spec.pointValue} {ins.currency} per contract;
              the price step is {spec.tick}. Default fees approximate IBKR&apos;s all-in rate (commission, exchange and
              regulatory fees).
            </>
          )}{' '}
          Slippage of one tick a side is the minimum this repo assumes; stop orders in a fast market usually lose more.
        </p>
      </div>
    </div>
  );
}

function Compare({
  a,
  b,
  aTrades,
  bTrades,
  changed,
  money,
  editor,
  onReset,
  onUse,
}: {
  a: TesterStats;
  b: TesterStats;
  aTrades: ArTrade[];
  bTrades: ArTrade[];
  changed: string[];
  money: Money;
  editor: React.ReactNode;
  onReset: () => void;
  onUse: () => void;
}) {
  const overlap = useMemo(() => tradeOverlap(aTrades, bTrades), [aTrades, bTrades]);
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const r = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}R`;
  const show = (s: TesterStats, label: string) =>
    label === 'Trades' || (label === 'Net without the best 3' ? s.trades > 3 : s.trades > 0);
  const rows: [string, (s: TesterStats) => number, (x: number) => string, boolean][] = [
    ['Trades', (s) => s.trades, (x) => String(x), false],
    ['Win rate', (s) => s.winRate, pct, false],
    ['Net after costs', (s) => s.net, (x) => money(x), true],
    ['Average / trade', (s) => s.avgNet, (x) => money(x), true],
    ['Median / trade', (s) => s.medianNet, (x) => money(x), true],
    ['Median R', (s) => s.medianR, r, true],
    ['Max drawdown', (s) => -s.maxDrawdown, (x) => money(x), true],
    ['Net without the best 3', (s) => s.netWithoutTop3, (x) => money(x), true],
  ];
  return (
    <div className="flex flex-wrap gap-x-8 p-3">
      <div className="w-[340px]">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-[#787b86]">B: change any setting</div>
        {editor}
      </div>
      <div className="min-w-[340px] flex-1">
        <div className="mb-1 text-[12px] text-[#b2b5be]">
          {changed.length ? (
            <>
              <span className="text-[#787b86]">B differs from the chart (A):</span> {changed.join(' · ')}
            </>
          ) : (
            <span className="text-[#787b86]">Change a setting on the left to see what it does. A = the chart.</span>
          )}
        </div>
        <table className="w-full">
          <thead>
            <tr>
              {['', 'A (chart)', 'B', 'B − A'].map((h) => (
                <th key={h} className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, get, fmt, isMoney]) => {
              const d = show(a, label) && show(b, label) ? get(b) - get(a) : 0;
              return (
                <tr key={label} className="border-t border-[#1e222d]">
                  <td className={`${td} text-[#b2b5be]`}>{label}</td>
                  <td className={td}>{show(a, label) ? fmt(get(a)) : '–'}</td>
                  <td className={td}>{show(b, label) ? fmt(get(b)) : '–'}</td>
                  <td className={td} style={{ color: isMoney ? tone(d) : undefined }}>
                    {d === 0 ? '–' : label === 'Trades' ? `${d > 0 ? '+' : ''}${d}` : label === 'Win rate' ? `${d > 0 ? '+' : '−'}${Math.round(Math.abs(d) * 100)} pts` : fmt(d)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-2 text-[12px] text-[#b2b5be]">
          Same trade in both: {overlap.both} · only in A: {overlap.onlyA.length} · only in B: {overlap.onlyB.length}
        </div>
        <div className="mt-1 text-[11px] text-[#787b86]">
          A difference on a few trades is mostly luck. Settings picked because they looked best here are tuned to these
          candles; test them on later data before believing them.
        </div>
        <div className="mt-2 flex gap-3">
          <button type="button" onClick={onReset} className="text-[12px] text-[#7ea4ff] hover:underline">
            Reset B to the chart
          </button>
          {changed.length ? (
            <button
              type="button"
              onClick={onUse}
              className="rounded bg-[#2962ff] px-2 py-0.5 text-[12px] font-medium text-white hover:bg-[#1e53e5]"
            >
              Use B on the chart
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
