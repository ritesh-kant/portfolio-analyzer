'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/tax', label: 'Tax' },
  { href: '/signals', label: 'Signals' },
  { href: '/trading', label: 'Trading' },
  { href: '/momentum', label: 'Momentum' },
  { href: '/options', label: 'Options' },
] as const;

export function NavLinks() {
  const pathname = usePathname();
  return (
    <>
      {LINKS.map(({ href, label }) => {
        const active = pathname === href || (href !== '/dashboard' && pathname.startsWith(href));
        return (
          <Link
            key={href}
            href={href}
            className={`rounded-full px-3 py-1 text-xs font-semibold transition sm:px-4 sm:py-1.5 sm:text-sm ${
              active ? 'bg-accent text-white shadow-sm' : 'hover:bg-black/5'
            }`}
          >
            {label}
          </Link>
        );
      })}
    </>
  );
}
