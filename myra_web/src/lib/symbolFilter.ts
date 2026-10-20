/**
 * Reusable symbol-search predicate shared by the scanner results tables.
 *
 * Framework-free so it can be unit tested without a DOM: the React state lives
 * in `useSymbolFilter.ts`, the input in `components/SymbolFilterInput.tsx`.
 * (Not to be confused with `components/SymbolSearch.tsx`, which is the global
 * symbol *navigator* with DB-backed autocomplete - a different job.)
 *
 * Contract:
 *   - matching is case-insensitive and partial (substring, not prefix-only)
 *   - leading/trailing whitespace is ignored; an all-whitespace query is "no
 *     filter" rather than "match nothing"
 *   - row order is preserved, so the ordering the view already applied
 *     (ranking, sort column, backend scan order) survives the filter
 *   - the input array is never mutated and never returned by reference,
 *     because callers routinely feed the result into `.sort()`.
 */

/** Normalise user input into the form comparisons use. */
export function normalizeQuery(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  return String(raw).trim().toUpperCase();
}

/** Normalise a symbol the same way the query is normalised. */
function normalizeSymbol(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  return String(raw).trim().toUpperCase();
}

/**
 * True when `symbol` contains `query`.
 *
 * Accepts a raw query (it is normalised here) so callers cannot accidentally
 * compare against an un-trimmed or lower-case value. The hot path in
 * `filterBySymbol` normalises once outside the loop instead.
 */
export function matchesSymbol(symbol: unknown, query: unknown): boolean {
  const q = normalizeQuery(query);
  if (!q) return true;
  return normalizeSymbol(symbol).includes(q);
}

/**
 * Filter `rows` to those whose symbol matches `rawQuery`.
 *
 * Always returns a new array (even when the query is empty) so a caller that
 * subsequently sorts the result cannot reorder its own state.
 */
export function filterBySymbol<T>(
  rows: readonly T[],
  rawQuery: unknown,
  getSymbol: (row: T) => unknown,
): T[] {
  const query = normalizeQuery(rawQuery);
  if (!query) return [...rows];
  return rows.filter((row) => normalizeSymbol(getSymbol(row)).includes(query));
}

/**
 * How many rows the query would keep. Used for the "3 of 42" hint on the
 * input without materialising the filtered array a second time.
 */
export function countSymbolMatches<T>(
  rows: readonly T[],
  rawQuery: unknown,
  getSymbol: (row: T) => unknown,
): number {
  const query = normalizeQuery(rawQuery);
  if (!query) return rows.length;
  let n = 0;
  for (const row of rows) {
    if (normalizeSymbol(getSymbol(row)).includes(query)) n += 1;
  }
  return n;
}
