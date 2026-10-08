'use client';

export function ReviewFilters({
  query,
  onQuery,
  date,
  onDate,
  dates,
}: {
  query: string;
  onQuery: (value: string) => void;
  date: string;
  onDate: (value: string) => void;
  dates: { value: string; label: string }[];
}) {
  return (
    <section className="review-filters" aria-label="Find a stock or trade">
      <label>
        Search stock
        <input
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Symbol, e.g. NAVA"
        />
      </label>
      <label>
        Session
        <select value={date} onChange={(e) => onDate(e.target.value)}>
          <option value="">All sessions</option>
          {dates.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </label>
      {(query || date) && (
        <button
          type="button"
          onClick={() => {
            onQuery('');
            onDate('');
          }}
        >
          Clear filters
        </button>
      )}
    </section>
  );
}
