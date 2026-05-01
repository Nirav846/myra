import { Librarian } from '../lib/Librarian';
import { useState, useEffect } from 'react';
import { Copy, Check, RefreshCw } from 'lucide-react';

export default function LeaderboardView({ lib }: { lib: Librarian }) {
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [apiData, setApiData] = useState<any[] | null>(null);

  // Concurrency Guard: Load only once on mount, no intervals.
  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setIsRefreshing(true);
    try {
      // Connect to local python repo via Librarian
      const result = await lib.executeQuery('_inst_conn', "SELECT ticker, vol_score, inst_flow FROM leaderboard LIMIT 10");
      
      if (result && result.length > 0) {
        setApiData(result);
      } else {
        // Fallback demo data if repo is disconnected
        setApiData([
          { ticker: 'NVDA', volScore: 99.1, instFlow: '+1.2B' },
          { ticker: 'AAPL', volScore: 85.4, instFlow: '+400M' },
          { ticker: 'TSLA', volScore: 42.8, instFlow: '-800M' },
        ]);
      }
      
      setDataLoaded(true);
      setLastRefreshed(new Date());
    } catch (e) {
      console.error(e);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleCopy = () => {
    const dataString = apiData 
      ? "Ticker\tVol Score\tInst Flow\n" + apiData.map(r => `${r.ticker}\t${r.volScore || r.vol_score}\t${r.instFlow || r.inst_flow}`).join('\n')
      : "";
    navigator.clipboard.writeText(dataString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa]">Institutional Leaderboard</span>
        <span className="text-[10px] font-mono text-[#666]">SQL: _inst_conn.execute()</span>
      </div>

      <div className="p-4 space-y-4 relative">
        <div className="flex justify-between items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded">
          <div className="text-[#88d] font-mono text-[11px] italic">
            {lib.isConnectedToLocalRepo 
              ? "🚀 Querying Connected Python Backend (MYRA Repo)" 
              : "st.info: Insert quantitative logic here (using _inst_conn)"}
          </div>
          <button 
            onClick={fetchData}
            disabled={isRefreshing}
            className="flex items-center gap-2 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#fafafa] transition-colors disabled:opacity-50"
            title="Concurrency Guard: Manual Refresh Only"
          >
            <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Querying..." : "Refresh"}
          </button>
        </div>

        {lastRefreshed && (
          <div className="text-[10px] text-[#666] font-mono mb-2">
            Last update: {lastRefreshed.toLocaleTimeString()}
          </div>
        )}

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors"
            title="Copy Table Data"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff1a]">
                <th className="pb-2 px-2 font-medium uppercase">Ticker</th>
                <th className="pb-2 px-2 font-medium uppercase">Vol Score</th>
                <th className="pb-2 px-2 font-medium uppercase text-right">Inst Flow</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {dataLoaded && apiData && apiData.map((row, idx) => (
                <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                  <td className="py-2 px-2 text-[#fafafa]">{row.ticker}</td>
                  <td className="py-2 px-2">{row.volScore || row.vol_score}</td>
                  <td className={`py-2 px-2 text-right ${String(row.instFlow || row.inst_flow).startsWith('-') ? 'text-red-400' : 'text-green-400'}`}>
                    {row.instFlow || row.inst_flow}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!dataLoaded && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No data loaded.</div>
          )}
        </div>
      </div>
    </div>
  );
}
