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
      onClick={(e) => { e.stopPropagation(); toggle(symbol); }}
      className={`transition-colors shrink-0 ${
        watched
          ? 'text-yellow-400 hover:text-yellow-300'
          : 'text-[#444] hover:text-yellow-400'
      }`}
      title={watched ? 'Remove from watchlist' : 'Add to watchlist'}
    >
      <Star size={size} fill={watched ? 'currentColor' : 'none'} />
    </button>
  );
}
