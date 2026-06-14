// NSE cash-market hours, evaluated in IST regardless of the browser's timezone.
// Window is padded (09:10–15:35) so dashboards keep refreshing through the
// 15:15 force-close and the ~15:18 EOD-summary settle before pausing.

const OPEN_MIN = 9 * 60 + 10; // 09:10 IST
const CLOSE_MIN = 15 * 60 + 35; // 15:35 IST

/**
 * Is the NSE cash market open right now? IST weekday + time window only.
 * NOTE: does not account for NSE trading holidays — on those (~12 days/yr) it
 * reports "open", i.e. the same always-on behavior as before. Harmless: it just
 * means a few extra refreshes on holidays, never a missed refresh on a real day.
 */
export function isNseMarketOpen(now: Date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  const weekday = get('weekday');
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  const mins = Number(get('hour')) * 60 + Number(get('minute'));
  return mins >= OPEN_MIN && mins <= CLOSE_MIN;
}
