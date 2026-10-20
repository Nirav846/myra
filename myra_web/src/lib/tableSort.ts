/**
 * Deterministic, null-safe sorting for scanner result tables.
 *
 * Why this exists
 * ---------------
 * The comparator this replaces picked text vs numeric comparison from the
 * *pair of values* instead of from the column:
 *
 *     const aVal = a[sortCol] ?? 0;
 *     if (typeof aVal === 'string' && typeof bVal === 'string') { ... }
 *     return aVal - bVal;          // 0 - "C" => NaN
 *
 * On the live Wyckoff cache that produced 74,648 NaN comparisons for the
 * `grade` column (1,333 ungraded rows x 56 graded rows). `Array#sort` requires a
 * consistent comparator; given NaN results its output is unspecified, so the
 * final order depended on the order of the incoming array.
 *
 * Rules implemented here
 * ----------------------
 * - The column decides the comparison kind, never the individual values.
 * - A value that cannot be represented in that kind (null, undefined, NaN,
 *   +/-Infinity, empty string, a non-numeric string in a numeric column) is
 *   "missing" and sorts **last** in both directions, so flipping the direction
 *   never buries real data behind blanks.
 * - The comparator always returns a negative number, zero or a positive number.
 *   It never returns NaN.
 * - Equal primary values fall back to a caller-supplied tie-break. For the
 *   Wyckoff table that is `symbol`, which is unique across the result set, so
 *   the output order is fully determined by the rows and not by their input
 *   order.
 */

export type SortKind = 'number' | 'string' | 'boolean';

/** Strict decimal/scientific literal: no hex, no bare separators. */
const NUMERIC_STRING = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

function coerceNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'boolean') {
    return value ? 1 : 0;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '' || !NUMERIC_STRING.test(trimmed)) return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function coerceText(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : null;
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return null;
}

function coerceBoolean(value: unknown): 0 | 1 | null {
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return value === 0 ? 0 : 1;
  }
  if (typeof value === 'string') {
    const t = value.trim().toLowerCase();
    if (t === 'true') return 1;
    if (t === 'false') return 0;
    return null;
  }
  return null;
}

function coerce(value: unknown, kind: SortKind): string | number | null {
  if (kind === 'number') return coerceNumber(value);
  if (kind === 'boolean') return coerceBoolean(value);
  return coerceText(value);
}

/**
 * Compare two raw cell values.
 *
 * Returns a negative number, zero or a positive number - never NaN.
 * `ascending` only reverses the ordering of *present* values; missing values
 * always come last.
 */
export function compareRows(
  aValue: unknown,
  bValue: unknown,
  kind: SortKind,
  ascending: boolean,
): number {
  const a = coerce(aValue, kind);
  const b = coerce(bValue, kind);

  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;

  let base: number;
  if (kind === 'string') {
    base = (a as string).localeCompare(b as string);
  } else {
    // Sign comparison, not subtraction: avoids Infinity arithmetic and -0.
    base = (a as number) < (b as number) ? -1 : (a as number) > (b as number) ? 1 : 0;
  }
  if (base === 0) return 0; // never return -0 from the reversed branch
  return ascending ? base : -base;
}

/**
 * Return a sorted copy of `rows`. The input array is never mutated, so callers
 * can pass React state or props directly.
 */
export function sortRows<T>(
  rows: readonly T[],
  key: keyof T & string,
  kind: SortKind,
  ascending: boolean,
  tieBreak?: (a: T, b: T) => number,
): T[] {
  return [...rows].sort((a, b) => {
    const primary = compareRows(a[key], b[key], kind, ascending);
    if (primary !== 0) return primary;
    return tieBreak ? tieBreak(a, b) : 0;
  });
}

/**
 * Column contract for the Wyckoff table. Every sortable header is listed; an
 * unknown key falls back to numeric, where unparseable values simply become
 * missing and the symbol tie-break keeps the order deterministic.
 */
export const WYCKOFF_SORT_KINDS: Record<string, SortKind> = {
  symbol: 'string',
  sector: 'string',
  grade: 'string',
  wyckoff_event: 'string',
  phase: 'string',
  event_date: 'string',
  equal_low_zone: 'boolean',
  two_candle_confirm: 'boolean',
  spring_score: 'number',
  close: 'number',
  lower_wick_ratio: 'number',
  close_location: 'number',
  grab_depth_pct: 'number',
  market_cap_cr: 'number',
  phase_complete_pct: 'number',
  days_since_event: 'number',
  event_delivery_pct: 'number',
  vol_ratio: 'number',
  event_quality: 'number',
  range_low_90: 'number',
  range_high_90: 'number',
};

/**
 * Sort Wyckoff candidates without mutating the caller's array.
 *
 * Tie-break: ascending `symbol`. Symbols are unique across a scan result
 * (1,389 rows / 1,389 distinct symbols on the live cache), so this yields a
 * total order - the same rows always produce the same sequence regardless of
 * the order they arrive in. Before this change the within-tie order was the
 * scan's universe-iteration order, which carried no ranking meaning.
 */
export function sortWyckoffCandidates<T extends { symbol: string }>(
  rows: readonly T[],
  key: string,
  ascending: boolean,
): T[] {
  return sortRows(rows, key as keyof T & string, WYCKOFF_SORT_KINDS[key] ?? 'number', ascending, (a, b) =>
    String(a.symbol ?? '').localeCompare(String(b.symbol ?? '')),
  );
}
