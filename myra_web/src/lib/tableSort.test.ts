import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  compareRows,
  sortRows,
  sortWyckoffCandidates,
  WYCKOFF_SORT_KINDS,
  type SortKind,
} from './tableSort';

/**
 * Regression tests for the two frontend defects:
 *
 * 1. the comparator could return NaN for a null-vs-value pair, leaving the
 *    final order dependent on the incoming array order;
 * 2. sorting mutated the array supplied by React state.
 *
 * Every assertion below is on observable behaviour only.
 */

type Row = {
  symbol: string;
  sector: string | null;
  wyckoff_event: string;
  grade: string | null;
  spring_score: number | null;
  market_cap_cr: number;
  phase_complete_pct: number;
  event_quality: number;
  days_since_event: number;
  equal_low_zone: boolean;
  event_date: string | null;
  close: number;
};

function row(symbol: string, overrides: Partial<Row> = {}): Row {
  return {
    symbol,
    sector: 'IT',
    wyckoff_event: 'SOS',
    grade: null,
    spring_score: null,
    market_cap_cr: 1000,
    phase_complete_pct: 90,
    event_quality: 50,
    days_since_event: 3,
    equal_low_zone: false,
    event_date: '2026-08-12 00:00:00',
    close: 100,
    ...overrides,
  };
}

