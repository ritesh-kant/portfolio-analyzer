import type { Metadata } from 'next';
import Link from 'next/link';

import './globals.css';

export const metadata: Metadata = {
  title: 'Portfolio Analyzer',
  description: 'Portfolio analytics for Indian investors',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen bg-grain">
          <header className="sticky top-0 z-20 border-b border-black/5 bg-bg/90 backdrop-blur">
            <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
              <div>
                <p className="font-display text-lg tracking-tight">Portfolio Analyzer</p>
                <p className="text-xs text-ink/70">Indian investor cockpit</p>
              </div>
              <nav className="flex items-center gap-2 rounded-full bg-panel p-1 shadow-card">
                <Link
                  href="/dashboard"
                  className="rounded-full px-4 py-1.5 text-sm font-semibold transition hover:bg-black/5"
                >
                  Dashboard
                </Link>
                <Link
                  href="/tax"
                  className="rounded-full px-4 py-1.5 text-sm font-semibold transition hover:bg-black/5"
                >
                  Tax
                </Link>
                <Link
                  href="/signals"
                  className="rounded-full px-4 py-1.5 text-sm font-semibold transition hover:bg-black/5"
                >
                  Signals
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
