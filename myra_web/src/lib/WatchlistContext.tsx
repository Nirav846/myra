import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

const STORAGE_KEY = 'myra_watchlist';

interface WatchlistContextType {
  watchlist: string[];
  isWatched: (symbol: string) => boolean;
  toggle: (symbol: string) => void;
  count: number;
}

const WatchlistContext = createContext<WatchlistContextType>({
  watchlist: [],
  isWatched: () => false,
  toggle: () => {},
  count: 0,
});

export function WatchlistProvider({ children }: { children: ReactNode }) {
  const [watchlist, setWatchlist] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(watchlist));
  }, [watchlist]);

  const isWatched = (symbol: string) => watchlist.includes(symbol.toUpperCase());

  const toggle = (symbol: string) => {
    const sym = symbol.toUpperCase();
    setWatchlist(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    );
  };

  return (
    <WatchlistContext.Provider value={{ watchlist, isWatched, toggle, count: watchlist.length }}>
      {children}
    </WatchlistContext.Provider>
  );
}

export const useWatchlist = () => useContext(WatchlistContext);
