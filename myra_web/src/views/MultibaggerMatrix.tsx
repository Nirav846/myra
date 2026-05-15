import { useState, useEffect, useMemo, useCallback } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, ShieldAlert, Zap, ArrowUpDown, AlertTriangle, ChevronUp, ChevronDown } from 'lucide-react';
import { ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, CartesianGrid, Cell, ReferenceLine } from 'recharts';
import { useSettings } from '../lib/SettingsContext';
import { resolveBucket } from '../lib/bucketUtils';
import PresetChip from '../components/PresetChip';
import { MultibaggerConfig } from '../lib/scannerPresets';

export const calcMultibaggerIndex = (roe: number, earningsYieldPct: number, accumulation: number) => {
    const roeScore = Math.max(0, Math.min(100, (roe / 50) * 100));
    const eyScore = Math.max(0, Math.min(100, (earningsYieldPct / 15) * 100));
    const accScore = Math.max(0, Math.min(100, accumulation));
    return Math.round((roeScore * 0.4) + (eyScore * 0.3) + (accScore * 0.3));
};

export const calcMoatScore = (roe: number) => {
    return roe > 25 ? 'Monopoly' : roe > 15 ? 'High' : roe > 8 ? 'Medium' : 'Low';
};

interface MultibaggerData {
  ticker: string;
  sector: string;
  returnOnEquity: number;
  earningsPerShare: number;
  peRatio: number;
  accumulation: number;
  earningsYield: number;
  multibagger_index: number;
  moat_score: string;
  bucket: string;
}

