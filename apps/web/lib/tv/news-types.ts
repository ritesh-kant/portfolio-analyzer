/** news = the company announced something; offering = new shares (dilution); filing = routine paperwork. */
export type NewsKind = 'news' | 'offering' | 'filing';

export interface NewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  /** ISO instant. */
  published_at: string;
  kind: NewsKind;
  text: string | null;
}

export interface NewsResponse {
  source: 'nse_filings' | 'sec_edgar' | 'none';
  supported: boolean;
  /** Why there is no news: unsupported symbol, missing config, upstream error. */
  reason?: string;
  items: NewsItem[];
}
