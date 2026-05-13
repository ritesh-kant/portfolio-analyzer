'use client';

import { useRef, useState } from 'react';

import { PORTFOLIO_API_BASE } from '@/lib/api';

interface ImportTransactionsCardProps {
  onImported: (count: number) => void;
}

export function ImportTransactionsCard({ onImported }: ImportTransactionsCardProps) {
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    if (!file.name.endsWith('.csv')) {
      setStatus('error');
      setMessage('Please upload a .csv transaction file.');
      return;
    }

    setStatus('uploading');
    setMessage('');

    try {
      const text = await file.text();
      const res = await fetch(`${PORTFOLIO_API_BASE}/portfolio/transactions/import`, {
        method: 'POST',
        headers: { 'content-type': 'text/plain' },
        body: text,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? `Import failed (${res.status})`);
      }

      const data = (await res.json()) as { count: number };
      setStatus('success');
      setMessage(`${data.count} transactions imported.`);
      onImported(data.count);
    } catch (err) {
      setStatus('error');
      setMessage(err instanceof Error ? err.message : 'Upload failed.');
    }
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) {
      void upload(file);
    }
    e.target.value = '';
  }

  return (
    <div className="rounded-2xl border border-black/10 bg-panel p-4 shadow-card">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-display text-lg">Import Transactions CSV</h3>
        <button
          type="button"
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white"
          onClick={() => inputRef.current?.click()}
        >
          Select CSV
        </button>
      </div>

      <p className="text-xs text-ink/60">
        Required headers: symbol, quantity, price, side, executedAt (or executed_at), broker
      </p>

      <input ref={inputRef} type="file" accept=".csv" className="hidden" onChange={onFileChange} />

      {status !== 'idle' ? (
        <p
          className={`mt-2 text-xs ${
            status === 'success'
              ? 'text-emerald-700'
              : status === 'error'
                ? 'text-rose-700'
                : 'text-ink/70'
          }`}
        >
          {status === 'uploading' ? 'Uploading…' : message}
        </p>
      ) : null}
    </div>
  );
}
