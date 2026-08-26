import { Star } from 'lucide-react';
import { useWatchlist } from '../lib/WatchlistContext';

interface StarButtonProps {
  symbol: string;
  size?: number;
}

export function StarButton({ symbol, size = 12 }: StarButtonProps) {
  const { isWatched, toggle } = useWatchlist();
  const watched = isWatched(symbol);

  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); toggle(symbol); }}
      aria-pressed={watched}
      aria-label={watched ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`}
      title={watched ? 'Remove from watchlist' : 'Add to watchlist'}
      className={`inline-flex items-center justify-center w-6 h-6 rounded transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-400/50 ${
        watched
          ? 'text-yellow-400 hover:text-yellow-300'
          : 'text-[#888] hover:text-yellow-400'
      }`}
    >
      <Star size={size} fill={watched ? 'currentColor' : 'none'} aria-hidden="true" />
    </button>
  );
}
