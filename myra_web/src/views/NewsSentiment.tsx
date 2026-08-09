import { useState } from 'react';
import { API_BASE } from '../config';

interface NewsItem {
  headline: string;
  source: string;
  date: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  confidence: number;
}

export default function NewsSentimentView() {
  const [ticker, setTicker] = useState('');
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cached, setCached] = useState(false);

  const fetchNews = async (refresh = false) => {
    if (!ticker.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/sentiment/${ticker.toUpperCase()}?refresh=${refresh}`);
      const data = await res.json();
      if (data.status === 'error') {
        setError(data.error || 'Unknown error');
        setNews([]);
      } else {
        setNews(data.news || []);
        setCached(data.cached || false);
      }
    } catch {
      setError('Failed to fetch news. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <h2 className="text-lg font-semibold">📰 News Sentiment</h2>

      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Enter NSE ticker (e.g., RELIANCE)"
          value={ticker}
          onChange={e => setTicker(e.target.value.toUpperCase())}
          className="bg-[#ffffff0a] border border-[#ffffff1a] rounded px-3 py-1.5 text-sm text-[#fafafa] flex-1"
          onKeyDown={e => e.key === 'Enter' && fetchNews(false)}
        />
        <button onClick={() => fetchNews(false)} disabled={loading}
          className="px-4 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50">
          {loading ? 'Loading…' : 'Search'}
        </button>
        <button onClick={() => fetchNews(true)} disabled={loading}
          className="px-4 py-1.5 bg-[#ffffff0a] border border-[#ffffff1a] text-[#888] rounded text-sm hover:text-white disabled:opacity-50"
          title="Force refresh from GDELT">
          🔄
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 text-red-400 text-sm">{error}</div>}

      {news.length > 0 && (
        <>
          <div className="text-[10px] text-[#888]">{cached ? '📦 From cache (≤6 hours old)' : '🆕 Live from GDELT'} · {news.length} articles</div>
          <div className="space-y-2">
            {news.map((item, i) => (
              <div key={i} className="bg-[#ffffff05] border border-[#ffffff0a] rounded p-3 flex justify-between gap-4">
                <div className="flex-1">
                  <p className="text-sm text-[#fafafa]">{item.headline}</p>
                  <p className="text-[10px] text-[#888] mt-1">{item.source} · {item.date}</p>
                </div>
                <div className="flex items-center gap-1 text-sm whitespace-nowrap">
                  <span className={item.sentiment === 'positive' ? 'text-green-400' : item.sentiment === 'negative' ? 'text-red-400' : 'text-[#888]'}>
                    {item.sentiment === 'positive' ? '📈' : item.sentiment === 'negative' ? '📉' : '➖'} {item.sentiment}
                  </span>
                  <span className="text-[10px] text-[#666]">{(item.confidence * 100).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {!loading && news.length === 0 && !error && (
        <div className="text-center text-[#888] py-12">
          <p className="text-3xl mb-2">🔍</p>
          <p className="text-sm">Enter a ticker to see recent news with AI sentiment</p>
          <p className="text-[10px] mt-1">Powered by GDELT + FinBERT · Cached for 6 hours</p>
        </div>
      )}
    </div>
  );
}
