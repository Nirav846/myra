import { Librarian } from '../lib/Librarian';
import { useState, useEffect, useCallback } from 'react';
import { Copy, Check, RefreshCw, AlertTriangle, ChartBar, Settings2 } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, ReferenceLine } from 'recharts';
import { alertBus } from '../lib/AlertManager';
import { useSettings } from '../lib/SettingsContext';

interface FVGRow {
  ticker: string;
  fvg_type: 'Bullish' | 'Bearish';
  freshness_days: number;
  price_change_5d: number;
  signal: 'Strong Buy' | 'Strong Sell' | 'Caution (Exhaustion)';
  fvg_top: number;
  fvg_bottom: number;
}

export default function FVGScannerView({ lib }: { lib: Librarian }) {
  const { settings } = useSettings();
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [exhaustionThreshold, setExhaustionThreshold] = useState(5.0);
  const [apiData, setApiData] = useState<FVGRow[]>([]);

  const fetchData = useCallback(async () => {
    setIsRefreshing(true);
    setErrorMsg(null);
    try {
      if (!lib.isConnectedToLocalRepo || settings.mockDataMode) {
        setIsDemo(true);
        setApiData([
          { ticker: 'NQ', fvg_type: 'Bullish', freshness_days: 2, price_change_5d: 2.1, signal: 'Strong Buy', fvg_top: 18520, fvg_bottom: 18450 },
          { ticker: 'ES', fvg_type: 'Bearish', freshness_days: 4, price_change_5d: -6.0, signal: 'Caution (Exhaustion)', fvg_top: 5240, fvg_bottom: 5210 },
          { ticker: 'BTC', fvg_type: 'Bullish', freshness_days: 14, price_change_5d: 8.5, signal: 'Caution (Exhaustion)', fvg_top: 69500, fvg_bottom: 68500 }
        ]);
        setDataLoaded(true);
        setLastRefreshed(new Date());
        setIsRefreshing(false);
        return;
      }

      setIsDemo(false);

      // Replaces COALESCE(volume, trades) with volume
      // Computes freshness using calendar days instead of bar count
      // Applies Exhaustion check using 5 day price change
      const query = `
        WITH LatestData AS (
          SELECT symbol, date, close, volume, delivery, bullish_fvg, bearish_fvg, fvg_top, fvg_bottom,
                 LAG(close, 5) OVER (PARTITION BY symbol ORDER BY date) as close_5d_ago,
                 date as fvg_date 
          FROM technical_data
        )
        SELECT symbol as ticker, 
               CASE WHEN bullish_fvg = 1 THEN 'Bullish' WHEN bearish_fvg = 1 THEN 'Bearish' END as fvg_type,
               CAST(julianday('now') - julianday(fvg_date) AS INTEGER) as freshness_days,
               ((close - close_5d_ago) / close_5d_ago) * 100.0 as price_change_5d,
               fvg_top, fvg_bottom
        FROM LatestData
        WHERE (bullish_fvg = 1 OR bearish_fvg = 1)
        AND date = (SELECT MAX(date) FROM technical_data)
        AND volume > 500000 
      `;
      const result = await lib.executeQuery('_tech_conn', query);
      
      if (result && result.length > 0) {
        const rows: FVGRow[] = result.map((r: any) => {
          let signal: 'Strong Buy' | 'Strong Sell' | 'Caution (Exhaustion)';
          const change = Number(r.price_change_5d || 0);
          if (r.fvg_type === 'Bullish') {
            signal = change > exhaustionThreshold ? 'Caution (Exhaustion)' : 'Strong Buy';
          } else {
            signal = change < -exhaustionThreshold ? 'Caution (Exhaustion)' : 'Strong Sell';
          }
          return {
            ticker: r.ticker,
            fvg_type: r.fvg_type,
            freshness_days: Number(r.freshness_days || 0),
            price_change_5d: change,
            signal,
            fvg_top: Number(r.fvg_top || 0),
            fvg_bottom: Number(r.fvg_bottom || 0)
          };
        });
        setApiData(rows);
      } else {
        setApiData([]);
      }
      
      setDataLoaded(true);
      setLastRefreshed(new Date());
      setIsRefreshing(false);
      
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || "Query failed.");
      setIsDemo(true);
      setApiData([]);
      setDataLoaded(true);
      setLastRefreshed(new Date());
      setIsRefreshing(false);
    }
  }, [lib, settings.mockDataMode, exhaustionThreshold]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleCopy = () => {
    const header = "Ticker\tFVG_Type\tFreshness_Days\tPrice_Change_5d\tSignal";
    const rows = apiData.map(r => `${r.ticker}\t${r.fvg_type}\t${r.freshness_days}\t${r.price_change_5d.toFixed(2)}%\t${r.signal}`);
    navigator.clipboard.writeText([header, ...rows].join('\n'));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa]">FVG Scanner (Technical)</span>
        <div className="flex gap-2 items-center">
          {isDemo && (
             <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
               ⚠️ SIMULATED DATA
             </span>
          )}
          <span className="text-[10px] font-mono text-[#666]">SQL: _tech_conn.execute()</span>
        </div>
      </div>

      <div className="p-4 space-y-4 relative">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
               <Settings2 size={14} className="text-[#888]" />
               <span className="text-[11px] text-[#888] font-mono">Exhaustion Threshold:</span>
               <input 
                 type="number"
                 value={exhaustionThreshold}
                 onChange={(e) => setExhaustionThreshold(Number(e.target.value))}
                 className="w-16 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:outline-none"
               />
               <span className="text-[11px] text-[#888] font-mono">%</span>
            </div>
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
            >
              <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "Querying..." : "Refresh"}
            </button>
          </div>
        </div>

        {lastRefreshed && (
          <div className="text-[10px] text-[#666] font-mono mb-2">
            Last update: {lastRefreshed.toLocaleTimeString()}
          </div>
        )}

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            disabled={!dataLoaded}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors z-10 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <table className="w-full text-left font-mono text-xs cursor-default">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff1a]">
                <th className="pb-2 px-2 font-medium uppercase">Asset</th>
                <th className="pb-2 px-2 font-medium uppercase">FVG Type</th>
                <th className="pb-2 px-2 font-medium uppercase">Freshness</th>
                <th className="pb-2 px-2 font-medium uppercase">5D Change</th>
                <th className="pb-2 px-2 font-medium uppercase">Signal</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {dataLoaded && apiData.map((row, idx) => (
                  <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff10] transition-colors">
                    <td className="py-2 px-2 text-[#fafafa] font-bold">{row.ticker}</td>
                    <td className={`py-2 px-2 ${row.fvg_type === 'Bullish' ? 'text-green-400' : 'text-red-400'}`}>
                      {row.fvg_type}
                    </td>
                    <td className="py-2 px-2">{row.freshness_days} days</td>
                    <td className="py-2 px-2">{row.price_change_5d > 0 ? '+' : ''}{row.price_change_5d.toFixed(2)}%</td>
                    <td className="py-2 px-2 flex items-center gap-1">
                      {row.signal.includes('Exhaustion') ? <AlertTriangle size={12} className="text-yellow-500" /> : <ChartBar size={12} className="text-blue-400" />}
                      <span className={`${row.signal.includes('Exhaustion') ? 'text-yellow-500' : row.signal === 'Strong Buy' ? 'text-green-400' : 'text-red-400'}`}>
                        {row.signal}
                      </span>
                    </td>
                  </tr>
              ))}
            </tbody>
          </table>
          {(!dataLoaded || apiData.length === 0) && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No Active FVGs Found.</div>
          )}
        </div>
      </div>
    </div>
  );
}
