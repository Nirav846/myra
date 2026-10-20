import type { KeyboardEvent } from 'react';
import { Search, X } from 'lucide-react';

export interface SymbolFilterInputProps {
  /** Current raw query (owned by the view via `useSymbolFilter`). */
  value: string;
  onChange: (next: string) => void;
  /** Called by the clear button and by Escape when the box is non-empty. */
  onClear: () => void;
  /**
   * Rows kept by the query. When supplied, the input reports "3 of 42" and
   * shouts when nothing matches, so the no-match state is visible even if the
   * table below is empty.
   */
  matchCount?: number;
  /** Rows before the query, used for the "3 of 42" denominator. */
  totalCount?: number;
  label?: string;
  placeholder?: string;
  className?: string;
}

/**
 * Compact symbol filter for scanner result tables.
 *
 * Purely presentational: the view owns the query state and applies
 * `filterBySymbol` inside its existing filter memo. No debounce is needed -
 * filtering is an in-memory `Array#filter` over the already-loaded candidate
 * list, not a request per keystroke.
 */
export default function SymbolFilterInput({
  value,
  onChange,
  onClear,
  matchCount,
  totalCount,
  label = 'Filter results by symbol',
  placeholder = 'Symbol…',
  className = '',
}: SymbolFilterInputProps) {
  const active = value.trim().length > 0;
  const noMatch = active && matchCount === 0;
  const showCount = active && !noMatch && matchCount !== undefined && totalCount !== undefined;

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && value) {
      e.preventDefault();
      onClear();
    }
  };

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="relative">
        <Search
          size={12}
          className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[#888]"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label={label}
          autoComplete="off"
          spellCheck={false}
          className={`w-36 sm:w-44 bg-[#1a1c24] border rounded pl-7 pr-7 py-1 text-[12px] font-mono uppercase text-[#fafafa] placeholder-[#666] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-cyan-500/40 ${
            noMatch ? 'border-red-500/50' : 'border-[#ffffff1a] focus:border-cyan-500/60'
          }`}
        />
        {value && (
          <button
            type="button"
            onClick={onClear}
            aria-label="Clear symbol filter"
            title="Clear symbol filter (Esc)"
            className="absolute right-1 top-1/2 -translate-y-1/2 inline-flex items-center justify-center w-5 h-5 rounded text-[#888] hover:text-white hover:bg-[#ffffff14] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40"
          >
            <X size={11} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* aria-live so the no-match state is announced, not just coloured. */}
      <span aria-live="polite" className="text-[11px] font-mono whitespace-nowrap">
        {noMatch ? (
          <span className="text-red-400">No symbol matches &ldquo;{value.trim()}&rdquo;</span>
        ) : showCount ? (
          <span className="text-[#888]">
            {matchCount} of {totalCount}
          </span>
        ) : null}
      </span>
    </div>
  );
}
