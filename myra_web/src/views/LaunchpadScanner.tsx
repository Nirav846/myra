import { useState, useEffect, useCallback, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, Filter, AlertCircle, ArrowUpRight } from 'lucide-react';

interface Prediction {
  symbol: string;
  sector?: string;
  market_cap?: number;
  trigger_date: string;
  days_since_trigger: number;
  current_price: number;
  predicted_price_lower: number;
  predicted_price_upper: number;
  breakout_probability: number;
  expected_return: number;
  confidence: 'High' | 'Medium' | 'Low';
}

interface LaunchpadResponse {
  predictions?: Prediction[];
  error?: string;
}

const resolveBucket = (mcap: number | undefined) => {
  if (!mcap) return 'Unknown';
  if (mcap > 20000) return 'Large Cap';
  if (mcap > 5000) return 'Mid Cap';
  return 'Small Cap';
};

export default function LaunchpadScannerView({ lib, onNavigate }: { lib: Librarian; onNavigate: (tab: string, symbol: string) => void }) {
  const [data, setData] = useState<Prediction[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [bucketFilter, setBucketFilter] = useState<string>('All');
  const [minProb, setMinProb] = useState<number>(30);

  const fetchPredictions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/predict');
      if (res.ok) {
        const json: LaunchpadResponse = await res.json();
        if (json.error) {
           setError(json.error);
        } else {
           setData(json.predictions || []);
        }
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to fetch predictions');
      }
    } catch (e: any) {
      setError(e.message || 'Error connecting to backend');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPredictions();
  }, [fetchPredictions]);

  const sectors = useMemo(() => {
    if (!data) return [];
    const set = new Set(data.map(d => d.sector || 'Unknown'));
    return Array.from(set).sort();
  }, [data]);

  const buckets = useMemo(() => {
    if (!data) return [];
    const set = new Set(data.map(d => resolveBucket(d.market_cap)));
    return Array.from(set).sort();
  }, [data]);

  const filteredData = useMemo(() => {
    if (!data) return [];
    return data
      .filter(d => sectorFilter === 'All' || (d.sector || 'Unknown') === sectorFilter)
      .filter(d => bucketFilter === 'All' || resolveBucket(d.market_cap) === bucketFilter)
      .filter(d => d.breakout_probability * 100 >= minProb)
      .sort((a, b) => b.breakout_probability - a.breakout_probability);
  }, [data, sectorFilter, bucketFilter, minProb]);

  const avgExpectedReturn = useMemo(() => {
    if (filteredData.length === 0) return 0;
    const total = filteredData.reduce((acc, curr) => acc + curr.expected_return, 0);
    return total / filteredData.length;
  }, [filteredData]);

  const highestConfidence = useMemo(() => {
    if (filteredData.length === 0) return null;
    return filteredData[0]; // Already sorted by probability
  }, [filteredData]);

  if (error || (data && data.length === 0)) {
    return (
      <div className="flex flex-col h-full relative">
         <div className="flex items-center gap-3 mb-4 p-4 pb-0">
          <div className="bg-red-500/20 p-2 rounded">
            <Rocket className="text-red-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Launchpad Scanner</h1>
            <p className="text-sm text-[#888]">Detecting setups before breakout</p>
          </div>
        </div>
        <div className="p-4 flex-1 flex items-center justify-center">
            <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
                <AlertCircle size={32} className="opacity-50" />
                <p>{error ? `Error: ${error}` : 'No launchpad model trained yet. Run labelling and training from ML Lab.'}</p>
                <button 
                  onClick={() => onNavigate('ML Lab', '')}
                  className="mt-4 px-4 py-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] rounded text-white text-xs transition-colors"
                >
                  Go to ML Lab
                </button>
            </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full relative space-y-4 p-4">
      <div className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-red-500/20 p-2 rounded">
            <Rocket className="text-red-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Launchpad Scanner</h1>
            <p className="text-xs font-mono text-[#888]">Quantifying Breakout Mechanics</p>
          </div>
        </div>
        <div>
          <button 
             onClick={fetchPredictions}
             disabled={loading}
             className="px-4 py-2 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs font-mono text-[#ccc] hover:bg-[#ffffff1a] transition-colors"
          >
             {loading ? 'Refreshing...' : 'Refresh Data'}
          </button>
        </div>
      </div>

      {loading && !data ? (
         <div className="flex-1 flex justify-center items-center font-mono text-[#888] animate-pulse">Running Inferences...</div>
      ) : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Stocks in Launch Window</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Avg Expected Return</div>
              <div className={`text-2xl font-bold ${avgExpectedReturn > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(avgExpectedReturn * 100).toFixed(2)}%
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Highest Confidence Setup</div>
              <div className="text-xl font-bold text-[#fafafa] flex items-center gap-2">
                {highestConfidence ? (
                   <>
                    <span 
                      className="cursor-pointer hover:text-cyan-400 decoration-cyan-400/50 underline underline-offset-4"
                      onClick={() => onNavigate('Technical Chart', highestConfidence.symbol)}
                    >
                        {highestConfidence.symbol}
                    </span>
                    <span className="text-xs px-2 py-0.5 rounded bg-green-500/10 text-green-400 font-mono">
                      {(highestConfidence.breakout_probability * 100).toFixed(1)}% Prob
                    </span>
                   </>
                ) : (
                   <span className="text-[#666]">—</span>
                )}
              </div>
            </div>
          </div>

          {/* Filters */}
          <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end">
            <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
               <Filter size={14} /> <span className="font-mono uppercase font-semibold">Filters</span>
            </div>
            <div className="flex flex-col gap-1 w-48">
              <label className="text-[10px] text-[#888] font-mono">Sector</label>
              <select 
                value={sectorFilter} 
                onChange={e => setSectorFilter(e.target.value)}
                className="bg-[#1a1c24] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none"
              >
                <option value="All">All Sectors</option>
                {sectors.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1 w-48">
              <label className="text-[10px] text-[#888] font-mono">Market Cap</label>
              <select 
                value={bucketFilter} 
                onChange={e => setBucketFilter(e.target.value)}
                className="bg-[#1a1c24] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none"
              >
                <option value="All">All Caps</option>
                {buckets.map(b => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1 w-64">
              <div className="flex justify-between items-center text-[10px] text-[#888] font-mono">
                  <span>Min Breakout Prob</span>
                  <span>{minProb}%</span>
              </div>
              <input 
                type="range" 
                min="0" 
                max="100" 
                step="5" 
                value={minProb} 
                onChange={e => setMinProb(parseInt(e.target.value))}
                className="w-full accent-cyan-500"
              />
            </div>
          </div>

          {/* Table */}
          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden flex flex-col">
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs font-mono whitespace-nowrap">
                <thead className="bg-[#0e1117] text-[#888] sticky top-0">
                  <tr>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Symbol</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Sector</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Mkt Cap</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Trigger Date</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Age (Days)</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Current Price</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Launch Range</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Prob %</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Exp. Return</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-center">Confidence</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                        <td colSpan={10} className="px-4 py-8 text-center text-[#666]">No setups match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, i) => (
                      <tr key={i} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-4 py-3 text-[#fafafa] font-bold">
                           <button 
                             onClick={() => onNavigate('Technical Chart', row.symbol)}
                             className="hover:text-cyan-400 inline-flex items-center gap-1 transition-colors group"
                            >
                             {row.symbol} <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" />
                           </button>
                        </td>
                        <td className="px-4 py-3 text-[#ccc] truncate max-w-[120px]" title={row.sector}>{row.sector || '-'}</td>
                        <td className="px-4 py-3 text-[#ccc]">{resolveBucket(row.market_cap)}</td>
                        <td className="px-4 py-3 text-[#aaa] text-right">{row.trigger_date}</td>
                        <td className="px-4 py-3 text-[#ccc] text-right font-bold">{row.days_since_trigger}</td>
                        <td className="px-4 py-3 text-[#fafafa] text-right">₹{row.current_price?.toFixed(2)}</td>
                        <td className="px-4 py-3 text-[#ccc] text-right">
                           ₹{row.predicted_price_lower?.toFixed(1)} <span className="text-[#666]">-</span> ₹{row.predicted_price_upper?.toFixed(1)}
                        </td>
                        <td className="px-4 py-3 text-right">
                           <span className={row.breakout_probability > 0.6 ? 'text-green-400 font-bold' : row.breakout_probability > 0.4 ? 'text-yellow-400' : 'text-[#888]'}>
                               {(row.breakout_probability * 100).toFixed(1)}%
                           </span>
                        </td>
                        <td className="px-4 py-3 text-right">
                           <span className={row.expected_return > 0 ? 'text-cyan-400 font-bold' : 'text-red-400'}>
                               {(row.expected_return * 100).toFixed(2)}%
                           </span>
                        </td>
                        <td className="px-4 py-3 text-center">
                           <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold 
                              ${row.confidence === 'High' ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 
                                row.confidence === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20' : 
                                'bg-red-500/10 text-red-400 border border-red-500/20'}`}
                           >
                             {row.confidence}
                           </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
