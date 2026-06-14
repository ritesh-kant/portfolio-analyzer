'use client';

import { useEffect } from 'react';

/**
 * Runs `callback` on a fixed interval, but skips ticks while the browser tab
 * is hidden and fires one immediate refresh when the tab becomes visible again.
 */
export function useVisibilityRefresh(callback: () => void, intervalMs: number) {
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState !== 'hidden') callback();
    }, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible') callback();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [callback, intervalMs]);
}
