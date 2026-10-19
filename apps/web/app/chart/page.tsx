import type { Metadata } from 'next';
import { TvChart } from '../../components/tv/tv-chart';

export const metadata: Metadata = { title: 'Chart · Portfolio Analyzer' };

export default function ChartPage() {
  return <TvChart />;
}
