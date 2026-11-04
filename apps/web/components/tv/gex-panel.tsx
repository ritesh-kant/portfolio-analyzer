'use client';

import { useState } from 'react';
import { type GexResponse, fmtGex } from '../../lib/tv/gex';

const UP = '#089981';
const DOWN = '#f23645';
const CALL = '#f4511e';
const PUT = '#43a047';
const HVL = '#448aff';
const RANGE = '#ff9800';

/** 1234567 → "1.23M". */
function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

const ratio = (puts: number, calls: number) => (calls > 0 ? (puts / calls).toFixed(2) : '—');

interface Row {
  label: string;
  /** Where the level sits on this chart (futures premium applied). */
  price: number;
  color: string;
  /** The option strike behind it, when it is one (shown instead of the shifted price). */
  strike?: number;
}

const OPEN_KEY = 'tv:gexPanelOpen';
function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== '0';
  } catch {
    return true;
  }
}

/**
 * The gamma read-out in its own column right of the chart, so it never covers candles; collapses to a thin
 * strip. The read-out (Tanuki's "Net exposure / Gamma profile / Chain activity" panels):
 * regime and what it means, net GEX and net delta, every key level with its distance from price, and the
 * put/call open-interest and volume ratios. Levels are drawn on the chart's scale (futures premium applied);
 * `price` is the chart's last price.
 */
