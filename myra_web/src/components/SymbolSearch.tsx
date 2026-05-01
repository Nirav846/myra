import React, { useState, useEffect, useRef } from 'react';
import { Search } from 'lucide-react';
import { Librarian } from '../lib/Librarian';

interface SymbolSearchProps {
  lib: Librarian;
  onSymbolSelect: (symbol: string) => void;
  placeholder?: string;
  className?: string;
  initialValue?: string;
}

export function SymbolSearch({ lib, onSymbolSelect, placeholder = "Search symbol...", className = "", initialValue = "", clearOnSelect = false }: SymbolSearchProps & { clearOnSelect?: boolean }) {
  const [inputValue, setInputValue] = useState(initialValue);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Click outside to close
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  // Fetch suggestions when input changes
  useEffect(() => {
    const fetchSuggestions = async () => {
      const term = inputValue.trim().toUpperCase();
      if (term.length < 2) {
        setSuggestions([]);
        return;
      }
      
      setLoading(true);
      try {
        // Query the database for matching symbols
        const query = `SELECT DISTINCT symbol FROM technical_data WHERE symbol LIKE '${term}%' LIMIT 10`;
        const result = await lib.executeQuery('_tech_conn', query, {}, 2000);
        
        let foundSymbols: string[] = [];
        if (result && result.length > 0) {
          foundSymbols = result.map((r: any) => r.symbol);
        } else {
          // Fallback demo symbols
          const demoSymbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY', 'ITC', 'SBIN', 'BHARTIARTL', 'BAJFINANCE', 'KOTAKBANK', 'NIFTY', 'BANKNIFTY'];
          foundSymbols = demoSymbols.filter(s => s.startsWith(term));
        }
        
        setSuggestions(foundSymbols);
        setSelectedIndex(-1);
      } catch (e) {
        // Fallback demo symbols on error
        const demoSymbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY', 'ITC', 'SBIN', 'BHARTIARTL', 'BAJFINANCE', 'KOTAKBANK', 'NIFTY', 'BANKNIFTY'];
        setSuggestions(demoSymbols.filter(s => s.startsWith(term)));
      } finally {
        setLoading(false);
      }
    };

    const timerObj = setTimeout(() => {
      fetchSuggestions();
    }, 300);

    return () => clearTimeout(timerObj);
  }, [inputValue, lib]);

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
        handleSelect(suggestions[selectedIndex]);
      } else if (inputValue.trim()) {
        handleSelect(inputValue.trim().toUpperCase());
      }
    } else if (e.key === 'Escape') {
      setShowSuggestions(false);
    }
  };

  const handleSelect = (sym: string) => {
    if (clearOnSelect) {
      setInputValue('');
    } else {
      setInputValue(sym);
    }
    setShowSuggestions(false);
    onSymbolSelect(sym);
  };

  return (
    <div className={`relative ${className}`} ref={wrapperRef}>
      <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#666]" />
      <input 
        ref={inputRef}
        type="text" 
        value={inputValue} 
        onChange={e => {
          setInputValue(e.target.value.toUpperCase());
          setShowSuggestions(true);
        }}
        onFocus={() => {
          if (inputValue.length >= 2) setShowSuggestions(true);
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className="w-full bg-[#0e1117] border border-[#ffffff1a] pl-8 pr-3 py-1.5 focus:border-cyan-500 rounded text-xs text-[#ccc] font-mono outline-none uppercase transition-colors"
      />
      {loading && <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin"></div>}
      
      {showSuggestions && suggestions.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl overflow-hidden z-50">
          {suggestions.map((sym, idx) => (
            <button
              key={sym}
              className={`w-full text-left px-3 py-2 text-xs font-mono transition-colors ${idx === selectedIndex ? 'bg-cyan-500/20 text-cyan-300' : 'text-[#ccc] hover:bg-[#ffffff0a] hover:text-white'}`}
              onClick={() => handleSelect(sym)}
            >
              {sym}
            </button>
          ))}
        </div>
      )}
      
      {showSuggestions && inputValue.length >= 2 && suggestions.length === 0 && !loading && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl overflow-hidden z-50 p-3 text-center text-xs text-[#666]">
          No symbols found
        </div>
      )}
    </div>
  );
}
