import { useState, useRef, useEffect, useId, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { HelpCircle } from 'lucide-react';

interface TooltipProps {
  content: string;
  good?: string;
  bad?: string;
  example?: string;
  children?: React.ReactNode;
  showIcon?: boolean;
  /**
   * Render the tooltip at document.body level with position:fixed.
   * Required inside overflow:auto/hidden containers (e.g. ScrollableTable)
   * where absolutely-positioned tooltips get clipped. Default false keeps
   * the original inline absolute positioning.
   */
  portal?: boolean;
}

/** Shared tooltip card body (content + good/bad/example rows). */
function TooltipBody({ content, good, bad, example }: Pick<TooltipProps, 'content' | 'good' | 'bad' | 'example'>) {
  return (
    <>
      <p className="text-[12px] text-[#ddd] font-sans leading-relaxed">{content}</p>
      {good && (
        <div className="mt-2 flex items-start gap-1.5">
          <span className="text-green-400 text-[12px] font-bold shrink-0 mt-0.5">✓ Good:</span>
          <span className="text-[12px] text-green-300/80">{good}</span>
        </div>
      )}
      {bad && (
        <div className="mt-1 flex items-start gap-1.5">
          <span className="text-red-400 text-[12px] font-bold shrink-0 mt-0.5">✗ Watch:</span>
          <span className="text-[12px] text-red-300/80">{bad}</span>
        </div>
      )}
      {example && (
        <div className="mt-2 pt-2 border-t border-[#ffffff10]">
          <span className="text-[12px] text-[#aaa] italic">{example}</span>
        </div>
      )}
    </>
  );
}

export function Tooltip({ content, good, bad, example, children, showIcon = true, portal = false }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState<'top' | 'bottom'>('top');
  // Snapshot of the trigger's viewport position — only used in portal mode.
  const [triggerRect, setTriggerRect] = useState<{ center: number; top: number; bottom: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();

  const measure = useCallback(() => {
    if (!ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setTriggerRect({ center: r.left + r.width / 2, top: r.top, bottom: r.bottom });
    setPos(r.top < 160 ? 'bottom' : 'top');
  }, []);

  useEffect(() => {
    if (visible && ref.current) {
      if (portal) {
        measure();
      } else {
        const rect = ref.current.getBoundingClientRect();
        setPos(rect.top < 160 ? 'bottom' : 'top');
      }
    }
  }, [visible, portal, measure]);

  // In portal mode, follow the trigger while it moves under scrolls/resizes.
  useEffect(() => {
    if (!visible || !portal) return;
    window.addEventListener('scroll', measure, true);   // capture: catches inner scroll containers
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [visible, portal, measure]);

  // Fixed-position coordinates for portal mode (tooltip box is w-64 = 256px).
  let fixedStyle: React.CSSProperties | undefined;
  if (portal && visible && triggerRect) {
    const margin = 136; // half tooltip width + gutter
    const left = Math.min(Math.max(triggerRect.center, margin), window.innerWidth - margin);
    const placeAbove = pos === 'top';
    fixedStyle = placeAbove
      ? { left, top: triggerRect.top - 8, transform: 'translate(-50%, -100%)' }
      : { left, top: triggerRect.bottom + 8, transform: 'translate(-50%, 0)' };
  }

  return (
    <div
      ref={ref}
      tabIndex={0}
      aria-describedby={id}
      className="relative inline-flex items-center gap-1 rounded-sm"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') setVisible(false);
      }}
    >
      {children}
      {showIcon && (
        <HelpCircle size={12} className="text-[#888] hover:text-cyan-400 cursor-help shrink-0 transition-colors" aria-hidden="true" />
      )}
      {visible && portal && (
        createPortal(
          <div
            id={id}
            role="tooltip"
            style={{ ...fixedStyle, position: 'fixed' }}
            className={`z-[9999] w-64 max-w-[calc(100vw-1rem)] bg-[#0e1117] border border-[#ffffff20] rounded-lg shadow-2xl p-3 text-left pointer-events-none`}
          >
            <TooltipBody content={content} good={good} bad={bad} example={example} />
            <div
              className={`absolute left-1/2 -translate-x-1/2 w-2 h-2 bg-[#0e1117] border-[#ffffff20] rotate-45
                ${pos === 'top' ? 'top-full -mt-1 border-b border-r' : 'bottom-full -mb-1 border-t border-l'}`}
            />
          </div>,
          document.body,
        )
      )}
      {visible && !portal && (
        <div
          id={id}
          role="tooltip"
          className={`absolute z-50 w-64 max-w-[calc(100vw-1rem)] bg-[#0e1117] border border-[#ffffff20] rounded-lg shadow-2xl p-3 text-left pointer-events-none
            ${pos === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            left-1/2 -translate-x-1/2`}
        >
          <TooltipBody content={content} good={good} bad={bad} example={example} />
          <div
            className={`absolute left-1/2 -translate-x-1/2 w-2 h-2 bg-[#0e1117] border-[#ffffff20] rotate-45
              ${pos === 'top' ? 'top-full -mt-1 border-b border-r' : 'bottom-full -mb-1 border-t border-l'}`}
          />
        </div>
      )}
    </div>
  );
}
