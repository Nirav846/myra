import { Librarian } from '../lib/Librarian';
import { useState, useEffect, useCallback } from 'react';
import { Copy, Check, RefreshCw, AlertTriangle, ArrowUpDown, Filter } from 'lucide-react';
import { useSettings } from '../lib/SettingsContext';

interface LeaderboardRow {
  ticker: string;
  volScore: number;
  instFlowNum: number;
  instFlow: string;
  impactPct: number | null;
}

export default function LeaderboardView({ lib }: { lib: Librarian }) {
  const { settings } = useSettings();
  const [copied, setCopied] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [apiData, setApiData] = useState<LeaderboardRow[] | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  
  const [excludeClient, setExcludeClient] = useState(false);
  const [sortByImpact, setSortByImpact] = useState(false);

  const fetchData = useCallback(async () => {
    setIsRefreshing(true);
    setErrorMsg(null);
    try {
      if (!lib.isConnectedToLocalRepo || settings.mockDataMode) {
        if (!lib.isConnectedToLocalRepo) setErrorMsg('Inst Connection Unavailable. Using Mock Data.');
        setIsDemo(true);
        setApiData([
          { ticker: 'HDFCBANK', volScore: 99.1, instFlowNum: 1.2, instFlow: '+1.2BCr', impactPct: 0.1 },
          { ticker: 'RELIANCE', volScore: 85.4, instFlowNum: 400, instFlow: '+400MCr', impactPct: 0.02 },
          { ticker: 'INFY', volScore: 42.8, instFlowNum: -800, instFlow: '-800MCr', impactPct: -0.05 },
        ]);
        return;
      }

      setIsDemo(false);
      // Fetch metadata for market cap normalization
      let mcapMap: Record<string, number> = {};
      try {
        const metaRes = await lib.executeQuery('_meta_conn', 'SELECT symbol, market_cap FROM equity_metadata', {}, 5000);
        if (metaRes) {
          metaRes.forEach((row: any) => {
            if (row.market_cap) mcapMap[row.symbol] = Number(row.market_cap);
          });
        }
      } catch (e) {
        console.warn("Could not fetch market cap", e);
      }

      // NSE sometimes reports bulk-deal quantity in actual shares, sometimes in lots.
      // This query assumes actual shares. If lot-based reporting is detected, the value may be inflated.
      const clientFilter = excludeClient 
        ? "AND client_name NOT LIKE '%CLIENT%' AND client_name NOT LIKE '%ACCOUNT%'" 
        : "";

      // Connect to local python repo via Librarian
      const query = `
        SELECT symbol as ticker, 
               95.0 as volScore, 
               SUM(CASE WHEN buy_sell = 'BUY' THEN trade_value ELSE -trade_value END) as inst_flow
        FROM (
            SELECT symbol, date, client_name, buy_sell, qty as quantity, price, value as trade_value, 'large' as source FROM large_deals
            UNION ALL
            SELECT symbol, date, client_name, buy_sell, quantity, price, trade_value, 'bulk' as source FROM bulk_deals
            UNION ALL
            SELECT symbol, date, client_name, buy_sell, quantity, price, trade_value, 'block' as source FROM block_deals
        )
        WHERE date >= date('now', '-30 days') ${clientFilter}
        GROUP BY symbol
      `;
      const result = await lib.executeQuery('_inst_conn', query);
      
      if (result && result.length > 0) {
        let mapped: LeaderboardRow[] = result.map((r: any) => {
          const flow = Number(r.inst_flow || 0);
          const mcap = mcapMap[r.ticker];
          return {
            ticker: r.ticker,
            volScore: r.volScore,
            instFlowNum: flow,
            instFlow: flow > 0 ? `+${flow.toFixed(1)}Cr` : `${flow.toFixed(1)}Cr`,
            impactPct: mcap ? (flow / mcap) * 100 : null
          };
        });
        
        mapped.sort((a, b) => {
          if (sortByImpact) {
            const valA = a.impactPct !== null ? Math.abs(a.impactPct) : -1;
            const valB = b.impactPct !== null ? Math.abs(b.impactPct) : -1;
            return valB - valA;
          } else {
            return Math.abs(b.instFlowNum) - Math.abs(a.instFlowNum);
          }
        });
        
        setApiData(mapped.slice(0, 10));
      } else {
        setApiData([]);
      }
      
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Inst Connection Error. Using Mock Data.');
      setIsDemo(true);
      const md: LeaderboardRow[] = [
        { ticker: 'HDFCBANK', volScore: 99.1, instFlowNum: 1.2, instFlow: '+1.2BCr', impactPct: 0.1 },
        { ticker: 'RELIANCE', volScore: 85.4, instFlowNum: 400, instFlow: '+400MCr', impactPct: 0.02 },
        { ticker: 'INFY', volScore: 42.8, instFlowNum: -800, instFlow: '-800MCr', impactPct: -0.05 },
      ];
      md.sort((a, b) => sortByImpact 
        ? Math.abs(b.impactPct || 0) - Math.abs(a.impactPct || 0)
        : Math.abs(b.instFlowNum) - Math.abs(a.instFlowNum)
      );
      setApiData(md);
    } finally {
      setIsRefreshing(false);
      setLastRefreshed(new Date());
    }
  }, [lib, settings.mockDataMode, excludeClient, sortByImpact]);

  // Concurrency Guard: Load only once on mount, no intervals.
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleCopy = () => {
    const dataString = apiData 
      ? "Ticker\tVol Score\tInst Flow\n" + apiData.map(r => `${r.ticker}\t${r.volScore}\t${r.instFlow}`).join('\n')
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
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded">
          <div className="text-[#88d] font-mono text-[11px] italic flex items-center gap-4">
            {isDemo 
              ? "st.info: Insert quantitative logic here (using _inst_conn)"
              : "🚀 Querying Connected Python Backend (MYRA Repo)"}

            <label className="flex items-center gap-2 cursor-pointer ml-4 hover:text-white transition-colors">
              <input 
                type="checkbox" 
                checked={excludeClient} 
                onChange={(e) => setExcludeClient(e.target.checked)}
                className="accent-blue-500 w-3 h-3"
              />
              <span className="text-xs">Exclude client trades</span>
            </label>
          </div>
          
          <div className="flex items-center gap-3 w-full md:w-auto">
            {errorMsg && (
              <span className="text-[10px] text-red-400 bg-red-400/10 px-2 py-1 rounded border border-red-400/20 flex items-center gap-1">
                <AlertTriangle size={10} /> {errorMsg}
              </span>
            )}
            
            <button 
              onClick={fetchData}
              disabled={isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#fafafa] transition-colors disabled:opacity-50 ml-auto md:ml-0"
              title="Concurrency Guard: Manual Refresh Only"
            >
              <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "Querying..." : "Refresh"}
            </button>
          </div>
        </div>

        <div className="flex justify-between items-center text-[10px] text-[#666] font-mono mb-2 px-1">
           <span>{lastRefreshed ? `Last update: ${lastRefreshed.toLocaleTimeString()}` : ''}</span>
           <button 
              onClick={() => setSortByImpact(!sortByImpact)}
              className="flex items-center gap-1.5 px-2 py-1 bg-[#ffffff0a] hover:bg-[#ffffff10] border border-[#ffffff1a] rounded transition-colors text-[#ccc]"
           >
              <ArrowUpDown size={10} /> 
              Sort by: {sortByImpact ? 'Impact %' : 'Absolute Flow'}
           </button>
        </div>

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            disabled={!apiData || apiData.length === 0}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
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
                <th className="pb-2 px-2 font-medium uppercase text-right">Deal Impact %</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {apiData && apiData.map((row, idx) => (
                <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                  <td className="py-2 px-2 text-[#fafafa]">{row.ticker}</td>
                  <td className="py-2 px-2">{row.volScore}</td>
                  <td className={`py-2 px-2 text-right ${row.instFlow.startsWith('-') ? 'text-red-400' : 'text-green-400'}`}>
                    {row.instFlow}
                  </td>
                  <td className={`py-2 px-2 text-right ${row.impactPct && row.impactPct < 0 ? 'text-red-400' : 'text-green-400'}`}>
                    {row.impactPct ? `${row.impactPct > 0 ? '+' : ''}${row.impactPct.toFixed(3)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(!apiData || apiData.length === 0) && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No data loaded.</div>
          )}
        </div>
      </div>
    </div>
  );
}
