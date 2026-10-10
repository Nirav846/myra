import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Search, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { API_BASE } from '../config';

interface Suggestion {
  symbol: string;
  name: string;
  score: number;
  has_price: boolean;
}

interface TickerSuggestProps {
  /** Company name from the fund filing — seeds the suggestion list. */
  company: string;
  onPick: (symbol: string) => void;
  onCancel: () => void;
  autoFocus?: boolean;
}

/**
 * Inline ticker picker for an unmapped MF holding.
 *
 * Mirrors the Technical Chart's symbol search (type-ahead against the local
 * symbol universe) but is seeded from the holding's company name, so the right
 * ticker is usually the first suggestion.  Calls /api/mf-smart-money/suggest,
 * which ranks symbols_master + symbol_alias + name_to_nse.csv by name similarity
 * and flags which candidates actually have price data.
 */
export function TickerSuggest({ company, onPick, onCancel, autoFocus = true }: TickerSuggestProps) {
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        onCancel();
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onCancel]);

  const fetchSuggestions = useCallback(
    async (term: string) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ company });
        if (term.trim()) params.set('q', term.trim());
        const res = await fetch(`${API_BASE}/mf-smart-money/suggest?${params.toString()}`);
        if (!res.ok) throw new Error('suggest failed');
        const data = await res.json();
        setSuggestions(data.suggestions ?? []);
        setSelectedIndex(-1);
      } catch {
        setSuggestions([]);
      } finally {
        setLoading(false);
      }
    },
    [company],
  );

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchSuggestions(query), 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, fetchSuggestions]);

  const pick = useCallback(
    (sym: string) => {
      if (sym) onPick(sym);
    },
    [onPick],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev < suggestions.length - 1 ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < suggestions.length) {
        pick(suggestions[selectedIndex].symbol);
      } else if (query.trim()) {
        pick(query.trim().toUpperCase());
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <div className="relative" ref={wrapperRef}>
      <div className="relative flex items-center">
        <Search size={11} className="absolute left-2 text-[#888] pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          onKeyDown={handleKeyDown}
          placeholder="Ticker…"
          className="w-32 bg-[#0e1117] border border-[#ffffff1a] pl-6 pr-6 py-0.5 focus:border-emerald-500 rounded text-[11px] text-[#ccc] font-mono outline-none uppercase"
        />
        {loading && (
          <Loader2
            size={11}
            className="absolute right-1.5 text-emerald-500 animate-spin pointer-events-none"
          />
        )}
      </div>

      {suggestions.length > 0 && (
        <div className="absolute top-full left-0 mt-1 w-72 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl overflow-hidden z-50 max-h-56 overflow-y-auto">
          {suggestions.map((s, idx) => (
            <button
              key={s.symbol}
              onClick={() => pick(s.symbol)}
              title={s.has_price ? 'Has price data' : 'No price data yet'}
              className={`w-full text-left px-2 py-1.5 text-[11px] transition-colors flex items-center gap-2 ${
                idx === selectedIndex
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : 'text-[#ccc] hover:bg-[#ffffff0a] hover:text-white'
              }`}
            >
              {s.has_price ? (
                <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
              ) : (
                <AlertCircle size={11} className="text-amber-400 shrink-0" />
              )}
              <span className="font-bold font-mono shrink-0">{s.symbol}</span>
              <span className="text-[#888] truncate">{s.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
