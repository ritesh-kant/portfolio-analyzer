'use client';

import { useEffect, useState } from 'react';
import { isNseMarketOpen } from './market-hours';

/**
 * Polls `callback` on an interval, but skips the fetch while the tab is hidden
 * OR the NSE market is closed. The timer keeps ticking cheaply (no network), so
 * polling resumes automatically at the next market open or tab focus. Returns
 * the current market-open flag so callers can reflect it in the UI.
 */
export function useMarketRefresh(
  callback: () => void,
  intervalMs: number,
): { marketOpen: boolean } {
  const [marketOpen, setMarketOpen] = useState<boolean>(() => isNseMarketOpen());

  useEffect(() => {
    const tick = () => {
      const open = isNseMarketOpen();
      setMarketOpen(open); // React bails out when the value is unchanged
      if (open && document.visibilityState !== 'hidden') callback();
    };
    const id = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible' && isNseMarketOpen()) callback();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [callback, intervalMs]);

  return { marketOpen };
}
