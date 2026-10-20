/**
 * Source-level contract test for the pinned-symbol-column CSS.
 *
 * There is no jsdom / testing-library in this project, so nothing here can
 * assert *rendered* geometry. What it can do - and does - is pin the rules the
 * behaviour depends on, so a later refactor of index.css cannot silently drop
 * the sticky positioning, the opaque fill, the z-index ordering or the
 * transform reset without a failing test.
 *
 * A browser check is still required to confirm the column actually stays put;
 * that is called out in docs/RESULTS_TABLE_SEARCH_AND_STICKY_SYMBOL.md rather
 * than claimed here.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../index.css', import.meta.url), 'utf8');

// Comments would otherwise be glued onto the next rule's selector list.
const cssRules = css.replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * Declaration bodies for a selector, concatenated across every rule whose
 * selector list contains it. The pinned column is split over several rules
 * (a shared `th.sticky-symbol, td.sticky-symbol` geometry rule, a header
 * z-index/background rule, and scroll-container variants), so a single-rule
 * lookup would miss the shared declarations.
 */
function block(selector: string): string {
  const bodies: string[] = [];
  for (const m of cssRules.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(',').map((s) => s.trim());
    if (selectors.includes(selector)) bodies.push(m[2]);
  }
  assert.ok(bodies.length > 0, `no CSS rule found for selector: ${selector}`);
  return bodies.join(';');
}

function decl(body: string, property: string): string | null {
  const re = new RegExp(`(?:^|;|\\s)${property}\\s*:\\s*([^;]+)`, 'm');
  const m = body.match(re);
  return m ? m[1].trim() : null;
}

describe('sticky symbol column CSS', () => {
  test('body cells are pinned to the left edge', () => {
    const body = block('td.sticky-symbol');
    assert.equal(decl(body, 'position'), 'sticky');
    assert.equal(decl(body, 'left'), '0');
  });

  test('header cell is pinned too, and carries an explicit min width', () => {
    const head = block('th.sticky-symbol');
    assert.equal(decl(head, 'position'), 'sticky');
    assert.equal(decl(head, 'left'), '0');
    assert.match(decl(head, 'min-width') ?? '', /^\d+(\.\d+)?rem$/);
    assert.match(decl(block('td.sticky-symbol'), 'min-width') ?? '', /^\d+(\.\d+)?rem$/);
  });

  test('header paints above body cells and above the other header cells', () => {
    const headZ = Number(decl(block('th.sticky-symbol'), 'z-index'));
    const bodyZ = Number(decl(block('td.sticky-symbol'), 'z-index'));
    assert.ok(Number.isFinite(headZ) && Number.isFinite(bodyZ), 'both z-indexes must be numeric');
    assert.ok(headZ > bodyZ, `header z-index ${headZ} must exceed body ${bodyZ}`);
    // The unscoped sticky-thead rule uses z-index 5; the pinned header must win.
    const scrollHeadZ = Number(
      decl(block('.scanner-table-scroll thead th.sticky-symbol'), 'z-index'),
    );
    assert.ok(scrollHeadZ > 5, `scoped header z-index ${scrollHeadZ} must exceed 5`);
  });

  test('pinned cells use an opaque fill, never a translucent one', () => {
    const bg = css.match(/--sticky-symbol-bg:\s*([^;]+);/);
    assert.ok(bg, '--sticky-symbol-bg must be declared');
    const value = (bg?.[1] ?? '').trim();
    assert.match(value, /^#[0-9a-f]{3}$|^#[0-9a-f]{6}$/i, `expected opaque hex, got ${value}`);
    assert.match(decl(block('td.sticky-symbol'), 'background') ?? '', /var\(--sticky-symbol-bg\)/);
  });

  test('row hover and zebra striping do not punch a hole in the column', () => {
    assert.match(block('.scanner-table-scroll tbody tr:hover td.sticky-symbol'), /var\(--sticky-symbol-bg-hover\)/);
    assert.match(
      block('.scanner-table-scroll tbody tr:nth-child(even) td.sticky-symbol'),
      /var\(--sticky-symbol-bg-alt\)/,
    );
  });

  test('the row hover transform is neutralised where a pinned cell exists', () => {
    const body = block('.scanner-table-scroll tbody tr:has(td.sticky-symbol):hover');
    assert.equal(decl(body, 'transform'), 'none');
  });

  test('pin is opt-in: no rule pins an arbitrary first column', () => {
    assert.doesNotMatch(css, /(^|\})\s*(th|td):first-child\s*\{[^}]*position:\s*sticky/);
    assert.doesNotMatch(css, /(^|\})\s*(th|td):nth-child\(1\)\s*\{[^}]*position:\s*sticky/);
  });

  test('the native search-cancel button is hidden so the X button is the only clear affordance', () => {
    assert.match(css, /::-webkit-search-cancel-button/);
    assert.match(css, /appearance:\s*none/);
  });
});