/** Deterministic shuffle so permutation tests are reproducible. */
function shuffled<T>(items: readonly T[], seed: number): T[] {
  let state = seed;
  const next = () => {
    state = (state * 1103515245 + 12345) & 0x7fffffff;
    return state / 0x7fffffff;
  };
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

const MESSY_VALUES: unknown[] = [
  null,
  undefined,
  Number.NaN,
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
  0,
  -0,
  -1,
  3.5,
  '3.5',
  ' 12 ',
  '1e3',
  '',
  '   ',
  'N/A',
  '0x10',
  true,
  false,
  '2026-01-02 00:00:00',
  'Alpha',
];

const KINDS: SortKind[] = ['number', 'string', 'boolean'];

describe('compareRows', () => {
  test('never returns NaN, for any value pair, kind or direction', () => {
    for (const kind of KINDS) {
      for (const ascending of [true, false]) {
        for (const a of MESSY_VALUES) {
          for (const b of MESSY_VALUES) {
            const result = compareRows(a, b, kind, ascending);
            assert.equal(
              Number.isNaN(result),
              false,
              `NaN for ${String(a)} vs ${String(b)} (${kind}, asc=${ascending})`,
            );
          }
        }
      }
    }
  });

  test('is antisymmetric', () => {
    for (const kind of KINDS) {
      for (const ascending of [true, false]) {
        for (const a of MESSY_VALUES) {
          for (const b of MESSY_VALUES) {
            const forward = compareRows(a, b, kind, ascending);
            const backward = compareRows(b, a, kind, ascending);
            // `===` (not assert.equal) so 0 and -0 are treated as the same sign.
            assert.ok(
              Math.sign(forward) === -Math.sign(backward),
              `not antisymmetric: ${String(a)} vs ${String(b)} (${kind})`,
            );
          }
        }
      }
    }
  });

  test('nulls, undefined, NaN and invalid numbers sort last in both directions', () => {
    const missing = [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, 'N/A', ''];
    for (const ascending of [true, false]) {
      for (const value of missing) {
        // missing sorts after a present value regardless of direction
        assert.equal(compareRows(value, 5, 'number', ascending), 1);
        assert.equal(compareRows(5, value, 'number', ascending), -1);
      }
    }
  });

  test('two missing values compare equal', () => {
    for (const kind of KINDS) {
      assert.equal(compareRows(null, undefined, kind, true), 0);
      assert.equal(compareRows(undefined, null, kind, false), 0);
      assert.equal(compareRows(Number.NaN, null, kind, true), 0);
    }
  });

  test('numeric strings are compared as numbers when the column is numeric', () => {
    assert.ok(compareRows('9', '100', 'number', true) < 0);
    assert.ok(compareRows(' 12 ', 3, 'number', true) > 0);
    assert.ok(compareRows('1e3', 999, 'number', true) > 0);
    assert.equal(compareRows('3.5', 3.5, 'number', true), 0);
  });

  test('unparseable strings in a numeric column are missing, not NaN', () => {
    for (const bad of ['N/A', '0x10', 'abc', '', '  ']) {
      assert.equal(compareRows(bad, 5, 'number', true), 1, `${bad} should be missing`);
    }
  });

  test('a string column does not fall through to arithmetic', () => {
    // The old comparator compared 0 - 'C' when one side was null.
    const result = compareRows(null, 'C', 'string', true);
    assert.equal(result, 1);
    assert.equal(Number.isNaN(result), false);
    assert.ok(compareRows('A', 'B', 'string', true) < 0);
    assert.ok(compareRows('A', 'B', 'string', false) > 0);
  });

  test('boolean columns order false before true', () => {
    assert.ok(compareRows(false, true, 'boolean', true) < 0);
    assert.ok(compareRows(false, true, 'boolean', false) > 0);
    assert.equal(compareRows(true, 1, 'boolean', true), 0);
    assert.equal(compareRows('false', false, 'boolean', true), 0);
  });

  test('equal present values compare equal and reversal is exact', () => {
    assert.equal(compareRows(42, 42, 'number', true), 0);
    assert.equal(compareRows('same', 'same', 'string', false), 0);
    assert.equal(compareRows(true, true, 'boolean', true), 0);

    assert.equal(Math.sign(compareRows(1, 2, 'number', true)), -1);
    assert.equal(Math.sign(compareRows(1, 2, 'number', false)), 1);
    assert.equal(Math.sign(compareRows('a', 'b', 'string', true)), -1);
    assert.equal(Math.sign(compareRows('a', 'b', 'string', false)), 1);
    assert.equal(Math.sign(compareRows(false, true, 'boolean', true)), -1);
    assert.equal(Math.sign(compareRows(false, true, 'boolean', false)), 1);
  });
});

describe('sortRows', () => {
  test('does not mutate the input array or its rows', () => {
    const rows = [row('C'), row('A'), row('B')];
    const snapshot = rows.map((r) => ({ ...r }));
    const order = rows.map((r) => r.symbol);

    const sorted = sortRows(rows, 'symbol', 'string', true);

    assert.notEqual(sorted, rows, 'must return a new array');
    assert.deepEqual(
      rows.map((r) => r.symbol),
      order,
      'input array order must be unchanged',
    );
    assert.deepEqual(rows, snapshot, 'row objects must be unchanged');
    assert.deepEqual(
      sorted.map((r) => r.symbol),
      ['A', 'B', 'C'],
    );
  });

  test('preserves input order for ties when no tie-break is supplied', () => {
    const rows = [row('Z', { event_quality: 10 }), row('A', { event_quality: 10 })];
    assert.deepEqual(
      sortRows(rows, 'event_quality', 'number', true).map((r) => r.symbol),
      ['Z', 'A'],
    );
  });

  test('applies the tie-break when primary values are equal', () => {
    const rows = [row('Z'), row('A'), row('M')];
    assert.deepEqual(
      sortRows(rows, 'event_quality', 'number', true, (a, b) =>
        a.symbol.localeCompare(b.symbol),
      ).map((r) => r.symbol),
      ['A', 'M', 'Z'],
    );
  });
});

describe('sortWyckoffCandidates', () => {
  // Mirrors the live cache shape: 56 graded Spring rows, 1,333 ungraded rows.
  const rows: Row[] = [
    ...Array.from({ length: 56 }, (_, i) =>
      row(`SPRING${String(i).padStart(3, '0')}`, {
        wyckoff_event: 'Spring',
        grade: ['A', 'B', 'C', null][i % 4] ?? null,
        spring_score: i % 4 === 3 ? null : 40 + i,
        phase_complete_pct: 75,
      }),
    ),
    ...Array.from({ length: 1333 }, (_, i) =>
      row(`SOS${String(i).padStart(4, '0')}`, {
        wyckoff_event: 'SOS',
        phase_complete_pct: i % 3 === 0 ? 25 : 90,
        event_quality: 37.8 + (i % 60),
      }),
    ),
  ];

  test('grade: no NaN comparisons and ungraded rows sort last', () => {
    const sorted = sortWyckoffCandidates(rows, 'grade', false);
    const graded = sorted.filter((r) => r.grade !== null);
    const ungraded = sorted.filter((r) => r.grade === null);

    assert.equal(graded.length, 42);
    assert.equal(ungraded.length, rows.length - 42);
    // every graded row precedes every ungraded row
    assert.deepEqual(
      sorted.slice(0, graded.length).every((r) => r.grade !== null),
      true,
    );
    // and the comparator stayed consistent across the whole sorted run
    for (let i = 1; i < sorted.length; i += 1) {
      assert.ok(
        compareRows(sorted[i - 1].grade, sorted[i].grade, 'string', false) <= 0,
        'sorted run must be monotonic',
      );
    }
  });

  test('every sortable column is mapped to a comparison kind', () => {
    const required = [
      'symbol',
      'sector',
      'spring_score',
      'grade',
      'close',
      'wyckoff_event',
      'phase',
      'lower_wick_ratio',
      'close_location',
      'grab_depth_pct',
      'equal_low_zone',
      'two_candle_confirm',
      'market_cap_cr',
      'phase_complete_pct',
      'event_date',
      'days_since_event',
      'event_delivery_pct',
      'vol_ratio',
      'event_quality',
    ];
    for (const key of required) {
      assert.ok(WYCKOFF_SORT_KINDS[key], `${key} has no declared sort kind`);
    }
  });

  test('output order is independent of input permutation, for every column', () => {
    for (const key of Object.keys(WYCKOFF_SORT_KINDS)) {
      for (const ascending of [true, false]) {
        const orders = [1, 7, 42, 1234].map((seed) =>
          sortWyckoffCandidates(shuffled(rows, seed), key, ascending)
            .map((r) => r.symbol)
            .join(','),
        );
        for (const order of orders.slice(1)) {
          assert.equal(
            order,
            orders[0],
            `order for ${key} (asc=${ascending}) changed with input permutation`,
          );
        }
      }
    }
  });

  test('repeated sorts of the same array are identical', () => {
    const first = sortWyckoffCandidates(rows, 'spring_score', false);
    for (let i = 0; i < 3; i += 1) {
      assert.deepEqual(sortWyckoffCandidates(rows, 'spring_score', false), first);
    }
  });

  test('sorting state-like data leaves it untouched', () => {
    // Exactly the component's situation: the same array is held in state and
    // passed straight to the sorter when no filter applies.
    const state = [...rows];
    const before = state.map((r) => r.symbol);
    const result = sortWyckoffCandidates(state, 'phase_complete_pct', false);

    assert.notEqual(result, state);
    assert.deepEqual(
      state.map((r) => r.symbol),
      before,
      'React state array order must not change',
    );
  });

  test('default view order (phase_complete_pct desc) is deterministic', () => {
    const a = sortWyckoffCandidates(rows, 'phase_complete_pct', false).map((r) => r.symbol);
    const b = sortWyckoffCandidates(shuffled(rows, 99), 'phase_complete_pct', false).map(
      (r) => r.symbol,
    );
    assert.deepEqual(a, b);
    // 90 band first, then 75, then 25
    const bands = sortWyckoffCandidates(rows, 'phase_complete_pct', false).map(
      (r) => r.phase_complete_pct,
    );
    assert.deepEqual([...new Set(bands)].sort((x, y) => y - x), [90, 75, 25]);
  });

  test('duplicate symbols stay stable - the tie-break is then not a total order', () => {
    // Documents the assumption behind permutation-independence: `symbol` is
    // unique across a scan result. If a future scanner emits one row per event
    // for the same symbol, equal symbols fall back to input order (stable).
    const rows = [row('DUP', { event_quality: 10 }), row('DUP', { event_quality: 10 })];
    assert.deepEqual(
      sortWyckoffCandidates(rows, 'event_quality', true).map((r) => r.symbol),
      ['DUP', 'DUP'],
    );
  });

  test('numeric columns order numerically, not lexicographically', () => {
    const data = [row('A', { market_cap_cr: 9 }), row('B', { market_cap_cr: 100 })];
    assert.deepEqual(
      sortWyckoffCandidates(data, 'market_cap_cr', true).map((r) => r.symbol),
      ['A', 'B'],
    );
  });

  test('null sector does not poison the whole sort', () => {
    const data = [
      row('A', { sector: null }),
      row('B', { sector: 'IT' }),
      row('C', { sector: 'FMCG' }),
    ];
    const asc = sortWyckoffCandidates(data, 'sector', true).map((r) => r.symbol);
    const desc = sortWyckoffCandidates(data, 'sector', false).map((r) => r.symbol);
    assert.deepEqual(asc, ['C', 'B', 'A']);
    assert.deepEqual(desc, ['B', 'C', 'A']);
  });
});
