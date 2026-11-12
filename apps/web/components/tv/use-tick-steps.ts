'use client';

import { useEffect, useState } from 'react';
import {
  unpackSteps,
  volumeSteps,
  type PackedStep,
  type TickSeries,
} from '../../lib/tv/tick-footprint';

/** NSE futures chart symbols: NIFTY1!.NFO, NIFTY26NOVFUT.NFO, NIFTY1!. */
export const isNseFuture = (symbol: string) => /\.NFO$/i.test(symbol) || /1!$/.test(symbol);

const EMPTY: TickSeries = volumeSteps([]);

/**
 * Volume per feed update recorded by capture_ticks.py for an NSE future (see lib/tv/tick-footprint.ts), refreshed
 * every 5 s while `live`. Empty for every other symbol, or when nothing was recorded.
 */
export function useTickSteps(symbol: string, enabled: boolean, live: boolean) {
  const [state, setState] = useState<{ symbol: string; series: TickSeries; days: string[] }>({
    symbol: '',
    series: EMPTY,
    days: [],
  });
  const wanted = enabled && isNseFuture(symbol);
  useEffect(() => {
    if (!wanted) return;
    let dead = false;
    const pull = async () => {
      try {
        const res = await fetch(`/api/chart/ticks?symbol=${encodeURIComponent(symbol)}&view=steps`, {
          cache: 'no-store',
        });
        if (!res.ok || dead) return;
        const j = (await res.json()) as {
          steps?: PackedStep[];
          segments?: [number, number][];
          days?: string[];
        };
        if (dead) return;
        setState({
          symbol,
          series: { steps: unpackSteps(j.steps ?? []), segments: j.segments ?? [] },
          days: j.days ?? [],
        });
      } catch {
        /* recorded ticks are optional; the footprint falls back to the 1-minute estimate */
      }
    };
    void pull();
    const id = live ? window.setInterval(pull, 5000) : 0;
    return () => {
      dead = true;
      if (id) window.clearInterval(id);
    };
  }, [wanted, symbol, live]);
  return wanted && state.symbol === symbol ? state : { symbol, series: EMPTY, days: [] as string[] };
}
