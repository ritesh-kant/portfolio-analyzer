'use client';

import { US_LOCALE } from '../../../../components/momentum-trade-chart';
import { MomentumWatchlist, type WatchlistMarket } from '../../../../components/momentum-watchlist';
import { fetchUSWatchlistBars, fetchUSWatchlistDays } from '../../../../lib/us-momentum-api';

const US_WATCHLIST: WatchlistMarket = {
  code: 'US',
  locale: US_LOCALE,
  open: '09:30',
  title: 'US momentum watchlist',
  intro:
    'Every name that passed the US screen, day by day, with the 1-minute bars the session traded on and a 5-minute view. Times are New York time; money is in dollars.',
  links: [
    { href: '/momentum/us', label: '← US momentum' },
    { href: '/momentum/watchlist', label: 'NSE watchlist →' },
  ],
  tradesHref: '/momentum/us',
  barsNote: 'The 1-minute bars the US session read from its feed (Yahoo).',
  fetchSessions: () => fetchUSWatchlistDays(),
  fetchBars: fetchUSWatchlistBars,
  empty:
    'No US session has written a watchlist yet. Names appear here once the US arm has run a live session.',
};

export default function USMomentumWatchlistPage() {
  return <MomentumWatchlist market={US_WATCHLIST} />;
}
