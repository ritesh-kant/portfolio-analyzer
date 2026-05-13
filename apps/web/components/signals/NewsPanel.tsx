'use client';

import type { NewsArticle } from '../../lib/signals-api';

interface NewsPanelProps {
  articles: NewsArticle[];
}

export function NewsPanel({ articles }: NewsPanelProps) {
  return (
    <details className="group">
      <summary className="cursor-pointer select-none list-none py-2 text-sm font-semibold text-ink/70 hover:text-ink">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▶</span>
        News Headlines
        <span className="ml-2 text-xs font-normal text-ink/50">({articles.length} articles)</span>
      </summary>

      <div className="mt-2 space-y-1 rounded-xl border border-black/5 bg-panel p-3">
        {articles.length === 0 ? (
          <p className="text-sm text-ink/50">No news available</p>
        ) : (
          articles.map((a, i) => (
            <a
              key={i}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-start gap-2 rounded-lg p-2 text-sm transition hover:bg-black/[0.04]"
            >
              <span className="mt-0.5 shrink-0 text-xs text-ink/30">{i + 1}.</span>
              <span className="flex-1 text-ink/80 hover:text-ink">{a.title}</span>
              <span className="shrink-0 text-[10px] text-ink/40">{a.source}</span>
            </a>
          ))
        )}
      </div>
    </details>
  );
}
