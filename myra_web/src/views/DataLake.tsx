import { Librarian } from '../lib/Librarian';
import { useState, useEffect } from 'react';
import { Copy, Check, RefreshCw, Database, Search } from 'lucide-react';
import { SymbolSearch } from '../components/SymbolSearch';

export default function DataLakeView({ lib }: { lib: Librarian }) {
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [searchQuery, setSearchQuery] = useState('RELIANCE');
  const [category, setCategory] = useState('Smart Money Concepts');
  const [apiData, setApiData] = useState<any[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, [searchQuery, category]);

  const fetchData = async () => {
    if (!searchQuery) return;
    setIsRefreshing(true);
    setErrorMsg(null);
    try {
      const query = `
        SELECT date, bullish_fvg, bearish_fvg, delivery_divergence_score, volatility_compression_score, relative_volume_score, nifty_outperformance_score, htf_bullish, mtf_bullish, trend_alignment
        FROM technical_data
        WHERE symbol = '${searchQuery.trim().toUpperCase()}'
        ORDER BY date DESC LIMIT 30
      `;
      const result = await lib.executeQuery('_tech_conn', query, {}, 5000);
      if (result && result.length > 0) {
        setApiData(result);
        setDataLoaded(true);
      } else {
        setApiData([]);
        setDataLoaded(false);
        setErrorMsg('No data found for this symbol.');
      }
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Database unavailable - generating mock data');
      setApiData(generateMockData());
      setDataLoaded(true);
    } finally {
      setLastRefreshed(new Date());
      setIsRefreshing(false);
    }
  };

  const generateMockData = () => {
    const dates = Array.from({length: 10}).map((_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - i);
      return d.toISOString().split('T')[0];
    });
    return dates.map(date => ({
      date,
      bullish_fvg: Math.random() > 0.8 ? 1 : 0,
      bearish_fvg: Math.random() > 0.8 ? 1 : 0,
      delivery_divergence_score: (Math.random() * 2 - 1).toFixed(2),
      volatility_compression_score: (Math.random() * 100).toFixed(1),
      trend_alignment: Math.random() > 0.5 ? 'Bullish' : 'Bearish'
    }));
  };

  const handleCopy = () => {
    if (!apiData || apiData.length === 0) return;
    const headers = Object.keys(apiData[0]).join('\t');
    const rows = apiData.map(row => Object.values(row).join('\t')).join('\n');
    navigator.clipboard.writeText(`${headers}\n${rows}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa] flex items-center gap-2">
          <Database size={14} className="text-blue-400" />
          Data Lake Explorer: Indicators & SMC
        </span>
        <span className="text-[10px] font-mono text-[#666]">SQL: technical_data</span>
      </div>

      <div className="p-4 space-y-4">
        <div className="flex flex-wrap items-center gap-3 bg-[#ffffff05] p-3 rounded border border-[#ffffff0a]">
          <div className="flex-1 min-w-[200px] relative">
            <SymbolSearch 
              lib={lib}
              initialValue={searchQuery}
              onSymbolSelect={setSearchQuery}
              placeholder="Search symbol (e.g. RELIANCE)..." 
            />
          </div>
          <select value={category} onChange={e => setCategory(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors">
            <option value="Smart Money Concepts">Smart Money Concepts</option>
            <option value="Enrichment Metrics">Enrichment Metrics</option>
          </select>
        </div>

        <div className="flex justify-between items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded">
          <div className="flex items-center gap-3">
             <div className="text-[#888] font-mono text-[11px] italic">
               {lib.isConnectedToLocalRepo 
                 ? "🚀 Connected to Local Technical Database" 
                 : "⚠️ Offline Mode - Local DB recommended"}
             </div>
             {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
          </div>
          <button 
            onClick={fetchData}
            disabled={isRefreshing}
            className="flex items-center gap-2 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#fafafa] transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Scanning..." : "Query Lake"}
          </button>
        </div>

        {lastRefreshed && (
          <div className="text-[10px] text-[#666] font-mono mb-2">
            Lake snapshot sync: {lastRefreshed.toLocaleTimeString()}
          </div>
        )}

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            disabled={apiData.length === 0}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors disabled:opacity-50"
            title="Copy Lake Data"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff1a]">
                <th className="pb-2 px-2 font-medium uppercase min-w-[80px]">Date</th>
                {category === 'Smart Money Concepts' ? (
                  <>
                    <th className="pb-2 px-2 font-medium uppercase">Bullish FVG</th>
                    <th className="pb-2 px-2 font-medium uppercase">Bearish FVG</th>
                    <th className="pb-2 px-2 font-medium uppercase">HTF Trend</th>
                    <th className="pb-2 px-2 font-medium uppercase">MTF Trend</th>
                    <th className="pb-2 px-2 font-medium uppercase">Alignment</th>
                  </>
                ) : (
                  <>
                    <th className="pb-2 px-2 font-medium uppercase">Del Div Score</th>
                    <th className="pb-2 px-2 font-medium uppercase">Vol Compress Score</th>
                    <th className="pb-2 px-2 font-medium uppercase">Rel Vol Score</th>
                    <th className="pb-2 px-2 font-medium uppercase">Nifty Outperf</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
               {apiData.map((row, idx) => (
                 <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                   <td className="py-2 px-2 text-[#888]">{row.date}</td>
                   {category === 'Smart Money Concepts' ? (
                     <>
                       <td className="py-2 px-2 text-green-400">{row.bullish_fvg ? 'Detected' : '-'}</td>
                       <td className="py-2 px-2 text-red-400">{row.bearish_fvg ? 'Detected' : '-'}</td>
                       <td className="py-2 px-2 text-[#fafafa]">{row.htf_bullish === 1 ? 'Bullish' : row.htf_bullish === 0 ? 'Bearish' : '-'}</td>
                       <td className="py-2 px-2 text-[#fafafa]">{row.mtf_bullish === 1 ? 'Bullish' : row.mtf_bullish === 0 ? 'Bearish' : '-'}</td>
                       <td className={`py-2 px-2 font-medium ${row.trend_alignment === 'Bullish' ? 'text-green-400' : row.trend_alignment === 'Bearish' ? 'text-red-400' : 'text-[#888]'}`}>{row.trend_alignment || '-'}</td>
                     </>
                   ) : (
                     <>
                       <td className={`py-2 px-2 ${Number(row.delivery_divergence_score) > 0 ? 'text-green-400' : 'text-red-400'}`}>{row.delivery_divergence_score || '-'}</td>
                       <td className="py-2 px-2 text-[#fafafa]">{row.volatility_compression_score || '-'}</td>
                       <td className="py-2 px-2 text-[#fafafa]">{row.relative_volume_score || '-'}</td>
                       <td className={`py-2 px-2 ${Number(row.nifty_outperformance_score) > 0 ? 'text-green-400' : 'text-red-400'}`}>{row.nifty_outperformance_score || '-'}</td>
                     </>
                   )}
                 </tr>
               ))}
            </tbody>
          </table>
          {!dataLoaded && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No data loaded or empty result sequence.</div>
          )}
        </div>
      </div>
    </div>
  );
}
