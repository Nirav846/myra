import { useState, useRef, useEffect } from 'react';
import { HelpCircle } from 'lucide-react';

interface TooltipProps {
  content: string;
  good?: string;
  bad?: string;
  example?: string;
  children?: React.ReactNode;
  showIcon?: boolean;
}

export function Tooltip({ content, good, bad, example, children, showIcon = true }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState<'top' | 'bottom'>('top');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visible && ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setPos(rect.top < 160 ? 'bottom' : 'top');
    }
  }, [visible]);

  return (
    <div
      ref={ref}
      className="relative inline-flex items-center gap-1"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {showIcon && (
        <HelpCircle size={11} className="text-[#555] hover:text-cyan-400 cursor-help shrink-0 transition-colors" />
      )}
      {visible && (
        <div
          className={`absolute z-50 w-64 bg-[#0e1117] border border-[#ffffff20] rounded-lg shadow-2xl p-3 text-left pointer-events-none
            ${pos === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            left-1/2 -translate-x-1/2`}
        >
          <p className="text-[11px] text-[#ddd] font-sans leading-relaxed">{content}</p>
          {good && (
            <div className="mt-2 flex items-start gap-1.5">
              <span className="text-green-400 text-[10px] font-bold shrink-0 mt-0.5">✓ Good:</span>
              <span className="text-[10px] text-green-300/80">{good}</span>
            </div>
          )}
          {bad && (
            <div className="mt-1 flex items-start gap-1.5">
              <span className="text-red-400 text-[10px] font-bold shrink-0 mt-0.5">✗ Watch:</span>
              <span className="text-[10px] text-red-300/80">{bad}</span>
            </div>
          )}
          {example && (
            <div className="mt-2 pt-2 border-t border-[#ffffff10]">
              <span className="text-[9px] text-[#666] italic">{example}</span>
            </div>
          )}
          <div
            className={`absolute left-1/2 -translate-x-1/2 w-2 h-2 bg-[#0e1117] border-[#ffffff20] rotate-45
              ${pos === 'top' ? 'top-full -mt-1 border-b border-r' : 'bottom-full -mb-1 border-t border-l'}`}
          />
        </div>
      )}
    </div>
  );
}
