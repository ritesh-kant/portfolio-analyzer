/**
 * The chart's "Ticks" chip for NSE futures: when recording makes sense and what the recorder's state means.
 * Shared by the record route and the chip (no Node imports here).
 */

const IST_MS = 19_800_000;
export const NSE_OPEN_MIN = 9 * 60 + 15;
export const NSE_CLOSE_MIN = 15 * 60 + 30;
/** The recorder stops itself at this IST time. */
export const RECORD_UNTIL = '15:30';
/** No new line for this long while the market is open = the recorder is not getting data. */
export const STALE_MS = 60_000;

export interface NseClock {
  /** IST calendar day, YYYY-MM-DD: the recorder's folder name. */
  day: string;
  /** Minutes since IST midnight. */
  minute: number;
  weekday: boolean;
  /** 09:15–15:30 on a weekday (exchange holidays are not known here). */
  open: boolean;
  /** A recorder started now has something left to record today. */
  canRecord: boolean;
}

export function nseClock(nowMs: number): NseClock {
  const d = new Date(nowMs + IST_MS);
  const minute = d.getUTCHours() * 60 + d.getUTCMinutes();
  const dow = d.getUTCDay();
  const weekday = dow >= 1 && dow <= 5;
  return {
    day: d.toISOString().slice(0, 10),
    minute,
    weekday,
    open: weekday && minute >= NSE_OPEN_MIN && minute < NSE_CLOSE_MIN,
    canRecord: weekday && minute < NSE_CLOSE_MIN,
  };
}

/** What the record route reports for one instrument. */
export interface TickRecordStatus {
  available: boolean;
  key: string | null;
  clock: NseClock;
  /** A recorder this app started is running for this instrument. */
  running: boolean;
  /** Something (this app's recorder, or one started by hand) wrote a line in the last STALE_MS. */
  fresh: boolean;
  /** Feed updates recorded today, and the time of the last one (ms). */
  updatesToday: number;
  lastTickMs: number | null;
  /** IST days with a recording of this instrument, oldest first. */
  days: string[];
  /** Tail of today's recorder log (errors show up here). */
  log: string;
}

export type ChipState = 'recording' | 'waiting' | 'stalled' | 'idle' | 'closed';

/**
 * recording: lines are arriving. waiting: recorder running, market not open yet. stalled: recorder running during
 * market hours but no line for a minute (expired token, feed down). idle: could record, nothing running.
 * closed: nothing left to record today.
 */
export function chipState(s: Pick<TickRecordStatus, 'running' | 'fresh' | 'clock'>): ChipState {
  if (s.fresh) return 'recording';
  if (s.running) return s.clock.open ? 'stalled' : 'waiting';
  return s.clock.canRecord ? 'idle' : 'closed';
}
