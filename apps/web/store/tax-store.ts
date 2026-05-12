import { create } from 'zustand';

import { fetchTaxSummary } from '@/lib/api';
import { mockTaxSummary } from '@/lib/mock-data';

interface TaxStore {
  taxSummary: typeof mockTaxSummary;
  loading: boolean;
  error: string | null;
  source: 'live' | 'fallback';
  loadTaxSummary: () => Promise<void>;
}

export const useTaxStore = create<TaxStore>((set) => ({
  taxSummary: mockTaxSummary,
  loading: false,
  error: null,
  source: 'fallback',
  loadTaxSummary: async () => {
    set({ loading: true, error: null });

    try {
      const taxSummary = await fetchTaxSummary();
      set({ taxSummary, source: 'live', loading: false });
    } catch (error) {
      set({
        source: 'fallback',
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load live tax summary',
      });
    }
  },
}));
