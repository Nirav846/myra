import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Search, Loader2 } from 'lucide-react';
import { API_ROOT } from '../../config';



interface SymbolOption {
  symbol: string;
  sector: string;
  industry: string;
}

interface SymbolAutocompleteProps {
  value: string | null;
  onSelect: (symbol: string) => void;
  placeholder?: string;
  className?: string;
}

export function SymbolAutocomplete({ value, onSelect, placeholder = 'Search symbol...', className = '' }: SymbolAutocompleteProps) {
  const [inputValue, setInputValue] = useState(value || '');
  const [suggestions, setSuggestions] = useState<SymbolOption[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    setInputValue(value || '');
  }, [value]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const fetchSuggestions = useCallback(async (term: string) => {
    if (term.length < 2) {
      setSuggestions([]);
      setIsOpen(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_ROOT}/api/search/symbols?q=${encodeURIComponent(term)}`);
      if (!res.ok) throw new Error('Search failed');
      const data: SymbolOption[] = await res.json();
      setSuggestions(data);
      setIsOpen(data.length > 0 || term.length >= 2);
      setSelectedIndex(-1);
    } catch {
      setSuggestions([]);
      setIsOpen(false);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value.toUpperCase();
    setInputValue(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchSuggestions(val), 200);
  };

  const handleSelect = (sym: string) => {
    setInputValue(sym);
    setIsOpen(false);
    onSelect(sym);
    inputRef.current?.focus();
  };

  const handleClear = () => {
    setInputValue('');
    setSuggestions([]);
    setIsOpen(false);
    onSelect('');
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev < suggestions.length - 1 ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev > 0 ? prev - 1 : -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < suggestions.length) {
        handleSelect(suggestions[selectedIndex].symbol);
      } else if (inputValue.trim().length >= 1) {
        handleSelect(inputValue.trim());
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
    }
  };

  return (
    <div className={`relative ${className}`} ref={wrapperRef}>
      <div className="relative flex items-center">
        <Search size={12} className="absolute left-2 text-[#666] pointer-events-none" />
        <input
          id="symbol-search-autocomplete"
          name="symbol-autocomplete"
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onFocus={() => { if (suggestions.length > 0 || inputValue.length >= 2) setIsOpen(true); }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="w-full bg-[#0e1117] border border-[#ffffff1a] pl-7 pr-7 py-1.5 focus:border-cyan-500 rounded text-[10px] text-[#ccc] font-mono outline-none uppercase transition-colors"
        />
        {loading ? (
          <Loader2 size={12} className="absolute right-2 text-cyan-500 animate-spin pointer-events-none" />
        ) : value && inputValue === value ? (
          <button onClick={handleClear} className="absolute right-2 text-[#666] hover:text-red-400 transition-colors" title="Clear">
            <X size={12} />
          </button>
        ) : null}
      </div>

      {isOpen && suggestions.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl overflow-hidden z-50 max-h-48 overflow-y-auto">
          {suggestions.map((opt, idx) => (
            <button
              key={opt.symbol}
              className={`w-full text-left px-3 py-2 text-[10px] font-mono transition-colors flex items-center justify-between ${
                idx === selectedIndex ? 'bg-cyan-500/20 text-cyan-300' : 'text-[#ccc] hover:bg-[#ffffff0a] hover:text-white'
              }`}
              onClick={() => handleSelect(opt.symbol)}
            >
              <span className="font-bold">{opt.symbol}</span>
              <span className="text-[#666] truncate ml-2">{opt.sector || opt.industry || ''}</span>
            </button>
          ))}
        </div>
      )}

      {isOpen && inputValue.length >= 2 && suggestions.length === 0 && !loading && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl overflow-hidden z-50 p-3 text-center text-[10px] text-[#666]">
          No symbols found
        </div>
      )}
    </div>
  );
}