export function GexPanel({
  res,
  price,
  symbol,
}: {
  res: GexResponse;
  price: number | undefined;
  /** The chart's symbol, labelling the price row (a future, not its underlying). */
  symbol: string;
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
  const all = res.all;
  if (!all) return null;
  const scale = res.scale ?? 1;
  const cur = res.currency ?? 'INR';
  const px = price ?? all.spot * scale;
  const flip = all.flip != null ? all.flip * scale : null;
  // Regime from where price is now against the flip (the snapshot's net GEX can lag a move through it);
  // without a flip, from the sign of net GEX.
  const positive = flip != null ? px >= flip : all.netGex >= 0;
  const regime = positive ? 'Positive gamma' : 'Negative gamma';
  const side = flip == null ? null : px >= flip ? 'Above HVL' : 'Below HVL';
  const meaning = positive
    ? 'dealer hedging tends to damp moves (buy dips, sell rips)'
    : 'dealer hedging tends to amplify moves (sell into drops, buy into rallies)';
  const gexUp = all.netGex >= 0;

  const rows: Row[] = [];
  all.callWalls.forEach((p, i) =>
    rows.push({
      label: i === 0 ? 'C1 · CR' : `C${i + 1}`,
      price: p * scale,
      color: CALL,
      strike: p,
    }),
  );
  all.putWalls.forEach((p, i) =>
    rows.push({
      label: i === 0 ? 'P1 · PS' : `P${i + 1}`,
      price: p * scale,
      color: PUT,
      strike: p,
    }),
  );
  if (flip != null) rows.push({ label: 'HVL', price: flip, color: HVL });
  if (all.range) {
    rows.push({ label: '1D Max', price: all.range[1] * scale, color: RANGE });
    rows.push({ label: '1D Min', price: all.range[0] * scale, color: RANGE });
  }
  rows.sort((a, b) => b.price - a.price);
  const at = rows.findIndex((r) => r.price < px);
  const list: (Row & { now?: boolean })[] = [...rows];
  list.splice(at < 0 ? rows.length : at, 0, {
    label: symbol,
    price: px,
    color: '#d1d4dc',
    now: true,
  });
  const digits = px >= 1000 ? 1 : 2;
  const act = all.activity;

  if (!open) {
    return (
      <aside
        data-tv-ui
        className="flex w-7 shrink-0 flex-col items-center border-l border-[#2a2e39] bg-[#131722]"
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          title="Show the gamma panel"
          className="mt-2 flex flex-col items-center gap-2 rounded px-1 py-2 text-[11px] text-[#787b86] hover:bg-[#2a2e39] hover:text-[#d1d4dc]"
        >
          <span>◂</span>
          <span className="[writing-mode:vertical-rl]">
            GEX ·{' '}
            <span style={{ color: positive ? UP : DOWN }}>
              {positive ? 'positive' : 'negative'}
            </span>
          </span>
        </button>
      </aside>
    );
  }

  return (
    <aside
      data-tv-ui
      className="w-60 shrink-0 select-none overflow-y-auto border-l border-[#2a2e39] bg-[#131722] text-[11px] text-[#b2b5be]"
    >
      <div className="flex items-center justify-between px-2.5 py-2">
        <span className="font-semibold" style={{ color: positive ? UP : DOWN }}>
          {regime}
          {side ? (
            <span className="ml-1.5 font-normal text-[#787b86]">{side.toLowerCase()}</span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          title="Hide the gamma panel"
          className="rounded px-1 text-[#787b86] hover:bg-[#2a2e39] hover:text-[#d1d4dc]"
        >
          ▸
        </button>
      </div>
      <div className="border-t border-[#2a2e39] px-2.5 pb-2 pt-1.5">
        <div className="mb-1.5 leading-snug text-[#9598a1]">
          {side ? `${side}: ` : ''}
          {meaning}.
        </div>

        <div className="mb-2 grid grid-cols-2 gap-1.5">
          <div
            className="rounded bg-[#1e222d] px-2 py-1"
            title="Net GEX at the snapshot's price: money dealers must trade per 1% move to stay hedged, under the chosen dealer position. + damps moves, − amplifies them."
          >
            <div className="text-[9px] uppercase tracking-wide text-[#787b86]">Net gamma</div>
            <div className="tabular-nums font-semibold" style={{ color: gexUp ? UP : DOWN }}>
              {fmtGex(all.netGex, cur)}
            </div>
          </div>
          <div
            className="rounded bg-[#1e222d] px-2 py-1"
            title="Net delta (DEX): Σ delta × open interest × price, as the option buyers hold it (calls +, puts −). Negative = open options are net short the underlying; the writers hold the opposite and hedge it."
          >
            <div className="text-[9px] uppercase tracking-wide text-[#787b86]">Net delta</div>
            <div
              className="tabular-nums font-semibold"
              style={{ color: (act?.netDelta ?? 0) >= 0 ? UP : DOWN }}
            >
              {act ? fmtGex(act.netDelta, cur) : '—'}
            </div>
          </div>
        </div>

        <table className="w-full tabular-nums">
          <tbody>
            {list.map((r) => {
              const pct = ((r.price - px) / px) * 100;
              return (
                <tr key={`${r.label}${r.price}`} className={r.now ? 'bg-[#2a2e39]' : ''}>
                  <td className="py-[1px] pl-1">
                    <span
                      className="mr-1.5 inline-block size-1.5 rounded-full"
                      style={{ background: r.color }}
                    />
                    <span style={{ color: r.now ? '#d1d4dc' : r.color }}>{r.label}</span>
                  </td>
                  <td
                    className="text-right text-[#d1d4dc]"
                    title={
                      r.strike != null && Math.abs(r.strike - r.price) > 1e-6
                        ? `Strike ${r.strike}, drawn at ${r.price.toFixed(2)} on this chart (futures premium)`
                        : undefined
                    }
                  >
                    {r.strike != null ? r.strike : r.price.toFixed(digits)}
                  </td>
                  <td
                    className="w-14 pr-1 text-right"
                    style={{ color: r.now ? '#787b86' : pct >= 0 ? UP : DOWN }}
                  >
                    {r.now ? '' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {act && act.callOi + act.putOi > 0 ? (
          <div className="mt-2 space-y-1 border-t border-[#2a2e39] pt-1.5">
            <div
              className="flex justify-between"
              title="Put/call open-interest ratio: put OI ÷ call OI. Above 1 = more puts open than calls."
            >
              <span className="text-[#787b86]">P/C OI</span>
              <span className="tabular-nums">
                <span className="text-[#d1d4dc]">{ratio(act.putOi, act.callOi)}</span>
                <span className="ml-1.5 text-[#787b86]">
                  {compact(act.putOi)} / {compact(act.callOi)}
                </span>
              </span>
            </div>
            {act.callVol + act.putVol > 0 ? (
              <div
                className="flex justify-between"
                title="Put/call volume ratio for today: put volume ÷ call volume."
              >
                <span className="text-[#787b86]">P/C volume</span>
                <span className="tabular-nums">
                  <span className="text-[#d1d4dc]">{ratio(act.putVol, act.callVol)}</span>
                  <span className="ml-1.5 text-[#787b86]">
                    {compact(act.putVol)} / {compact(act.callVol)}
                  </span>
                </span>
              </div>
            ) : null}
            <div className="text-[9px] text-[#5d606b]">
              {res.source === 'yahoo' ? 'shares (contracts × 100)' : 'units of the underlying'} ·{' '}
              {res.expiries?.length ?? 0} expiries
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
