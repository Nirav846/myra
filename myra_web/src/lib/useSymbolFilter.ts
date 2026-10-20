import { useCallback, useState } from 'react';
import { normalizeQuery } from './symbolFilter';

export interface SymbolFilterState {
  /** Raw value bound to the input. */
  query: string;
  setQuery: (next: string) => void;
  /** Reset to "" - wired to the clear button and to Escape. */
  clear: () => void;
  /** Trimmed, upper-cased query; "" when inactive. */
  normalized: string;
  /** True when a non-empty query is narrowing the table. */
  active: boolean;
}

/**
 * Local (client-side) symbol search state for a results table.
 *
 * Deliberately client-side: every scanner result table this hook is wired into
 * already holds its complete result set in memory (the scanner returns the
 * whole candidate list in one response), so filtering is instant and costs no
 * request per keystroke. Tables whose data is server-paged must filter on the
 * server instead - see docs/RESULTS_TABLE_SEARCH_AND_STICKY_SYMBOL.md.
 *
 * Pair with `filterBySymbol(rows, state.query, r => r.symbol)` inside the
 * view's existing filter memo, so search composes with the other filters and
 * with sorting instead of overriding them.
 */
export function useSymbolFilter(initial = ''): SymbolFilterState {
  const [query, setQuery] = useState(initial);
  const normalized = normalizeQuery(query);
  const clear = useCallback(() => setQuery(''), []);
  return { query, setQuery, clear, normalized, active: normalized.length > 0 };
}
