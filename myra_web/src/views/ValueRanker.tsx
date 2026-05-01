import { Librarian } from '../lib/Librarian';
import { useState, useEffect } from 'react';
import { Copy, Check, RefreshCw, Target } from 'lucide-react';

export default function ValueRankerView({ lib }: { lib: Librarian }) {
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [apiData, setApiData] = useState<any[] | null>(null);
  
  // Dynamic parameters
  const [minMos, setMinMos] = useState<number>(20);
  const [limit, setLimit] = useState<number>(15);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    fetchData();
  }, [minMos, limit]);

  const fetchData = async () => {
    setIsRefreshing(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    try {
      const result = await lib.executeQuery('_meta_conn', `SELECT 
        f.symbol as ticker, 
        f.sector, 
        f.eps,
        f.book_value,
        f.pe
        FROM fundamentals f
        LIMIT 1000
      `, {}, 10000);
      
      if (result && result.length > 0) {
        // Map potential raw column names to expected UI properties
        const mappedData = result.map((r: any) => {
          // Calculate graham value locally since SQRT in SQLite can be unavailable
          const eps = Number(r.eps || 0);
          const bv = Number(r.book_value || 0);
          const graham = eps > 0 && bv > 0 ? Math.sqrt(22.5 * eps * bv) : 0;
          
          // Approximate current price from PE and EPS if actual price isn't available
          const priceApprox = (r.pe && r.eps) ? (Number(r.pe) * Number(r.eps)) : 0;
          
          const mos = graham > 0 ? ((graham - priceApprox) / graham) * 100 : 0;
          return {
            ticker: r.ticker || 'UNKNOWN',
            sector: r.sector || 'General',
            price: priceApprox,
            graham: graham,
            mos: mos,
            rating: mos > 30 ? 'Strong Buy' : mos > 15 ? 'Buy' : 'Hold'
          };
        }).filter((x: any) => x.mos >= minMos).sort((a: any, b: any) => b.mos - a.mos).slice(0, limit);
        setApiData(mappedData);
      } else {
        setIsDemo(true);
        generateMockData();
      }
      
      setDataLoaded(true);
      setLastRefreshed(new Date());
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Meta Connection Unavailable');
      setIsDemo(true);
      generateMockData();
    } finally {
      setIsRefreshing(false);
    }
  };

  const generateMockData = () => {
    const mockDb = [
      { ticker: 'ITC', sector: 'FMCG', price: 412.50, graham: 540.20, mos: 30.9, rating: 'Strong Buy' },
      { ticker: 'ONGC', sector: 'Energy', price: 265.10, graham: 330.00, mos: 24.5, rating: 'Buy' },
      { ticker: 'COALINDIA', sector: 'Energy', price: 280.00, graham: 345.50, mos: 23.3, rating: 'Buy' },
      { ticker: 'NMDC', sector: 'Mining', price: 210.40, graham: 255.80, mos: 21.6, rating: 'Buy' },
      { ticker: 'INFY', sector: 'IT Services', price: 1420, graham: 1850, mos: 23.2, rating: 'Buy' },
      { ticker: 'SBI', sector: 'Financials', price: 750, graham: 980, mos: 23.4, rating: 'Buy' }
    ];
    
    // Scale properties against inputs
    const mapped = mockDb.map(d => ({
       ...d,
       mos: d.mos + (Math.random() * 5), 
       rating: d.mos > 30 ? 'Strong Buy' : 'Buy'
    }));
    
    // Filter by the user's slider bounds
    setApiData(mapped.filter(x => x.mos >= minMos).slice(0, limit));
    setDataLoaded(true);
    setLastRefreshed(new Date());
  };

  const handleCopy = () => {
    const dataString = apiData 
      ? "Ticker\tSector\tPrice\tGraham Value\tMoS (%)\n" + apiData.map(r => `${r.ticker}\t${r.sector}\t${r.price}\t${r.graham}\t${r.mos}%`).join('\n')
      : "";
    navigator.clipboard.writeText(dataString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa] flex items-center gap-2">
          <Target size={14} className="text-green-400" />
          Fundamental Value Ranker (Graham Model)
        </span>
        <span className="text-[10px] font-mono text-[#666]">SQL: _meta_conn.execute()</span>
      </div>

      <div className="p-4 space-y-4 relative">
        <div className="flex justify-between items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded flex-wrap">
          <div className="flex gap-4 items-center">
             <div className="flex flex-col">
               <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Min MoS (%)</label>
               <input type="number" value={minMos} onChange={e => setMinMos(Number(e.target.value))} className="w-20 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] font-mono focus:border-cyan-500 outline-none" />
             </div>
             <div className="flex flex-col">
               <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Max Results</label>
               <select value={limit} onChange={e => setLimit(Number(e.target.value))} className="w-20 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] font-mono focus:border-cyan-500 outline-none">
                 <option value={5}>5</option>
                 <option value={10}>10</option>
                 <option value={15}>15</option>
                 <option value={50}>50</option>
               </select>
             </div>
          </div>

          <div className="flex items-center gap-3">
             {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
             {isDemo && <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono">⚠️ DEMO DATA</span>}
            <button 
              onClick={fetchData}
              disabled={isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#fafafa] transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "Calculating..." : "Screen Value"}
            </button>
          </div>
        </div>

        {lastRefreshed && (
          <div className="text-[10px] text-[#666] font-mono mb-2">
            Last screened: {lastRefreshed.toLocaleTimeString()}
          </div>
        )}

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors"
            title="Copy Screen Results"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff1a]">
                <th className="pb-2 px-2 font-medium uppercase">Ticker</th>
                <th className="pb-2 px-2 font-medium uppercase">Sector</th>
                <th className="pb-2 px-2 font-medium uppercase text-right">CMP (₹)</th>
                <th className="pb-2 px-2 font-medium uppercase text-right">Graham Value</th>
                <th className="pb-2 px-2 font-medium uppercase text-right">MoS (%)</th>
                <th className="pb-2 px-2 font-medium uppercase text-right">Rating</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {dataLoaded && apiData && apiData.map((row, idx) => (
                <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                  <td className="py-2 px-2 text-[#fafafa] font-bold">{row.ticker}</td>
                  <td className="py-2 px-2 text-[#888]">{row.sector}</td>
                  <td className="py-2 px-2 text-right">{row.price.toFixed(2)}</td>
                  <td className="py-2 px-2 text-right text-blue-400">{row.graham.toFixed(2)}</td>
                  <td className="py-2 px-2 text-right text-green-400">+{row.mos}%</td>
                  <td className="py-2 px-2 text-right">
                    <span className="bg-green-500/10 text-green-400 px-1.5 py-0.5 rounded text-[10px]">{row.rating}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!dataLoaded && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No fundamental data loaded.</div>
          )}
        </div>
      </div>
    </div>
  );
}
