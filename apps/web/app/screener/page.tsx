import type { Metadata } from 'next';
import { ScreenerPage } from './screener-page';

export const metadata: Metadata = { title: 'Screener · Portfolio Analyzer' };

export default function Page() {
  return <ScreenerPage />;
}
