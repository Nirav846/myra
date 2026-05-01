import { useState, useEffect } from 'react';
import { RefreshCw, AlertCircle, CheckCircle } from 'lucide-react';

interface FvgData {
  symbol: string;
  date: string;
  close: number;
  volume: number;
  fvg_bottom: number;
  fvg_top: number;
  fvg_type: string;
  gap_size: number;
}

export default function FvgScanner() {
  const [data, setData] = useState<FvgData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchFvgData = async () => {
    setLoading(true);
    setError(null);

    const query = `
WITH RankedData AS (
  SELECT 
    symbol, date, open, high, low, close, volume,
    LAG(high, 2) OVER(PARTITION BY symbol ORDER BY date) as day1_high,
    LAG(low, 2) OVER(PARTITION BY symbol ORDER BY date) as day1_low,
    ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY date DESC) as rn
  FROM technical_data
)
SELECT 
  symbol, 
  date,
  close,
  volume,
  day1_high as fvg_bottom, 
  low as fvg_top, 
  'Bullish FVG' as fvg_type,
  ROUND(low - day1_high, 2) as gap_size
FROM RankedData
WHERE low > day1_high 
  AND day1_high IS NOT NULL 
  AND rn <= 5

UNION ALL

SELECT 
  symbol, 
  date,
  close, 
  volume,
  high as fvg_bottom, 
  day1_low as fvg_top, 
  'Bearish FVG' as fvg_type,
  ROUND(day1_low - high, 2) as gap_size
FROM RankedData
WHERE high < day1_low 
  AND day1_low IS NOT NULL 
  AND rn <= 5

ORDER BY date DESC, gap_size DESC
LIMIT 50;
    `;

    try {
      const response = await fetch('http://localhost:8000/api/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          database: '_tech_conn',
          query: query.trim(),
          args: {}
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const result = await response.json();
      setData(result.data || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch FVG data');
      console.error('FVG Scanner error:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFvgData();
  }, []);

  return (
    <div className="bg-[#1a1c24] rounded-lg border border-[#ffffff1a] p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-lg font-bold text-white mb-1">FVG Scanner (Daily)</h2>
          <p className="text-xs text-[#888]">Fair Value Gaps detected in the last 5 trading days</p>
        </div>
        <button
          onClick={fetchFvgData}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-500/30 rounded text-cyan-400 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded mb-4">
          <AlertCircle size={16} className="text-red-400" />
          <span className="text-sm text-red-400">{error}</span>
        </div>
      )}

      {loading && data.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <RefreshCw size={24} className="text-cyan-400 animate-spin" />
        </div>
      )}

      {!loading && data.length === 0 && !error && (
        <div className="flex items-center justify-center py-12 text-[#888]">
          <CheckCircle size={24} className="mr-2 text-green-400" />
          <span className="text-sm">No recent FVGs found</span>
        </div>
      )}

      {data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[#ffffff1a]">
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Symbol</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Date</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Type</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Gap Size</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Gap Bottom</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Gap Top</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Close</th>
                <th className="pb-3 px-4 text-xs font-semibold text-[#888] uppercase tracking-wider">Volume</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row, idx) => (
                <tr
                  key={idx}
                  className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors"
                >
                  <td className="py-3 px-4 font-mono text-sm font-bold text-white">{row.symbol}</td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">{row.date}</td>
                  <td className="py-3 px-4">
                    <span
                      className={`inline-block px-2 py-1 rounded text-xs font-semibold ${
                        row.fvg_type === 'Bullish FVG'
                          ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                          : 'bg-red-500/20 text-red-400 border border-red-500/30'
                      }`}
                    >
                      {row.fvg_type}
                    </span>
                  </td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">{row.gap_size.toFixed(2)}</td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">{row.fvg_bottom.toFixed(2)}</td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">{row.fvg_top.toFixed(2)}</td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">{row.close.toFixed(2)}</td>
                  <td className="py-3 px-4 font-mono text-sm text-[#ccc]">
                    {row.volume >= 1000000
                      ? (row.volume / 1000000).toFixed(2) + 'M'
                      : (row.volume / 1000).toFixed(1) + 'k'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.length > 0 && (
        <div className="mt-4 text-xs text-[#666] font-mono">
          Showing {data.length} recent FVGs
        </div>
      )}
    </div>
  );
}
