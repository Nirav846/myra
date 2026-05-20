import { useState, useEffect, useCallback, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, Filter, AlertCircle, ArrowUpRight } from 'lucide-react';

interface RealPrediction {
  symbol: string;
  trigger_date: string;
  predicted_return_pct: number;
  predicted_days_to_breakout: number;
  current_digestion_days: number;
}

export default function LaunchpadScannerView({ lib, onNavigate }: { lib: Librarian; onNavigate: (tab: string, symbol: string) => void }) {
  const [data, setData] = useState<RealPrediction[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [minReturn, setMinReturn] = useState<number>(0);

  const fetchPredictions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/predict');
      if (res.ok) {
        const json: RealPrediction[] = await res.json();
        setData(json);
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

  const filteredData = useMemo(() => {
    if (!data) return [];
    return data
      .filter(d => (d.predicted_return_pct * 100) >= minReturn)
      .sort((a, b) => b.predicted_return_pct - a.predicted_return_pct);
  }, [data, minReturn]);

  const avgExpectedReturn = useMemo(() => {
    if (filteredData.length === 0) return 0;
    const total = filteredData.reduce((acc, curr) => acc + curr.predicted_return_pct, 0);
    return total / filteredData.length;
  }, [filteredData]);

  const highestConfidence = useMemo(() => {
    if (filteredData.length === 0) return null;
    return filteredData[0]; // Already sorted by return
  }, [filteredData]);

  const getConfidence = (ret: number) => {
    if (ret > 0.08) return 'High';
    if (ret > 0.04) return 'Medium';
    return 'Low';
  };

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
                      {(highestConfidence.predicted_return_pct * 100).toFixed(2)}% Return
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
            <div className="flex flex-col gap-1 w-64">
              <div className="flex justify-between items-center text-[10px] text-[#888] font-mono">
                  <span>Min Exp Return</span>
                  <span>{minReturn}%</span>
              </div>
              <input 
                type="range" 
                min="-10" 
                max="50" 
                step="1" 
                value={minReturn} 
                onChange={e => setMinReturn(parseInt(e.target.value))}
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
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Trigger Date</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Age (Days)</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Predicted Days to Breakout</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Exp. Return</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-center">Confidence</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                        <td colSpan={6} className="px-4 py-8 text-center text-[#666]">No setups match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, i) => {
                      const conf = getConfidence(row.predicted_return_pct);
                      return (
                      <tr key={i} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-4 py-3 text-[#fafafa] font-bold">
                           <button 
                             onClick={() => onNavigate('Technical Chart', row.symbol)}
                             className="hover:text-cyan-400 inline-flex items-center gap-1 transition-colors group"
                            >
                             {row.symbol} <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" />
                           </button>
                        </td>
                        <td className="px-4 py-3 text-[#aaa] text-right">{row.trigger_date}</td>
                        <td className="px-4 py-3 text-[#ccc] text-right font-bold">{row.current_digestion_days}</td>
                        <td className="px-4 py-3 text-[#ccc] text-right">{row.predicted_days_to_breakout?.toFixed(1) || '-'}</td>
                        <td className="px-4 py-3 text-right">
                           <span className={row.predicted_return_pct > 0 ? 'text-cyan-400 font-bold' : 'text-red-400'}>
                               {(row.predicted_return_pct * 100).toFixed(2)}%
                           </span>
                        </td>
                        <td className="px-4 py-3 text-center">
                           <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold 
                              ${conf === 'High' ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 
                                conf === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20' : 
                                'bg-red-500/10 text-red-400 border border-red-500/20'}`}
                           >
                             {conf}
                           </span>
                        </td>
                      </tr>
                      );
                    })
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
