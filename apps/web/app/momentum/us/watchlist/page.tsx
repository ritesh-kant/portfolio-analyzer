'use client';

import { useMemo, useState } from 'react';

import { US_LOCALE, US_LOCALE_IST } from '../../../../components/momentum-trade-chart';
import { MomentumWatchlist, type WatchlistMarket } from '../../../../components/momentum-watchlist';
import { fetchUSWatchlistBars, fetchUSWatchlistDays } from '../../../../lib/us-momentum-api';

const US_WATCHLIST: WatchlistMarket = {
  code: 'US',
  locale: US_LOCALE,
  open: '09:30',
  title: 'US momentum watchlist',
  intro:
    'Every name that passed the US screen, day by day, with the 1-minute bars the session traded on and a 5-minute view. Times are New York time (or India time with the toggle); money is in dollars.',
  links: [
    { href: '/momentum/us', label: '← US momentum' },
    { href: '/momentum/watchlist', label: 'NSE watchlist →' },
  ],
  tradesHref: '/momentum/us',
  barsNote: 'The 1-minute bars the US session read from its feed (Yahoo).',
  newsNote:
    "This company's SEC EDGAR filings from the start of the previous trading day to the end of this session, and nothing else: news aggregators (Google News, Yahoo, Benzinga, StocksToTrade) publish recaps after a stock has moved, so every mover would look like it had a catalyst. An 8-K or 6-K is shown by its press-release headline; share offerings are marked. Context only: the scanner never reads these.",
  newsEmpty: 'No SEC filings in the window: nothing official from the company explains this move.',
  fetchSessions: () => fetchUSWatchlistDays(),
  fetchBars: fetchUSWatchlistBars,
  empty:
    'No US session has written a watchlist yet. Names appear here once the US arm has run a live session.',
};

export default function USMomentumWatchlistPage() {
  const [ist, setIst] = useState(false);
  // 09:30 ET is 19:00 IST while US daylight saving is in effect (until early November), 20:00 after.
  const market = useMemo<WatchlistMarket>(
    () => (ist ? { ...US_WATCHLIST, locale: US_LOCALE_IST, open: '19:00' } : US_WATCHLIST),
    [ist],
  );
  return (
    <>
      <div className="mx-auto flex max-w-6xl justify-end px-4 pt-4">
        <button type="button" onClick={() => setIst((v) => !v)} className="metric-chip px-3 py-1 text-xs">
          Times in {ist ? 'IST' : 'ET'} · show {ist ? 'ET' : 'IST'}
        </button>
      </div>
      <MomentumWatchlist market={market} />
    </>
  );
}
