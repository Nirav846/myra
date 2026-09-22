import { Loader2 } from 'lucide-react';

export interface ScannerLoadingStateProps {
  /** Message shown next to the spinner, e.g. "Scanning...". */
  label?: string;
  /** Approximate number of skeleton rows to render (defaults to a typical scanner row count). */
  rowCount?: number;
}

const COLUMN_MIX = [0.18, 0.12, 0.1, 0.12, 0.16, 0.14];

/**
 * ScannerLoadingState - standardized loading placeholder for scanner views.
 *
 * Replaces the repeated "spinner + opacity-50 table" pattern found across views.
 * Renders skeleton rows shaped like a typical scanner table (ticker column wider
 * on the left, tighter numeric columns to the right) instead of dimming real rows.
 */
export function ScannerLoadingState({
  label = 'Scanning...',
  rowCount = 8,
}: ScannerLoadingStateProps) {
  return (
    <div className="w-full" role="status" aria-live="polite">
      <div className="flex items-center gap-2 px-3 py-3">
        <Loader2 size={14} className="animate-spin text-signal-positive" aria-hidden="true" />
        <span className="text-xs font-mono uppercase tracking-wider text-text-muted">{label}</span>
      </div>
      <div className="flex flex-col gap-1.5 px-3 pb-3">
        {Array.from({ length: rowCount }).map((_, row) => (
          <div
            key={row}
            className="flex items-center gap-3 h-[46px] px-3 rounded-md bg-surface-panel/40 border border-white/5"
            aria-hidden="true"
          >
            {COLUMN_MIX.map((width, col) => (
              <div
                key={col}
                className="skeleton skeleton-wave h-3 rounded"
                style={{ width: `${width * 100}%` }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export default ScannerLoadingState;