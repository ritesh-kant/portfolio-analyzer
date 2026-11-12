import type { Metadata } from 'next';
import { ChartLayout } from '../../components/tv/chart-layout';

export const metadata: Metadata = { title: 'Chart · Portfolio Analyzer' };

export default function ChartPage() {
  return <ChartLayout />;
}
