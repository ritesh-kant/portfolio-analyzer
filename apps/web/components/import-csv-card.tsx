'use client';

import { useRef, useState } from 'react';

interface ImportCsvCardProps {
  onImported: (count: number) => void;
}

const PORTFOLIO_API_BASE =
  typeof window !== 'undefined' && process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE
    ? process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE
    : 'http://localhost:3001';

export function ImportCsvCard({ onImported }: ImportCsvCardProps) {
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    if (!file.name.endsWith('.csv')) {
      setStatus('error');
      setMessage('Please upload a .csv file exported from Groww.');
      return;
    }

    setStatus('uploading');
    setMessage('');

    try {
      const text = await file.text();
      const res = await fetch(`${PORTFOLIO_API_BASE}/portfolio/import`, {
        method: 'POST',
        headers: { 'content-type': 'text/plain' },
        body: text,
      });

      if (!res.ok) {
        throw new Error(`Import failed (${res.status})`);
      }

      const data = (await res.json()) as { count: number };
      setStatus('success');
      setMessage(`${data.count} holdings imported successfully.`);
      onImported(data.count);
    } catch (err) {
      setStatus('error');
      setMessage(err instanceof Error ? err.message : 'Upload failed.');
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void upload(file);
    e.target.value = '';
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) void upload(file);
  }

  const bgClass =
    status === 'success'
      ? 'border-emerald-600/30 bg-emerald-50'
      : status === 'error'
        ? 'border-rose-600/30 bg-rose-50'
        : dragging
          ? 'border-accent/50 bg-teal-50'
          : 'border-dashed border-black/20 bg-panel hover:border-accent/50 hover:bg-teal-50/30';

  return (
    <div
      className={`rounded-2xl border-2 p-4 shadow-card transition-colors cursor-pointer select-none ${bgClass}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent/10 text-accent">
          <UploadIcon />
        </div>
        <div className="min-w-0">
          <p className="font-semibold">Import Groww Holdings</p>
          <p className="text-xs text-ink/60">
            {status === 'uploading'
              ? 'Uploading…'
              : status === 'success'
                ? message
                : status === 'error'
                  ? message
                  : 'Drop a CSV or click to select. Export from Groww → Reports → Holdings.'}
          </p>
        </div>
      </div>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 12V3m0 0L8 7m4-4 4 4" />
    </svg>
  );
}
