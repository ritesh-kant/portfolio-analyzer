'use client';

import { useRouter } from 'next/navigation';
import { ScreenerPanel } from '../../components/screener/screener-panel';

/** Full-page screener. A row opens that symbol on the chart page. */
export function ScreenerPage() {
  const router = useRouter();
  return (
    <div className="h-[calc(100dvh-9.5rem)] min-h-[560px] sm:h-[calc(100dvh-7.5rem)]">
      <ScreenerPanel
        mode="page"
        onOpenSymbol={(row) => router.push(`/chart?symbol=${encodeURIComponent(row.symbol)}`)}
      />
    </div>
  );
}
