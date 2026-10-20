import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  countSymbolMatches,
  filterBySymbol,
  matchesSymbol,
  normalizeQuery,
} from './symbolFilter.ts';

interface Row {
  symbol: string | null | undefined;
  score: number;
}

const rows: Row[] = [
  { symbol: 'TATAMOTORS', score: 90 },
  { symbol: 'TATASTEEL', score: 80 },
  { symbol: 'TATAPOWER', score: 70 },
  { symbol: 'RELIANCE', score: 60 },
  { symbol: 'INFY', score: 50 },
];

const symbols = (list: Row[]) => list.map((r) => r.symbol);

describe('normalizeQuery', () => {
  test('trims and upper-cases', () => {
    assert.equal(normalizeQuery('  tata  '), 'TATA');
    assert.equal(normalizeQuery('\tTaTa\n'), 'TATA');
  });

  test('blank and missing input normalise to the inactive query', () => {
    for (const v of ['', '   ', null, undefined]) {
      assert.equal(normalizeQuery(v), '');
    }
  });

  test('non-string input does not throw', () => {
    assert.equal(normalizeQuery(42), '42');
    assert.equal(normalizeQuery({}), '[OBJECT OBJECT]');
  });
});

describe('matchesSymbol', () => {
  test('matches partially, case-insensitively', () => {
    assert.equal(matchesSymbol('TATAMOTORS', 'TATA'), true);
    assert.equal(matchesSymbol('tatamotors', 'TATA'), true);
    assert.equal(matchesSymbol('TATAMOTORS', 'tata'), true);
    // partial in the middle of the symbol, not only a prefix
    assert.equal(matchesSymbol('TATAMOTORS', 'MOTORS'), true);
    assert.equal(matchesSymbol('BAJAJ-AUTO', 'AJ-AU'), true);
  });

  test('an empty query matches everything', () => {
    for (const s of ['RELIANCE', '', null, undefined]) {
      assert.equal(matchesSymbol(s, ''), true);
    }
  });

  test('non-matching and null symbols do not match a query', () => {
    assert.equal(matchesSymbol('RELIANCE', 'TATA'), false);
    assert.equal(matchesSymbol(null, 'TATA'), false);
    assert.equal(matchesSymbol(undefined, 'TATA'), false);
    assert.equal(matchesSymbol('', 'TATA'), false);
  });

  test('symbols are normalised before comparison', () => {
    assert.equal(matchesSymbol('  reliance  ', 'RELI'), true);
  });
});

describe('filterBySymbol', () => {
  const get = (r: Row) => r.symbol;

  test('partial match is case-insensitive and returns every hit', () => {
    for (const q of ['tata', 'TATA', '  TaTa  ']) {
      assert.deepEqual(symbols(filterBySymbol(rows, q, get)), [
        'TATAMOTORS',
        'TATASTEEL',
        'TATAPOWER',
      ]);
    }
  });

  test('an empty or whitespace query filters nothing', () => {
    for (const q of ['', '   ', '\t']) {
      assert.deepEqual(symbols(filterBySymbol(rows, q, get)), symbols(rows));
    }
  });

  test('a query with no matches yields an empty array, not the input', () => {
    const out = filterBySymbol(rows, 'ZZZZ', get);
    assert.deepEqual(out, []);
    assert.notEqual(out, rows);
  });

  test('clearing the query restores the full set in its original order', () => {
    assert.equal(filterBySymbol(rows, 'TATA', get).length, 3);
    assert.deepEqual(symbols(filterBySymbol(rows, '', get)), symbols(rows));
  });

  test('searches the whole set, not just a visible window', () => {
    const many: Row[] = Array.from({ length: 500 }, (_, i) => ({
      symbol: `SYM${i}`,
      score: i,
    }));
    many[499] = { symbol: 'LASTONE', score: 499 };
    assert.deepEqual(symbols(filterBySymbol(many, 'LAST', get)), ['LASTONE']);
    assert.equal(filterBySymbol(many, 'SYM', get).length, 499);
  });

  test('preserves the incoming order so sort/ranking survives', () => {
    const sorted = [...rows].sort((a, b) => b.score - a.score);
    const out = filterBySymbol(sorted, 'TATA', get);
    assert.deepEqual(symbols(out), ['TATAMOTORS', 'TATASTEEL', 'TATAPOWER']);
    assert.deepEqual(
      out.map((r) => r.score),
      [90, 80, 70],
    );
  });

  test('never mutates or aliases the source array', () => {
    const source = [...rows];
    const snapshot = [...source];
    const filtered = filterBySymbol(source, 'TATA', get);
    assert.deepEqual(source, snapshot, 'source order must not change');
    assert.notEqual(filtered, source, 'result must be a new array');

    const identity = filterBySymbol(source, '', get);
    assert.notEqual(identity, source, 'even an inactive filter must copy');

    // The old Wyckoff bug: sorting the returned array reordered React state.
    identity.sort((a, b) => b.score - a.score);
    assert.deepEqual(source, snapshot, 'sorting the copy must not touch the source');
  });

  test('null/undefined symbols are skipped without throwing', () => {
    const mixed: Row[] = [
      { symbol: 'AAA', score: 1 },
      { symbol: null, score: 2 },
      { symbol: undefined, score: 3 },
      { symbol: 'AAB', score: 4 },
    ];
    assert.deepEqual(symbols(filterBySymbol(mixed, 'AA', get)), ['AAA', 'AAB']);
  });
});

describe('countSymbolMatches', () => {
  const get = (r: Row) => r.symbol;

  test('matches the filtered length', () => {
    for (const q of ['', 'TATA', 'tata', 'ZZZ', '  ']) {
      assert.equal(countSymbolMatches(rows, q, get), filterBySymbol(rows, q, get).length);
    }
  });

  test('empty query reports the full count', () => {
    assert.equal(countSymbolMatches(rows, '', get), rows.length);
    assert.equal(countSymbolMatches([], '', get), 0);
  });

  test('no-match reports zero (drives the no-match message)', () => {
    assert.equal(countSymbolMatches(rows, 'ZZZZ', get), 0);
  });
});
