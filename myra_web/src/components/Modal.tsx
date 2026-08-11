import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  maxWidth?: string;
  accentBorder?: string;
  titleClass?: string;
}

/**
 * Accessible modal dialog (Sprint 4).
 * - role="dialog" + aria-modal="true" + aria-labelledby
 * - Focus trapped inside: Tab/Shift+Tab cycle within dialog
 * - Focus restored to previously-focused element on close
 * - Escape closes
 * - Click on backdrop closes
 */
export default function Modal({
  open,
  onClose,
  title,
  children,
  maxWidth = 'max-w-md',
  accentBorder = 'border-[#ffffff1a]',
  titleClass = 'text-[#fafafa]',
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useRef(`modal-title-${Math.random().toString(36).slice(2, 8)}`).current;

  // Keep latest onClose in a ref so the focus-trap effect depends only on [open],
  // avoiding focus thrash when the parent re-renders with fresh inline closures.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Focus trap + restore
  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    if (!dialog) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';
    const getFocusable = () =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement
      );

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;

      const focusable = getFocusable();
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    // Move focus into dialog (first focusable, or dialog itself)
    const focusable = getFocusable();
    (focusable[0] ?? dialog).focus();

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 bg-black/80 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`bg-[#1a1c24] border rounded-lg p-6 ${accentBorder} ${maxWidth} w-full mx-4 max-h-[90vh] overflow-y-auto`}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 id={titleId} className={`text-sm font-semibold ${titleClass}`}>
            {title}
          </h3>
          <button
            onClick={onClose}
            className="text-[#888] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#888] rounded"
            aria-label="Close dialog"
          >
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
