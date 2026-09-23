import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';

interface VirtualizedRowsProps<T> {
  /**
   * The FULL already-filtered/sorted dataset backing the table. VirtualizedRows
   * only ever slices this array — filtering/sorting must happen upstream in a
   * memo/useMemo so the array passed here matches what should be rendered.
   */
  data: readonly T[];
  /** Renders a single <tr> from one row; must return a stable-keyed element. */
  renderRow: (row: T, index: number) => ReactElement;
  /** Optional row height in px. Omitted -> auto-measured from the first row. */
  rowHeight?: number;
  /** Extra rows rendered above/below the viewport window. */
  overscan?: number;
  /** Forwarded to the rendered <tbody> (keeps per-view cell styling). */
  className?: string;
}

const SCROLLER_SELECTOR = '.scanner-table-scroll';

/**
 * VirtualizedRows
 *
 * Opt-in padding-based windowing for <ScrollableTable>: renders only the
 * <tr>s currently visible in the table's scroll container (plus a small
 * overscan buffer) and simulates the missing rows' vertical space with
 * paddingTop/paddingBottom on the <tbody>. Nothing is moved out of the
 * document flow, so the mirror-scrollbar's scrollWidth sync and each view's
 * own sticky <thead> are unaffected.
 *
 * Scroll position is tracked against the nearest ancestor matching the
 * ScrollableTable scroller class — this is the one intentional point of
 * coupling. If the scroller is ever missing, it degrades to rendering the
 * full dataset (correct, just not virtualized).
 */
export default function VirtualizedRows<T>({
  data,
  renderRow,
  rowHeight: rowHeightProp,
  overscan = 6,
  className = '',
}: VirtualizedRowsProps<T>) {
  const tbodyRef = useRef<HTMLTableSectionElement>(null);
  const rafRef = useRef<number | null>(null);
  const [rowHeight, setRowHeight] = useState(rowHeightProp ?? 0);
  const [windowRange, setWindowRange] = useState({ start: 0, end: data.length });

  const total = data.length;

  const computeWindow = useCallback(
    (scroller: HTMLElement, rh: number) => {
      const viewport = scroller.clientHeight;
      const scrollTop = scroller.scrollTop;
      const start = Math.max(0, Math.floor(scrollTop / rh) - overscan);
      const end = Math.min(total, Math.ceil((scrollTop + viewport) / rh) + overscan);
      return { start, end };
    },
    [total, overscan],
  );

  useLayoutEffect(() => {
    const tbody = tbodyRef.current;
    const scroller = tbody?.closest<HTMLElement>(SCROLLER_SELECTOR);
    if (!tbody || !scroller) return;

    // Auto-measure from the first real <tr> when no rowHeight prop was given.
    // First pass renders the full dataset (rowHeight 0 -> no padding), so the
    // first row is always present to measure. Re-runs once after setRowHeight.
    if (!rowHeightProp && rowHeight === 0) {
      const first = tbody.firstElementChild as HTMLElement | null;
      if (first) {
        const h = Math.round(first.getBoundingClientRect().height);
        if (h > 0) {
          setRowHeight(h);
          return; // re-run this effect with the measured height
        }
      }
    }
    if (rowHeight <= 0) return;

    const update = () => {
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        setWindowRange(computeWindow(scroller, rowHeight));
      });
    };

    update();

    scroller.addEventListener('scroll', update, { passive: true });
    // Re-window when the container's viewport height changes (e.g. the
    // ScrollableTable height calc adjusting on window resize/zoom). This only
    // reads clientHeight — it never writes the container, so it can't fight
    // ScrollableTable's own ResizeObserver.
    const ro = new ResizeObserver(update);
    ro.observe(scroller);

    return () => {
      scroller.removeEventListener('scroll', update);
      ro.disconnect();
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [computeWindow, rowHeightProp, rowHeight]);

  // On dataset changes: clamp a stranded scroll position to the true end, so
  // sorting/filtering can't leave the window pointing past a shorter dataset.
  useLayoutEffect(() => {
    const tbody = tbodyRef.current;
    const scroller = tbody?.closest<HTMLElement>(SCROLLER_SELECTOR);
    if (!tbody || !scroller || rowHeight <= 0) return;
    const maxScroll = Math.max(0, total * rowHeight - scroller.clientHeight);
    if (scroller.scrollTop > maxScroll) {
      scroller.scrollTop = maxScroll;
    }
    setWindowRange(computeWindow(scroller, rowHeight));
  }, [total, rowHeight, computeWindow]);

  const visibleRows = useMemo(() => {
    const slice = data.slice(windowRange.start, windowRange.end);
    return slice.map((row, i) => renderRow(row, windowRange.start + i));
    // renderRow is recreated by callers each render; recomputing the windowed
    // slice on data/window change is the only boundary that matters.
  }, [data, windowRange, renderRow]);

  return (
    <tbody
      ref={tbodyRef}
      className={className}
      style={{
        paddingTop: windowRange.start * rowHeight,
        paddingBottom: (total - windowRange.end) * rowHeight,
      }}
    >
      {visibleRows}
    </tbody>
  );
}