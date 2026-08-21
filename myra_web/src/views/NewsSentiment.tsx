import { useState } from 'react';
import { API_BASE } from '../config';
import FundTractionButton from '../components/FundTractionButton';

interface NewsItem {
  headline: string;
  source: string;
  date: string;
  url?: string;
  language?: string;
  tone?: number | null;
  domain?: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  confidence: number;
  company_name?: string;
}

interface AiOpinion {
  ticker: string;
  signal: 'BUY' | 'SELL' | 'HOLD';
  reason: string;
  confidence: number;
  source: 'gemini' | 'degraded';
  cached: boolean;
  summary: string;
}

export default function NewsSentimentView() {
  const [ticker, setTicker] = useState('');
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cached, setCached] = useState(false);

  const [aiOpinion, setAiOpinion] = useState<AiOpinion | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');

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

  const fetchAiOpinion = async () => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setAiLoading(true);
    setAiError('');
    try {
      const res = await fetch(`${API_BASE}/ai-opinion/${t}`);
      if (!res.ok) {
        setAiError(`AI opinion request failed (${res.status})`);
        setAiOpinion(null);
        return;
      }
      const data: AiOpinion = await res.json();
      setAiOpinion(data);
    } catch {
      setAiError('Failed to fetch AI opinion. Is the backend running?');
    } finally {
      setAiLoading(false);
    }
  };

  const clearAiOpinion = () => {
    setAiOpinion(null);
    setAiError('');
  };

  const posCount = news.filter(n => n.sentiment === 'positive').length;
  const negCount = news.filter(n => n.sentiment === 'negative').length;
  const neuCount = news.filter(n => n.sentiment === 'neutral').length;

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
          title="Force refresh from news sources (GDELT + Groww)">
          🔄
        </button>
        <FundTractionButton symbols={ticker.trim() ? [ticker.trim().toUpperCase()] : []} />
      </div>

      {/* AI Opinion button row */}
      <div className="flex gap-2 items-center">
        <button onClick={fetchAiOpinion} disabled={aiLoading || !ticker.trim()}
          className="px-4 py-1.5 bg-[#ffffff0a] border border-[#ffffff1a] text-[#888] rounded text-sm hover:text-white disabled:opacity-50">
          {aiLoading ? '⏳ Thinking…' : '🤖 AI Opinion'}
        </button>
        {aiOpinion && (
          <button onClick={clearAiOpinion}
            className="px-3 py-1.5 bg-[#ffffff0a] border border-[#ffffff1a] text-[#888] rounded text-sm hover:text-white">
            🗑 Clear
          </button>
        )}
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 text-red-400 text-sm">{error}</div>}

      {/* AI Opinion panel */}
      {aiOpinion && (
        <div className="bg-[#ffffff05] border border-[#ffffff0a] rounded p-3 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={
              aiOpinion.signal === 'BUY' ? 'text-green-400 font-semibold' :
              aiOpinion.signal === 'SELL' ? 'text-red-400 font-semibold' :
              'text-[#888] font-semibold'
            }>
              🤖 AI: {aiOpinion.signal}
            </span>
            <span className="text-[12px] text-[#888]">{(aiOpinion.confidence * 100).toFixed(0)}%</span>
            {aiOpinion.cached && <span className="text-[12px] text-[#888]">📦 cached</span>}
          </div>
          <div className="text-[12px] text-[#888]">
            {aiOpinion.source === 'gemini' ? 'Gemini · 24h cache' : '⚙️ Degraded (no API key or LLM unavailable)'}
          </div>
          <p className="text-[12px] text-[#aaa] mt-1">{aiOpinion.reason}</p>
          {aiOpinion.summary && (
            <details className="mt-1">
              <summary className="text-[12px] text-[#888] cursor-pointer hover:text-[#aaa]">
                Show data the model evaluated
              </summary>
              <pre className="text-[12px] font-mono text-[#888] whitespace-pre-wrap mt-1 p-2 bg-[#ffffff05] rounded">
                {aiOpinion.summary}
              </pre>
            </details>
          )}
        </div>
      )}

      {aiError && <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 text-red-400 text-sm">{aiError}</div>}

      {news.length > 0 && (
        <>
          <div className="flex items-center gap-3 text-[12px]">
            <span className="text-green-400">📈 {posCount} positive</span>
            <span className="text-red-400">📉 {negCount} negative</span>
            <span className="text-[#888]">➖ {neuCount} neutral</span>
            <span className="text-[#888]">·</span>
            <span className="text-[#888]">{cached ? '📦 Cached (≤6h)' : '🆕 Live'}</span>
            <span className="text-[#888]">·</span>
            <span className="text-[#888]">{news.length} articles</span>
          </div>
          <div className="space-y-2">
            {news.map((item, i) => (
              <div key={i} className="bg-[#ffffff05] border border-[#ffffff0a] rounded p-3 flex justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-[#fafafa] leading-snug">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:text-cyan-400 transition-colors">
                        {item.headline}
                      </a>
                    ) : (
                      item.headline
                    )}
                  </p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-[12px] text-[#888]">
                    {item.company_name && item.company_name.toUpperCase() !== ticker.trim().toUpperCase() && (
                      <span className="text-cyan-400/80">{item.company_name}</span>
                    )}
                    <span>{item.source || 'GDELT'}</span>
                    {item.domain && <span className="text-[#888]">{item.domain}</span>}
                    <span>{item.date ? new Date(item.date).toLocaleDateString('en-IN', {day:'numeric', month:'short', year:'numeric'}) : ''}</span>
                    {item.language && item.language !== 'English' && <span className="text-[#888]">🌐 {item.language}</span>}
                    {item.tone != null && (
                      <span className={item.tone > 0 ? 'text-green-500' : item.tone < 0 ? 'text-red-500' : 'text-[#888]'}>
                        Tone: {item.tone > 0 ? '+' : ''}{item.tone.toFixed(1)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 text-sm whitespace-nowrap">
                  <span className={item.sentiment === 'positive' ? 'text-green-400' : item.sentiment === 'negative' ? 'text-red-400' : 'text-[#888]'}>
                    {item.sentiment === 'positive' ? '📈' : item.sentiment === 'negative' ? '📉' : '➖'} {item.sentiment}
                  </span>
                  <span className="text-[12px] text-[#888]">{(item.confidence * 100).toFixed(0)}%</span>
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
          <p className="text-[12px] mt-1">Powered by GDELT + Groww + FinBERT · Cached for 6 hours</p>
        </div>
      )}
    </div>
  );
}
