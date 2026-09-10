import type { Metadata } from 'next';
import { NavLinks } from '../components/nav-links';
import { auth, signOut } from '../auth';

import './globals.css';

export const metadata: Metadata = {
  title: 'Portfolio Analyzer',
  description: 'Portfolio analytics for Indian investors',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();

  return (
    <html lang="en">
      <body>
        <div className="min-h-screen bg-grain">
          <header className="sticky top-0 z-20 border-b border-black/5 bg-bg/90 backdrop-blur">
            <div className="mx-auto max-w-[1600px] px-4 sm:px-6 lg:px-8">
              {/* Top row: title + (mobile: sign-out | desktop: nav + sign-out) */}
              <div className="flex items-center justify-between py-3">
                <div>
                  <p className="font-display text-lg tracking-tight">Portfolio Analyzer</p>
                  <p className="text-xs text-ink/70">Indian investor cockpit</p>
                </div>
                {/* Desktop nav + sign out */}
                <div className="hidden sm:flex items-center gap-3">
                  <nav className="flex items-center gap-2 rounded-full bg-panel p-1 shadow-card">
                    <NavLinks />
                  </nav>
                  {session?.user && (
                    <form
                      action={async () => {
                        'use server';
                        await signOut({ redirectTo: '/login' });
                      }}
                    >
                      <button
                        type="submit"
                        className="rounded-full border border-black/10 bg-panel px-3 py-1.5 text-xs font-medium text-ink/70 shadow-card transition hover:text-ink"
                        title={session.user.email ?? undefined}
                      >
                        Sign out
                      </button>
                    </form>
                  )}
                </div>
                {/* Mobile: sign out only */}
                {session?.user && (
                  <form
                    className="sm:hidden"
                    action={async () => {
                      'use server';
                      await signOut({ redirectTo: '/login' });
                    }}
                  >
                    <button
                      type="submit"
                      className="rounded-full border border-black/10 bg-panel px-3 py-1.5 text-xs font-medium text-ink/70 shadow-card transition hover:text-ink"
                      title={session.user.email ?? undefined}
                    >
                      Sign out
                    </button>
                  </form>
                )}
              </div>
              {/* Mobile: scrollable nav row */}
              <div className="scrollbar-none sm:hidden -mx-4 overflow-x-auto px-4 pb-3">
                <nav className="flex w-max items-center gap-1 rounded-full bg-panel p-1 shadow-card">
                  <NavLinks />
                </nav>
              </div>
            </div>
          </header>
          <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
