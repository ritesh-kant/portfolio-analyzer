/** One intraday bar: start time (unix seconds) and the shares traded in it. */
export interface VolBar {
  t: number;
  v: number;
}

export interface SessionClock {
  /** The calendar day this instant belongs to in the exchange's zone, e.g. "2026-10-23". */
  day: string;
  /** Minutes since midnight in the exchange's zone. */
  min: number;
}

/** Builds a clock for an IANA zone. One formatter is reused: constructing it per bar is the slow part. */
export function zoneClock(timeZone: string): (t: number) => SessionClock {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  return (t) => {
    const p: Record<string, string> = {};
    for (const part of fmt.formatToParts(new Date(t * 1000))) p[part.type] = part.value;
    return { day: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
  };
}

/**
 * Relative volume AT THIS TIME OF DAY: the volume traded so far in the latest session divided by the
 * average volume the previous sessions had traded by the same clock time.
 *
 * Why not just volume ÷ average daily volume: a stock that has done 30% of a normal day's volume by
 * 10:00 is running far hotter than the full-day ratio (0.3×) suggests. Comparing like with like
 * removes the "it is still early" bias, so this reads the same at 09:45 and at 15:00.
 *
 * Returns null when fewer than `minPriorDays` earlier sessions are available or they traded nothing.
 */
export function relVolAtTime(
  bars: readonly VolBar[],
  clock: (t: number) => SessionClock,
  lookback = 10,
  minPriorDays = 3,
): number | null {
  if (!bars.length) return null;
  const byDay = new Map<string, Array<{ min: number; v: number }>>();
  for (const b of bars) {
    if (!(b.v >= 0)) continue;
    const { day, min } = clock(b.t);
    let list = byDay.get(day);
    if (!list) byDay.set(day, (list = []));
    list.push({ min, v: b.v });
  }
  const days = [...byDay.keys()].sort();
  const latest = days[days.length - 1]!;
  const today = byDay.get(latest)!;
  const cutoff = Math.max(...today.map((b) => b.min));
  const cum = (list: Array<{ min: number; v: number }>) =>
    list.reduce((s, b) => (b.min <= cutoff ? s + b.v : s), 0);

  const prior: number[] = [];
  for (const day of days.slice(0, -1).slice(-lookback)) {
    const list = byDay.get(day)!;
    // A half-day (early close) never reached this clock time: it would drag the average down.
    if (Math.max(...list.map((b) => b.min)) < cutoff - 10) continue;
    prior.push(cum(list));
  }
  if (prior.length < minPriorDays) return null;
  const avg = prior.reduce((s, v) => s + v, 0) / prior.length;
  return avg > 0 ? cum(today) / avg : null;
}
