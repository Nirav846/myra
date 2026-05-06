import { Librarian } from '../lib/Librarian';
import { useState, useEffect } from 'react';
import { RefreshCw, Target, Activity, BarChart2, ShieldAlert } from 'lucide-react';
import { computeExhaustionSignal, computeDivergenceSignal, computeSpringCoilSignal, SignalData } from '../lib/reversionSignals';

type SetupType = 'Exhaustion' | 'Divergence' | 'SpringCoil';

export default function ReversionEngineView({ lib, onNavigate }: { lib: Librarian, onNavigate?: (tab: string, symbol?: string) => void }) {
  const [activeSetup, setActiveSetup] = useState<SetupType>('Exhaustion');
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [apiData, setApiData] = useState<any[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [filterSector, setFilterSector] = useState<string>('All');
  const [filterCategory, setFilterCategory] = useState<string>('All');
  const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string; indices: string[]; in_nifty500: number }>>(new Map());
  const [availableSectors, setAvailableSectors] = useState<string[]>([]);

  useEffect(() => {
    fetchMetadata();
  }, [lib]);

  useEffect(() => {
    fetchData(activeSetup);
  }, [activeSetup, filterSector, filterCategory]);

  const fetchMetadata = async () => {
    try {
      if (!lib.isConnectedToLocalRepo) return;
      const symbolsResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, sector, in_nifty500 FROM symbols_master LIMIT 5000');
      const indexResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000');
      
      const map = new Map<string, { sector: string, indices: string[], in_nifty500: number }>();
      const sectors = new Set<string>();
      
      if (symbolsResult && Array.isArray(symbolsResult)) {
        symbolsResult.forEach((row: any) => {
          const normalizedSector = (row.sector && row.sector.trim() !== '') ? row.sector : 'Uncharted Sector';
          sectors.add(normalizedSector);
          map.set(row.symbol, { sector: normalizedSector, indices: [], in_nifty500: row.in_nifty500 });
        });
      }
      
      if (indexResult && Array.isArray(indexResult)) {
        indexResult.forEach((row: any) => {
          if (map.has(row.symbol)) {
              map.get(row.symbol)!.indices.push(row.index_name);
          } else {
              map.set(row.symbol, { sector: 'Uncharted Sector', indices: [row.index_name], in_nifty500: 0 });
          }
        });
      }
      
      if (!sectors.has('Uncharted Sector')) sectors.add('Uncharted Sector');
      
      setAvailableSectors(Array.from(sectors).sort());
      setMetadataMap(map);
    } catch(e) {
      console.warn("Failed to fetch metadata", e);
    }
  };

  const fetchData = async (setup: SetupType) => {
    setIsRefreshing(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    setApiData([]);
    
    try {
      // We need rolling time-series context (20-day avg volume, 20-day high/low).
      // To keep it fast, we first constrain to the last 30 trading dates, then compute window functions.
      const result = await lib.executeQuery('_tech_conn', `
        WITH RecentDates AS (
           SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 30
        ),
        RecentData AS (
           SELECT td.* FROM technical_data td
           INNER JOIN RecentDates rd ON td.date = rd.date
        ),
        RankedData AS (
          SELECT 
            symbol as ticker,
            date as "Date",
            open as "Open",
            high as "High",
            low as "Low",
            close as "Close",
            COALESCE(volume, trades) as "Volume",
            COALESCE(delivery, delivery_qty) as "Deliverable_Volume",
            (COALESCE(delivery, delivery_qty) * 100.0 / NULLIF(COALESCE(volume, trades), 0)) as del_perc,
            
            AVG(COALESCE(volume, trades)) OVER w20 as avg_vol_20,
            
            AVG((COALESCE(delivery, delivery_qty) * 100.0 / NULLIF(COALESCE(volume, trades), 0))) OVER w20 as avg_del_20,
            AVG((COALESCE(delivery, delivery_qty) * 100.0 / NULLIF(COALESCE(volume, trades), 0)) * (COALESCE(delivery, delivery_qty) * 100.0 / NULLIF(COALESCE(volume, trades), 0))) OVER w20 as avg_del_sq_20,

            MAX(high) OVER w20 as high_20,
            MIN(low) OVER w20 as low_20,
            
            AVG(close) OVER w20 as avg_close_20,
            AVG(close * close) OVER w20 as avg_close_sq_20,
            
            AVG((high - low) / NULLIF(close, 0)) OVER w20 as vol_long,
            AVG((high - low) / NULLIF(close, 0)) OVER w5 as vol_short,

            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
          FROM RecentData
          WINDOW 
            w20 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
            w5 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
        )
        SELECT * FROM RankedData 
        WHERE rn = 1 
        ORDER BY "Volume" DESC 
        LIMIT 2500
      `, {}, 15000);
      
      if (result && result.length > 0) {
        // Deterministic processing based on real indicators
        const mappedData = processSetups(result, setup);
        setApiData(mappedData);
      } else {
        setIsDemo(true);
        generateMockData(setup);
      }
      
      setDataLoaded(true);
      setLastRefreshed(new Date());
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Meta Connection Unavailable');
      setIsDemo(true);
      generateMockData(setup);
    } finally {
      setIsRefreshing(false);
    }
  };

  const processSetups = (rawData: any[], setup: SetupType) => {
    // Map raw data into strongly typed SignalData properties
    const parsedData: SignalData[] = rawData.map(r => ({
        ticker: r.ticker || r.symbol || '',
        close: Number(r.Close ?? r.close) || 0,
        high: Number(r.High ?? r.high) || 0,
        low: Number(r.Low ?? r.low) || 0,
        volume: Number(r.Volume ?? r.volume) || 0,
        del_perc: Number(r.del_perc ?? r.Del_perc) || 0,
        
        avg_vol_20: Number(r.avg_vol_20) || 1, // Let 0 default to 1 to avoid div-by-zero
        avg_del_20: Number(r.avg_del_20) || 0,
        avg_del_sq_20: Number(r.avg_del_sq_20) || 0,
        
        high_20: Number(r.high_20) || Number(r.High ?? r.high), 
        low_20: Number(r.low_20) || Number(r.Low ?? r.low), 
        
        avg_close_20: Number(r.avg_close_20) || Number(r.Close ?? r.close),
        avg_close_sq_20: Number(r.avg_close_sq_20) || (Number(r.Close ?? r.close) * Number(r.Close ?? r.close)),
        
        vol_long: Number(r.vol_long) || 1,
        vol_short: Number(r.vol_short) || 1,
    }));
    
    let processed = [];
    if (setup === 'Exhaustion') {
        processed = parsedData.map(computeExhaustionSignal);
    } else if (setup === 'Divergence') {
        processed = parsedData.map(computeDivergenceSignal);
    } else {
        processed = parsedData.map(computeSpringCoilSignal);
    }

    // Attach metadata and filter
    const finalData = processed.map(item => {
        const meta = metadataMap.get(item.ticker) || { sector: 'Uncharted Sector', indices: [], in_nifty500: 0 };
        
        let bucket = "Deep Frontier";
        if (meta.indices.some(i => i.includes('NIFTY 50') && !i.includes('NEXT'))) {
            bucket = "Large Cap (N50)";
        } else if (meta.indices.some(i => i.includes('NIFTY NEXT 50'))) {
            bucket = "Large Cap (N100)";
        } else if (meta.in_nifty500 === 1 || meta.indices.some(i => i.includes('NIFTY 500'))) {
            bucket = "Broader Market (N500)";
        } else if (meta.indices.length > 0) {
            bucket = "Broader Market (N500)"; // fallback for other index members
        }

        return {
            ...item,
            sector: meta.sector,
            bucket: bucket,
            isUncharted: meta.sector === 'Uncharted Sector'
        };
    }).filter(item => {
        if (filterSector !== 'All') {
            if (item.sector !== filterSector) return false;
        }
        if (filterCategory !== 'All') {
            if (item.bucket !== filterCategory) return false;
        }
        return true;
    });

    return finalData.sort((a, b) => b.score - a.score).slice(0, 75);
  };

  const generateMockData = (setup: SetupType) => {
    const symbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HUL', 'SBIN', 'BAJFINANCE', 'BHARTIARTL', 'KOTAKBANK'];
    
    const mapped = symbols.map(sym => {
        let score = 0;
        let note = '';
        let delPerc = 0;
        
        if (setup === 'Exhaustion') {
            score = 75 + Math.random() * 22;
            note = 'Panic Dump + Silent Accum.';
            delPerc = 60 + Math.random() * 20;
        } else if (setup === 'Divergence') {
            score = 80 + Math.random() * 18;
            note = 'Price Support + Spike in %Del';
            delPerc = 85 + Math.random() * 10;
        } else {
             score = 70 + Math.random() * 26;
             note = 'Spring Coil (VCP)';
             delPerc = 30 + Math.random() * 20;
        }
        
        const close = 100 + (Math.random() * 2000);
        const candleRange = close * (Math.random() * 0.05 + 0.02);
        const high = close + (candleRange * Math.random());
        const low = close - (candleRange * Math.random());
        
        const entry = high + (high * 0.001);
        const sl = low - (low * 0.002);
        const risk = ((entry - sl) / entry) * 100;
        
        let bucket = "Large Cap (N50)";
        if (sym === 'BAJFINANCE' || sym === 'KOTAKBANK') bucket = "Large Cap (N100)";
        if (sym === 'HUL') bucket = "Broader Market (N500)";

        return {
           ticker: sym,
           close: close,
           entry: entry,
           sl: sl,
           risk: risk,
           score,
           delPerc,
           signal: score > 90 ? 'STRONGCALL' : score > 80 ? 'CALL' : 'WATCH',
           note,
           sector: sym === 'RELIANCE' ? 'Energy' : sym === 'TCS' || sym === 'INFY' ? 'IT' : 'Financials',
           bucket: bucket,
           isUncharted: false
        };
    }).filter(item => {
        if (filterSector !== 'All') {
            if (item.sector !== filterSector) return false;
        }
        if (filterCategory !== 'All') {
            if (item.bucket !== filterCategory) return false;
        }
        return true;
    }).sort((a, b) => b.score - a.score);
    
    setApiData(mapped);
    setDataLoaded(true);
    setLastRefreshed(new Date());
  };

  return (
    <div className="bg-[#0b0c10] rounded-xl border border-white/10 shadow-2xl flex flex-col overflow-hidden max-w-5xl mx-auto">
      <div className="p-4 border-b border-white/5 bg-gradient-to-r from-cyan-900/20 to-transparent flex justify-between items-center">
        <div className="flex items-center gap-3">
            <div className="p-1.5 bg-cyan-500/10 rounded-md border border-cyan-500/20">
              <Target size={18} className="text-cyan-400" />
            </div>
            <span className="text-sm font-bold uppercase tracking-wider text-[#fafafa]">
              Reversion Engine
            </span>
        </div>
        <span className="text-[10px] font-mono text-cyan-500/70 border border-cyan-500/20 px-2 py-0.5 rounded-full bg-cyan-500/5">v1.2 Quantum</span>
      </div>

      <div className="p-5 space-y-5 relative">
        <div className="flex flex-col md:flex-row gap-4 items-start md:items-center justify-between bg-[#12141a] border border-white/5 p-3 rounded-lg shadow-sm">
           <div className="flex bg-[#0A0A0A] rounded-md border border-[#333] p-1 gap-1 shadow-inner h-[34px]">
              <button 
                onClick={() => setActiveSetup('Exhaustion')}
                className={`px-3 py-1 rounded text-xs font-mono transition-all duration-200 flex items-center gap-2 ${activeSetup === 'Exhaustion' ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 shadow-[0_0_10px_rgba(34,211,238,0.1)]' : 'text-[#888] hover:text-[#ddd] border border-transparent hover:bg-white/5'}`}
              >
                  <Activity size={12} /> Exhaustion
              </button>
              <button 
                onClick={() => setActiveSetup('Divergence')}
                className={`px-3 py-1 rounded text-xs font-mono transition-all duration-200 flex items-center gap-2 ${activeSetup === 'Divergence' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30 shadow-[0_0_10px_rgba(168,85,247,0.1)]' : 'text-[#888] hover:text-[#ddd] border border-transparent hover:bg-white/5'}`}
              >
                  <ShieldAlert size={12} /> Divergence
              </button>
              <button 
                onClick={() => setActiveSetup('SpringCoil')}
                className={`px-3 py-1 rounded text-xs font-mono transition-all duration-200 flex items-center gap-2 ${activeSetup === 'SpringCoil' ? 'bg-green-500/20 text-green-400 border border-green-500/30 shadow-[0_0_10px_rgba(34,197,94,0.1)]' : 'text-[#888] hover:text-[#ddd] border border-transparent hover:bg-white/5'}`}
              >
                  <BarChart2 size={12} /> Spring Coil
              </button>
           </div>
           
             <div className="flex items-center gap-3 w-full md:w-auto h-full">
               <div className="flex bg-[#0A0A0A] rounded border border-[#333333] shadow-inner items-center h-[34px] overflow-hidden">
                   <select 
                     value={filterCategory} 
                     onChange={(e) => setFilterCategory(e.target.value)}
                     className="bg-transparent border-r border-[#333333] hover:bg-[#1a1c24] transition-colors outline-none text-[#fafafa] text-xs font-mono h-full px-2 cursor-pointer"
                   >
                     <option value="All" className="bg-[#1a1c24] text-white">All Categories</option>
                     <option value="Large Cap (N50)" className="bg-[#1a1c24] text-white">Large Cap (N50)</option>
                     <option value="Large Cap (N100)" className="bg-[#1a1c24] text-white">Large Cap (N100)</option>
                     <option value="Broader Market (N500)" className="bg-[#1a1c24] text-white">Broader Market (N500)</option>
                     <option value="Deep Frontier" className="bg-[#1a1c24] text-white">Deep Frontier</option>
                   </select>
                   <select 
                     value={filterSector} 
                     onChange={(e) => setFilterSector(e.target.value)}
                     className="bg-transparent hover:bg-[#1a1c24] transition-colors outline-none text-[#fafafa] text-xs font-mono h-full px-2 max-w-[140px] cursor-pointer"
                   >
                     <option value="All" className="bg-[#1a1c24] text-white">All Sectors</option>
                     {availableSectors.map(s => <option key={s} value={s} className="bg-[#1a1c24] text-white">{s}</option>)}
                     {isDemo && availableSectors.length === 0 && (
                         <>
                             <option value="IT" className="bg-[#1a1c24] text-white">IT</option>
                             <option value="Financials" className="bg-[#1a1c24] text-white">Financials</option>
                             <option value="Energy" className="bg-[#1a1c24] text-white">Energy</option>
                         </>
                     )}
                   </select>
               </div>
               
               {errorMsg && <span className="text-xs text-red-500 font-mono px-2 py-1 bg-red-500/10 border border-red-500/20 rounded hidden lg:block">{errorMsg}</span>}
               {isDemo && <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded border border-yellow-500/20 font-mono hidden lg:block">⚠️ DEMO</span>}
              <button 
                onClick={() => fetchData(activeSetup)}
                disabled={isRefreshing}
                className="flex justify-center items-center gap-2 px-4 h-[34px] bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/30 rounded text-xs text-indigo-300 font-bold transition-all disabled:opacity-50 font-mono shadow-[0_0_15px_rgba(99,102,241,0.1)] ml-auto md:ml-0"
              >
                <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
                {isRefreshing ? "Scanning..." : "Run"}
              </button>
            </div>
        </div>

        <div className="bg-[#12141a]/50 border border-white/5 rounded-lg p-4 text-xs font-mono text-[#888] leading-relaxed shadow-sm">
            {activeSetup === 'Exhaustion' && (
              <p><span className="text-cyan-400 font-bold mr-2">IDEA 1: EXHAUSTION</span> Looks for isolated instances where price drops intensely, volume explodes, but delivery percentage implies institutional accumulation rather than pure panic offloading.</p>
            )}
            {activeSetup === 'Divergence' && (
              <p><span className="text-purple-400 font-bold mr-2">IDEA 2: DIVERGENCE</span> Scans for symbols testing multi-month lows with average volume, but experiencing extreme anomalies in Delivery % (e.g. {'>'} 85%), indicating quiet smart-money absorption.</p>
            )}
            {activeSetup === 'SpringCoil' && (
              <p><span className="text-green-400 font-bold mr-2">IDEA 3: SPRING COIL</span> Identifies assets going dormant. Both price range and volume dry up dramatically, while delivery percentage remains stable, preceding explosive expansion.</p>
            )}
        </div>

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'} min-h-[250px] bg-[#12141a] rounded-lg border border-white/5`}>
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-[#666] border-b border-white/5 bg-black/20">
                <th className="py-3 px-3 font-semibold uppercase min-w-[100px] tracking-wider rounded-tl-lg">Ticker</th>
                <th className="py-3 px-3 font-semibold uppercase tracking-wider">Sector</th>
                <th className="py-3 px-3 font-semibold uppercase text-right tracking-wider">Close (₹)</th>
                <th className="py-3 px-3 font-semibold uppercase text-right tracking-wider">Score</th>
                <th className="py-3 px-3 font-semibold uppercase text-right tracking-wider">Del %</th>
                <th className="py-3 px-3 font-semibold uppercase w-[25%] hidden md:table-cell tracking-wider">Action Plan</th>
                <th className="py-3 px-3 font-semibold uppercase w-[20%] hidden lg:table-cell tracking-wider rounded-tr-lg">Engine Note</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {dataLoaded && apiData.map((row, idx) => (
                <tr key={idx} className="border-b border-white/5 hover:bg-white/5 transition-all duration-200">
                  <td className="py-3 px-3">
                    <div className="flex items-center gap-1.5">
                      <button 
                        onClick={() => onNavigate?.('Technical Chart', row.ticker)}
                        className="text-[#fafafa] font-bold hover:text-indigo-400 transition-colors cursor-pointer text-sm"
                      >
                        {row.ticker}
                      </button>
                      {row.isUncharted && <span className="text-[8px] bg-red-500/10 border border-red-500/20 text-red-400 font-bold px-1.5 py-0.5 rounded tracking-wide">UNCHARTED</span>}
                    </div>
                    <div className="text-[9px] text-[#666] tracking-widest uppercase truncate mt-0.5">{row.bucket}</div>
                  </td>
                  <td className="py-3 px-3 text-[#888] truncate max-w-[120px]">{row.sector}</td>
                  <td className="py-3 px-3 text-right text-[#fafafa] font-medium">{row.close.toFixed(2)}</td>
                  <td className="py-3 px-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <div className="w-16 h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden hidden sm:block shadow-inner">
                         <div className="h-full rounded-full transition-all duration-500" style={{ width: `${row.score}%`, backgroundColor: row.score > 90 ? '#22c55e' : row.score > 80 ? '#3b82f6' : '#8b5cf6', boxShadow: `0 0 8px ${row.score > 90 ? '#22c55e' : row.score > 80 ? '#3b82f6' : '#8b5cf6'}` }}></div>
                      </div>
                      <span className={`font-bold ${row.score > 90 ? 'text-green-400' : row.score > 80 ? 'text-blue-400' : 'text-purple-400'}`}>{row.score.toFixed(1)}</span>
                    </div>
                  </td>
                  <td className="py-3 px-3 text-right text-gray-400 font-medium">{row.delPerc.toFixed(1)}%</td>
                  <td className="py-3 px-3 hidden md:table-cell">
                    <div className="flex flex-col gap-1 text-[10px] font-mono">
                      <div className="text-[9px] text-[#888] uppercase tracking-wider mb-0.5">T+1 Buy Stop</div>
                      <div className="flex items-center flex-wrap gap-x-3 gap-y-1 bg-black/40 p-1.5 rounded-md border border-white/5 shadow-inner">
                        <div className="flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-green-500"></div><span className="text-gray-300">En <span className="text-green-400 font-bold text-xs ml-0.5">₹{row.entry.toFixed(2)}</span></span></div>
                        <span className="text-[#333]">|</span>
                        <div className="flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-red-500"></div><span className="text-gray-300">SL <span className="text-red-400 font-bold text-xs ml-0.5">₹{row.sl.toFixed(2)}</span></span></div>
                        <span className="text-[#333]">|</span>
                        <span className="text-orange-400 font-bold">R {row.risk.toFixed(1)}%</span>
                      </div>
                    </div>
                  </td>
                  <td className="py-3 px-3 text-[#777] italic hidden lg:table-cell truncate max-w-[150px]">{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!dataLoaded && !isRefreshing && (
            <div className="w-full py-16 text-center text-[#555] text-xs font-mono flex flex-col items-center justify-center gap-2">
                <Target size={24} className="opacity-20" />
                <span>Run engine to scan opportunities.</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