export default function MultibaggerMatrixView({ lib }: { lib: Librarian }) {
  const { settings } = useSettings();
  const [data, setData] = useState<MultibaggerData[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string, bucket: string }>>(new Map());
  const [metadataLoaded, setMetadataLoaded] = useState(false);

  // Dynamic States for Toggles
  const [excludeCyclical, setExcludeCyclical] = useState(true);
  const [requireAccumulation, setRequireAccumulation] = useState(true);
  const [minRoe, setMinRoe] = useState(15);
  const [minEps, setMinEps] = useState(10);
  const [days, setDays] = useState(180);
  const [filterMcap, setFilterMcap] = useState<string>('Broader Market (N500)');

  const [minRoeInput, setMinRoeInput] = useState(minRoe);
  const [minEpsInput, setMinEpsInput] = useState(minEps);
  const [daysInput, setDaysInput] = useState(days);

  // Sorting State
  const [sortConfig, setSortConfig] = useState<{ key: keyof MultibaggerData, direction: 'asc' | 'desc' } | null>({ key: 'multibagger_index', direction: 'desc' });

  useEffect(() => {
    const t = setTimeout(() => {
      setMinRoe(minRoeInput);
      setMinEps(minEpsInput);
      setDays(daysInput);
    }, 500);
    return () => clearTimeout(t);
  }, [minRoeInput, minEpsInput, daysInput]);

  useEffect(() => {
    let active = true;
    const fetchMeta = async () => {
      try {
        if (!lib.isConnectedToLocalRepo || settings.mockDataMode) {
          if (active) setMetadataLoaded(true);
          return;
        }
        const symbolsResult = await lib.executeQuery('_meta_conn', 'SELECT symbol as ticker, sector, in_nifty500 FROM symbols_master LIMIT 10000', {}, 12000);
        const indexResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000');
        
        const indicesMap = new Map<string, string[]>();
        if (indexResult && Array.isArray(indexResult)) {
          indexResult.forEach((row: any) => {
            if (indicesMap.has(row.symbol)) {
                indicesMap.get(row.symbol)!.push(row.index_name);
            } else {
                indicesMap.set(row.symbol, [row.index_name]);
            }
          });
        }
        const metaMap = new Map<string, { sector: string, bucket: string }>();
        if (symbolsResult) {
           for (const m of symbolsResult) {
              const indices = indicesMap.get(m.ticker) || [];
              const bucket = resolveBucket(indices, m.in_nifty500);
              metaMap.set(m.ticker, {
                  sector: m.sector,
                  bucket: bucket
              });
           }
        }
        if (active) {
          setMetadataMap(metaMap);
          setMetadataLoaded(true);
        }
      } catch (e) {
        console.error(e);
        if (active) setMetadataLoaded(true);
      }
    };
    fetchMeta();
    return () => { active = false; };
  }, [lib, settings.mockDataMode]);

  const generateMockCandidates = useCallback(() => {
    const rawMock: Omit<MultibaggerData, 'earningsYield' | 'multibagger_index' | 'moat_score'>[] = [
      { ticker: 'DIXON', sector: 'Manufacturing', returnOnEquity: 28.4, earningsPerShare: 45.2, peRatio: 85, accumulation: 68.5, bucket: 'Broader Market (N500)' },
      { ticker: 'KPITTECH', sector: 'IT Services', returnOnEquity: 22.1, earningsPerShare: 38.6, peRatio: 65, accumulation: 72.1, bucket: 'Broader Market (N500)' },
      { ticker: 'TRENT', sector: 'Retail', returnOnEquity: 19.5, earningsPerShare: 55.4, peRatio: 120, accumulation: 61.2, bucket: 'Large Cap (N100)' },
      { ticker: 'POLYCAB', sector: 'Industrials', returnOnEquity: 25.8, earningsPerShare: 28.9, peRatio: 45, accumulation: 58.4, bucket: 'Large Cap (N100)' },
      { ticker: 'HAL', sector: 'Defense', returnOnEquity: 32.4, earningsPerShare: 22.1, peRatio: 35, accumulation: 75.8, bucket: 'Large Cap (N50)' },
      { ticker: 'ZOMATO', sector: 'Consumer Tech', returnOnEquity: 12.5, earningsPerShare: 6.4, peRatio: 90, accumulation: 54.2, bucket: 'Large Cap (N100)' },
      { ticker: 'CDSL', sector: 'Financials', returnOnEquity: 42.1, earningsPerShare: 28.5, peRatio: 48, accumulation: 66.7, bucket: 'Broader Market (N500)' },
      { ticker: 'KAYNES', sector: 'Manufacturing', returnOnEquity: 18.2, earningsPerShare: 52.1, peRatio: 105, accumulation: 69.4, bucket: 'Broader Market (N500)' },
      { ticker: 'TATASTEEL', sector: 'Metals', returnOnEquity: 14.2, earningsPerShare: 18.5, peRatio: 12, accumulation: 42.1, bucket: 'Large Cap (N50)' }, 
      { ticker: 'RELIANCE', sector: 'Energy', returnOnEquity: 11.5, earningsPerShare: 14.2, peRatio: 28, accumulation: 50.1, bucket: 'Large Cap (N50)' } 
    ];

    let filtered = rawMock;
    if (filterMcap !== 'All') {
      filtered = filtered.filter(f => f.bucket === filterMcap);
    }
    if (excludeCyclical) {
      const cyclicals = ['Metals', 'Chemicals', 'Energy', 'Mining', 'Materials'];
      filtered = filtered.filter(f => !cyclicals.includes(f.sector));
    }
    
    const safeMinRoe = Math.max(-100, Math.min(100, Number(minRoe) || 0));
    const safeMinEps = Math.max(-1000, Math.min(1000, Number(minEps) || 0));
    
    filtered = filtered.filter(f => f.returnOnEquity >= safeMinRoe && f.earningsPerShare >= safeMinEps);
    if (requireAccumulation) {
      filtered = filtered.filter(f => f.accumulation >= 50);
    }

    const calculated: MultibaggerData[] = filtered.map(d => {
       const earningsYieldPct = d.peRatio > 0 ? (1 / d.peRatio) * 100 : 0;

       return {
         ...d, 
         earningsYield: earningsYieldPct,
         multibagger_index: calcMultibaggerIndex(d.returnOnEquity, earningsYieldPct, d.accumulation),
         moat_score: calcMoatScore(d.returnOnEquity)
       };
    });

    setData(calculated.sort((a, b) => b.multibagger_index - a.multibagger_index));
  }, [filterMcap, excludeCyclical, minRoe, minEps, requireAccumulation]);

  const fetchMultibaggerCandidates = useCallback(async () => {
    if (!metadataLoaded) return;
    setIsLoading(true);
    setErrorMsg(null);
    const mockMode = !lib.isConnectedToLocalRepo || settings.mockDataMode;
    
    try {
      let usedMock = false;
      if (mockMode) {
        if (!lib.isConnectedToLocalRepo) setErrorMsg('Database unavailable - generating mock data.');
        setIsDemo(true);
        generateMockCandidates();
        usedMock = true;
      }

      if (!usedMock) {
        setIsDemo(false);

        const safeMinRoe = Math.max(-100, Math.min(100, Number(minRoe) || 0));
        const safeMinEps = Math.max(-1000, Math.min(1000, Number(minEps) || 0));
        const safeDays = Math.max(1, Math.min(365 * 3, Math.floor(Number(days) || 180)));

        // 2. Fetch fundamentals
      let fundQuery = `
        SELECT symbol as ticker, sector, COALESCE(returnOnEquity, roe) as returnOnEquity, COALESCE(earningsPerShare, eps) as earningsPerShare, peRatio
        FROM fundamentals
        WHERE COALESCE(returnOnEquity, roe) > ? AND COALESCE(earningsPerShare, eps) > ?
      `;
      if (excludeCyclical) {
        fundQuery += ` AND sector NOT IN ('Metals', 'Chemicals', 'Energy', 'Mining', 'Materials')`;
      }
      const fundResult = await lib.executeQuery('_val_conn', fundQuery, [safeMinRoe, safeMinEps], 12000);
      
      if (fundResult && fundResult.length > 0) {
        const candidateTickers = fundResult
            .filter((r: any) => {
               const bucket = metadataMap.get(r.ticker)?.bucket || "Deep Frontier";
               return filterMcap === 'All' || bucket === filterMcap;
            })
            .map((r: any) => r.ticker as string);

        if (candidateTickers.length === 0) {
            setData([]);
            return;
        }

        const placeholders = candidateTickers.map(() => '?').join(',');
        
        // 3. Fetch Accumulation
        const techQuery = `
          SELECT symbol as ticker, SUM(delivery) * 100.0 / NULLIF(SUM(volume), 0) as accumulation
          FROM technical_data
          WHERE date >= date('now', ?) AND symbol IN (${placeholders})
          GROUP BY symbol
        `;
        const techResult = await lib.executeQuery('_tech_conn', techQuery, [`-${safeDays} days`, ...candidateTickers], 12000);
        
        const techMap = new Map();
        if (techResult && techResult.length > 0) {
           for (const t of techResult) {
              techMap.set(t.ticker, t.accumulation || 0);
           }
        }

        // 4. Fetch price to calculate exact earnings yield from technicals, or fallback to PE
        const priceQuery = `
          SELECT symbol as ticker, close FROM technical_data 
          WHERE date = (SELECT MAX(date) FROM technical_data) AND symbol IN (${placeholders})
        `;
        const priceResult = await lib.executeQuery('_tech_conn', priceQuery, [...candidateTickers], 12000);
        const priceMap = new Map();
        if (priceResult) {
            for (const p of priceResult) {
                priceMap.set(p.ticker, p.close);
            }
        }
        
        // Re-applies the bucket filter to fundResult rows (since fundResult contains all symbols).
        const bucketFilteredSet = new Set(candidateTickers);
        let mapped: MultibaggerData[] = fundResult.filter((d: any) => bucketFilteredSet.has(d.ticker)).map((d: any) => {
          const accumulation = techMap.get(d.ticker) || 0;
          const estPrice = ((Number(d.peRatio) || 0) * (Number(d.earningsPerShare) || 0)) || 1;
          const closePrice = priceMap.get(d.ticker) || estPrice;
          const earningsYield = closePrice ? (Number(d.earningsPerShare) / closePrice) : 0;
          const earningsYieldPct = earningsYield * 100;
          const bucket = metadataMap.get(d.ticker)?.bucket || "Deep Frontier";
          const roe = Number(d.returnOnEquity);
          
          return {
            ticker: d.ticker,
            sector: d.sector,
            returnOnEquity: roe,
            earningsPerShare: Number(d.earningsPerShare),
            peRatio: Number(d.peRatio),
            bucket,
            accumulation,
            earningsYield: earningsYieldPct,
            multibagger_index: calcMultibaggerIndex(roe, earningsYieldPct, accumulation),
            moat_score: calcMoatScore(roe)
          };
        });

        if (requireAccumulation) {
          mapped = mapped.filter((d) => d.accumulation >= 50);
        }

        mapped.sort((a, b) => b.multibagger_index - a.multibagger_index);
        
        setData(mapped.slice(0, 100));
      } else {
        setData([]);
      }
      }
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Database unavailable - generating mock data.');
      setIsDemo(true);
      generateMockCandidates();
    } finally {
      setIsLoading(false);
    }
  }, [lib, settings.mockDataMode, minRoe, minEps, days, excludeCyclical, requireAccumulation, filterMcap, metadataLoaded, metadataMap, generateMockCandidates]);

  useEffect(() => {
    if (metadataLoaded) {
      fetchMultibaggerCandidates();
    }
  }, [fetchMultibaggerCandidates, metadataLoaded]);

  const handleSort = (key: keyof MultibaggerData) => {
    setSortConfig((prev) => {
      if (!prev) return { key, direction: 'desc' };
      return {
        key,
        direction: prev.key === key && prev.direction === 'desc' ? 'asc' : 'desc'
      };
    });
  };

  const sortedData = useMemo(() => {
    let sortableItems = [...data];
    if (sortConfig !== null) {
      const moatOrder: Record<string, number> = { 'Monopoly': 4, 'High': 3, 'Medium': 2, 'Low': 1 };
      
      sortableItems.sort((a, b) => {
        let aVal = a[sortConfig.key];
        let bVal = b[sortConfig.key];

        // Handle logical sorting for Moat Score
        if (sortConfig.key === 'moat_score') {
          aVal = moatOrder[a.moat_score as string] || 0;
          bVal = moatOrder[b.moat_score as string] || 0;
        }

        if (aVal < bVal) {
          return sortConfig.direction === 'asc' ? -1 : 1;
        }
        if (aVal > bVal) {
          return sortConfig.direction === 'asc' ? 1 : -1;
        }
        return 0;
      });
    }
    return sortableItems;
  }, [data, sortConfig]);

  const SortIcon = ({ column }: { column: keyof MultibaggerData }) => {
    if (sortConfig?.key !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortConfig.direction === 'asc' 
      ? <ChevronUp size={10} className="inline ml-1 text-orange-400" /> 
      : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
  };


  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Rocket size={20} className="text-orange-500" />
          Multibagger Discovery Matrix
        </h3>
        <div className="flex gap-2 items-center">
          {errorMsg && (
             <span className="text-[10px] bg-red-500/20 text-red-500 px-2 py-1 rounded font-mono border border-red-500/30 flex items-center gap-1">
               <AlertTriangle size={10} /> {errorMsg}
             </span>
          )}
          {isDemo && (
             <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
               ⚠️ SIMULATED PIPELINE
             </span>
          )}
          <span className="text-xs text-[#888] font-mono">Module: alpha.secular_growth</span>
        </div>
      </div>

      <div className="p-6 grid grid-cols-1 lg:grid-cols-4 gap-6">
        
        {/* Left Side: Analytical Concepts & Metrics */}
        <div className="lg:col-span-1 flex flex-col gap-4">
          <div className="bg-orange-500/10 border border-orange-500/30 p-4 rounded-lg">
             <h4 className="text-orange-400 font-semibold text-sm flex items-center gap-2 mb-2">
               <Zap size={16} /> Earnings Yield & ROE
             </h4>
             <p className="text-xs text-[#ccc] leading-relaxed">
               This matrix plots <strong>Return on Equity (ROE)</strong> against <strong>Earnings Yield</strong> to identify systemic compounders trading at reasonable valuations. Targets in the top-right quadrant represent superior structural alphas.
             </p>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] p-4 rounded-lg flex-1 border-t-4 border-t-orange-500/50">
            <h4 className="font-semibold text-[#fafafa] text-sm mb-3">Risk Toggles / Logic State</h4>
            
            <PresetChip
              module="MultibaggerMatrix"
              currentConfig={{ excludeCyclical, requireAccumulation, minRoe, minEps, days, filterMcap }}
              onLoad={(config) => {
                const c = config as MultibaggerConfig;
                setExcludeCyclical(c.excludeCyclical);
                setRequireAccumulation(c.requireAccumulation);
                setMinRoe(c.minRoe);
                setMinRoeInput(c.minRoe);
                setMinEps(c.minEps);
                setMinEpsInput(c.minEps);
                setDays(c.days);
                setDaysInput(c.days);
                setFilterMcap(c.filterMcap);
              }}
              accentColor="orange"
            />

            <div className="space-y-3 mt-4">
               <label className="flex items-center gap-3 p-2 bg-[#1a1c24] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff0a] transition-colors">
                 <input 
                   type="checkbox" 
                   checked={excludeCyclical}
                   onChange={(e) => setExcludeCyclical(e.target.checked)}
                   className="accent-orange-500" 
                 />
                 <span className="text-xs text-[#eee]">Exclude Cyclicals (Metals, Energy)</span>
               </label>
               <label className="flex items-center gap-3 p-2 bg-[#1a1c24] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff0a] transition-colors">
                 <input 
                   type="checkbox" 
                   checked={requireAccumulation}
                   onChange={(e) => setRequireAccumulation(e.target.checked)}
                   className="accent-orange-500" 
                 />
                 <span className="text-xs text-[#eee]">Require High Accumulation</span>
               </label>
               
               {/* Market Cap Filter */}
               <div className="flex flex-col pt-2 border-t border-[#ffffff1a]">
                  <label className="text-[10px] text-[#888] font-mono mb-1">Market Cap Category</label>
                  <select 
                    value={filterMcap} 
                    onChange={(e) => setFilterMcap(e.target.value)}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"
                  >
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="All">All</option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="Large Cap (N50)">Large Cap (N50)</option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="Large Cap (N100)">Large Cap (N100)</option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="Broader Market (N500)">Broader Market (N500)</option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="Nifty Small Cap 250">Nifty Small Cap 250</option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value="Deep Frontier">Deep Frontier</option>
                  </select>
               </div>

               {/* Dynamic Threshold Inputs */}
               <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[#ffffff1a]">
                  <div className="flex flex-col">
                    <label className="text-[10px] text-[#888] font-mono mb-1">Min ROE (%)</label>
                    <input type="number" value={minRoeInput} onChange={(e) => setMinRoeInput(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"/>
                  </div>
                  <div className="flex flex-col">
                    <label className="text-[10px] text-[#888] font-mono mb-1">Min EPS (₹)</label>
                    <input type="number" value={minEpsInput} onChange={(e) => setMinEpsInput(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"/>
                  </div>
                  <div className="flex flex-col col-span-2">
                     <div className="flex justify-between text-[10px] text-[#888] font-mono mb-1">
                        <label title="Lookback period for delivery accumulation only.">Lookback Period (Days) (?)</label>
                        <span className="text-orange-400">{daysInput}</span>
                     </div>
                     <select value={daysInput} onChange={(e) => setDaysInput(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                       <option className="bg-[#1a1c24] text-[#fafafa]" value={30}>30 Days</option>
                       <option className="bg-[#1a1c24] text-[#fafafa]" value={90}>90 Days</option>
                       <option className="bg-[#1a1c24] text-[#fafafa]" value={180}>180 Days</option>
                       <option className="bg-[#1a1c24] text-[#fafafa]" value={365}>1 Year</option>
                     </select>
                  </div>
               </div>
            </div>
            {isLoading && <div className="text-xs text-orange-400 font-mono mt-4 animate-pulse flex items-center justify-center">Recalculating Tensor Matrix...</div>}
          </div>
        </div>

        {/* Right Side: The Matrix */}
        <div className="lg:col-span-3 flex flex-col gap-6">
          <div className="h-72 bg-[#0e1117] border border-[#ffffff0a] rounded-lg p-4 relative">
              <h4 className="text-xs font-mono text-[#888] uppercase mb-2 absolute top-4 left-4 z-10">ROE vs Earnings Yield (%)</h4>
             <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#222" />
                  <XAxis type="number" dataKey="earningsYield" name="Earnings Yield" unit="%" stroke="#666" tick={{fill:'#666', fontSize:10}} tickFormatter={(v) => v.toFixed(1)} />
                  <YAxis type="number" dataKey="returnOnEquity" name="ROE" unit="%" stroke="#666" tick={{fill:'#666', fontSize:10}} />
                  <ZAxis type="number" dataKey="multibagger_index" range={[100, 500]} name="Score" />
                  <Tooltip 
                    cursor={{strokeDasharray: '3 3'}}
                    contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', fontSize: '12px', color: '#fff', borderRadius: '4px' }}
                    formatter={(value: number, name: string) => {
                      if (name === "Earnings Yield" || name === "ROE") return `${value.toFixed(2)}%`;
                      return value;
                    }}
                  />
                  {/* Quadrant Lines matching ideal 'compounder' thresholds */}
                  <ReferenceLine x={4} stroke="#ffffff22" strokeDasharray="5 5" label={{ position: 'insideTopRight', value: 'High Yield', fill: '#ffffff44', fontSize: 10 }} />
                  <ReferenceLine y={20} stroke="#ffffff22" strokeDasharray="5 5" label={{ position: 'insideTopLeft', value: 'High ROE', fill: '#ffffff44', fontSize: 10 }} />
                  
                  <Scatter name="Candidates" data={data} fill="#f97316">
                    {data.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.moat_score === 'Monopoly' ? '#a855f7' : (entry.accumulation >= 65 ? '#22c55e' : '#f97316')} />
                    ))}
                  </Scatter>
                </ScatterChart>
             </ResponsiveContainer>
             {data.length === 0 && !isLoading && (
                 <div className="absolute inset-0 flex items-center justify-center text-sm font-mono text-[#666] bg-[#0e1117]/80 z-20 rounded-lg">
                     No candidates match the criteria.
                 </div>
             )}
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] rounded-lg overflow-hidden flex-1">
            <div className="overflow-x-auto max-h-64">
              <table className="w-full text-left font-mono text-xs relative">
                <thead className="bg-[#1a1c24] border-b border-[#ffffff1a] sticky top-0 z-10">
                  <tr className="text-[#888]">
                    <th className={`py-3 px-4 font-medium uppercase cursor-pointer hover:text-white transition-colors ${sortConfig?.key === 'ticker' ? 'text-white' : ''}`} onClick={() => handleSort('ticker')}>
                      Ticker <SortIcon column="ticker" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase cursor-pointer hover:text-white transition-colors ${sortConfig?.key === 'sector' ? 'text-white' : ''}`} onClick={() => handleSort('sector')}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white transition-colors ${sortConfig?.key === 'returnOnEquity' ? 'text-white' : ''}`} onClick={() => handleSort('returnOnEquity')}>
                      ROE <SortIcon column="returnOnEquity" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white transition-colors ${sortConfig?.key === 'earningsYield' ? 'text-white' : ''}`} onClick={() => handleSort('earningsYield')}>
                      Earn Yield <SortIcon column="earningsYield" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-green-300 transition-colors ${sortConfig?.key === 'accumulation' ? 'text-green-400' : 'text-green-400/70'}`} onClick={() => handleSort('accumulation')}>
                      Accumulation <SortIcon column="accumulation" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase text-center cursor-pointer hover:text-white transition-colors ${sortConfig?.key === 'moat_score' ? 'text-white' : ''}`} onClick={() => handleSort('moat_score')}>
                      Moat <SortIcon column="moat_score" />
                    </th>
                    <th className={`py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-orange-300 transition-colors ${sortConfig?.key === 'multibagger_index' ? 'text-orange-400' : 'text-orange-400/70'}`} onClick={() => handleSort('multibagger_index')}>
                      MB Index <SortIcon column="multibagger_index" />
                    </th>
                  </tr>
                </thead>
                <tbody className="text-[#ccc]">
                  {sortedData.map((row, idx) => (
                    <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                      <td className="py-2.5 px-4 font-bold text-white flex items-center gap-2">
                        {row.ticker}
                        {row.moat_score === 'Monopoly' && <ShieldAlert size={12} className="text-purple-400" />}
                      </td>
                      <td className="py-2.5 px-4 text-[#888]">{row.sector}</td>
                      <td className="py-2.5 px-4 text-right text-cyan-400">{(row.returnOnEquity || 0).toFixed(1)}%</td>
                      <td className="py-2.5 px-4 text-right">{(row.earningsYield || 0).toFixed(2)}%</td>
                      <td className="py-2.5 px-4 text-right text-green-400">{(row.accumulation || 0).toFixed(1)}%</td>
                      <td className="py-2.5 px-4 text-center">
                        <span className={`px-2 py-0.5 rounded text-[10px] ${row.moat_score === 'Monopoly' ? 'bg-purple-500/20 text-purple-400' : 'bg-[#ffffff1a] text-[#aaa]'}`}>
                          {row.moat_score}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 text-right font-bold text-orange-400">{row.multibagger_index}</td>
                    </tr>
                  ))}
                  {sortedData.length === 0 && (
                     <tr>
                       <td colSpan={7} className="py-8 text-center text-[#666]">No candidates match the structural criteria.</td>
                     </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
