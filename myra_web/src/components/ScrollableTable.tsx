import { useRef, useEffect, useCallback, ReactNode } from 'react';

interface ScrollableTableProps {
  children: ReactNode;        // must be a <table> element
  bottomOffset?: number;      // extra px to subtract from height (default 24)
  className?: string;
}

/**
 * ScrollableTable
 *
 * Wraps a <table> to provide:
 *   1. Vertical scroll bound to available viewport height (computed dynamically
 *      so it works regardless of parent flex chain).
 *   2. Horizontal scroll with a FLOATING scrollbar pinned to the bottom of the
 *      visible area via position:sticky — it is always visible without needing
 *      to scroll to the end of the rows.
 *   3. Sticky <thead> that remains pinned to the top of the scroll container.
 *
 * Usage:
 *   <ScrollableTable>
 *     <table>...</table>
 *   </ScrollableTable>
 */
export default function ScrollableTable({
  children,
  bottomOffset = 24,
  className = '',
}: ScrollableTableProps) {
  const outerRef   = useRef<HTMLDivElement>(null);
  const mirrorRef  = useRef<HTMLDivElement>(null);
  const spacerRef  = useRef<HTMLDivElement>(null);
  const syncingRef = useRef(false);  // prevents scroll event ping-pong

  // ── 1. Compute and maintain container height ────────────────────────────────
  const updateHeight = useCallback(() => {
    if (!outerRef.current) return;
    const rect = outerRef.current.getBoundingClientRect();
    const available = window.innerHeight - rect.top - bottomOffset;
    outerRef.current.style.height = `${Math.max(available, 200)}px`;
  }, [bottomOffset]);

  useEffect(() => {
    updateHeight();
    const ro = new ResizeObserver(updateHeight);
    ro.observe(document.documentElement);
    window.addEventListener('resize', updateHeight);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', updateHeight);
    };
  }, [updateHeight]);

  // ── 2. Sync scroll positions between container and mirror scrollbar ─────────
  useEffect(() => {
    const outer  = outerRef.current;
    const mirror = mirrorRef.current;
    if (!outer || !mirror) return;

    const onOuterScroll = () => {
      if (syncingRef.current) return;
      syncingRef.current = true;
      mirror.scrollLeft = outer.scrollLeft;
      syncingRef.current = false;
    };

    const onMirrorScroll = () => {
      if (syncingRef.current) return;
      syncingRef.current = true;
      outer.scrollLeft = mirror.scrollLeft;
      syncingRef.current = false;
    };

    outer.addEventListener('scroll', onOuterScroll, { passive: true });
    mirror.addEventListener('scroll', onMirrorScroll, { passive: true });
    return () => {
      outer.removeEventListener('scroll', onOuterScroll);
      mirror.removeEventListener('scroll', onMirrorScroll);
    };
  }, []);

  // ── 3. Keep mirror spacer width in sync with table scroll width ─────────────
  useEffect(() => {
    const outer  = outerRef.current;
    const spacer = spacerRef.current;
    if (!outer || !spacer) return;

    const update = () => {
      spacer.style.width = `${outer.scrollWidth}px`;
    };
    update();

    const ro = new ResizeObserver(update);
    ro.observe(outer);
    return () => ro.disconnect();
  }, []);

  return (
    <div className={`relative flex flex-col overflow-hidden ${className}`}>
      {/* ── Main scroll container ── */}
      <div
        ref={outerRef}
        className="overflow-auto scanner-table-scroll"
        role="region"
        aria-label="Scanner results — scroll horizontally or vertically"
        tabIndex={0}
        style={{ overscrollBehavior: 'contain' }}
      >
        {children}

        {/*
          Floating horizontal scrollbar strip.
          position:sticky + bottom:0 keeps it pinned to the BOTTOM OF THE
          VISIBLE AREA inside the scroll container at all times, regardless
          of how many rows are in the table.

          The mirror div has overflow-x:auto but height of 0 — only the
          scrollbar track is visible (12px via padding). The spacer inside
          matches the table's scrollWidth so the scrollbar thumb has the
          correct proportional size.
        */}
        <div
          ref={mirrorRef}
          className="sticky bottom-0 left-0 z-30 overflow-x-auto overflow-y-hidden scanner-table-scroll"
          style={{
            height: '14px',
            marginTop: '-14px',  // overlap the last row by the scrollbar height
            background: 'rgba(14,17,23,0.92)',
            backdropFilter: 'blur(4px)',
            borderTop: '1px solid rgba(255,255,255,0.06)',
          }}
          aria-hidden="true"  // decorative — keyboard users scroll via tabIndex above
        >
          <div ref={spacerRef} style={{ height: '1px' }} />
        </div>
      </div>
    </div>
  );
}
