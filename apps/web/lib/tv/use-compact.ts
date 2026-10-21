'use client';

import { useEffect, useState } from 'react';

/** True on phone-width viewports (below Tailwind's `md`, 768px). False on the server and until mounted. */
export function useCompact(): boolean {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)');
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return compact;
}
