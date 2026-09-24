'use client';

import { MomentumWatchlist, NSE_WATCHLIST } from '../../../components/momentum-watchlist';

export default function MomentumWatchlistPage() {
  return <MomentumWatchlist market={NSE_WATCHLIST} />;
}
