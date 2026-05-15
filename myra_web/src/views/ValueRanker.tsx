import { Librarian } from '../lib/Librarian';
import { useState, useEffect, useCallback } from 'react';
import { Copy, Check, RefreshCw, Target } from 'lucide-react';
import PresetChip from '../components/PresetChip';
import { ValueRankerConfig } from '../lib/scannerPresets';
import { resolveBucket } from '../lib/bucketUtils';

interface RankerData {
  symbol: string;
  sector: string;
  bucket: string;
  graham: number;
  price: number;
  eps: number;
  grahamMargin: number;
  earningsYield: number;
  roe: number;
  debtEquity: number;
  dividendYield: number;
  netMargin: number;
  score: number;
}

export default function ValueRankerView({ lib, onNavigate }: { lib: Librarian, onNavigate?: (tab: string, symbol?: string) => void }) {
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [apiData, setApiData] = useState<RankerData[]>([]);
  const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string; bucket: string }>>(new Map());
  const [metadataLoaded, setMetadataLoaded] = useState(false);
  
  // UI settings mapping exactly to ValueRankerConfig
  const [weights, setWeights] = useState({ graham: 25, earningsYield: 25, roe: 20, debtEquity: 15, dividendYield: 10, netMargin: 5 });
  const [minScore, setMinScore] = useState<number>(50);
  const [maxPE, setMaxPE] = useState<number>(30);
  const [filterSector, setFilterSector] = useState<string>('All');
  const [filterMcap, setFilterMcap] = useState<string>('All');
  
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  // Sorting
  const [sortCol, setSortCol] = useState<keyof RankerData>('score');
  const [sortAsc, setSortAsc] = useState<boolean>(false);

  // Pre-fetch metadata
  useEffect(() => {
    let active = true;
    const fetchMeta = async () => {
      try {
        if (!lib.isConnectedToLocalRepo) {
            if (active) setMetadataLoaded(true);
            return;
        }

        const symbolsResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, sector, in_nifty500 FROM symbols_master LIMIT 10000', [], 5000);
        const indexResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000', [], 5000);

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
        
        const m = new Map<string, { sector: string; bucket: string }>();
        if (symbolsResult && Array.isArray(symbolsResult)) {
            for (const r of symbolsResult) {
                const indices = indicesMap.get(r.symbol) || [];
                const bucket = resolveBucket(indices, r.in_nifty500);
                m.set(r.symbol, { sector: r.sector || 'Unknown', bucket });
            }
        }
        
        if (active) {
            setMetadataMap(m);
            setMetadataLoaded(true);
        }
      } catch (e) {
        console.error("Failed to load metadata", e);
      } finally {
        if (active) setMetadataLoaded(true);
      }
    };
    fetchMeta();
    return () => { active = false; };
  }, [lib]);

  const fetchData = useCallback(async () => {
    if (!metadataLoaded) return;
    setIsRefreshing(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);

    try {
      if (!lib.isConnectedToLocalRepo) {
        generateMockData();
        return;
      }

      // We normalize out negative or bizarre ROE/EPS values here:
      const query = `
        SELECT
          symbol,
          COALESCE(returnOnEquity, roe) as roe,
          COALESCE(earningsPerShare, eps) as eps,
          COALESCE(priceToBook, NULL) as pb_ratio,
          COALESCE(debtToEquity, debt_to_equity) as debt_equity,
          COALESCE(dividendYield, dividend_yield) as dividend_yield,
          COALESCE(peRatio, pe) as pe_ratio,
          book_value,
          market_cap,
          net_margin
        FROM fundamentals
        WHERE COALESCE(returnOnEquity, roe) > 0
          AND COALESCE(earningsPerShare, eps) > 0
          AND book_value > 0
          AND COALESCE(peRatio, pe) <= ?
      `;

      const result = await lib.executeQuery('_val_conn', query, [maxPE], 15000);

      if (result && result.length > 0) {
        const mappedData: RankerData[] = [];

        for (const r of result) {
          const sym = r.symbol;
          const meta = metadataMap.get(sym) || { sector: 'Unknown', bucket: 'Unknown' };
          const bucket = meta.bucket;

          // Filtering
          if (filterSector !== 'All' && meta.sector !== filterSector) continue;
          if (filterMcap !== 'All' && bucket !== filterMcap) continue;

          // Compute raw values
          const eps = Number(r.eps || 0);
          const bv = Number(r.book_value || 0);
          const graham = Math.sqrt(22.5 * eps * bv);
          const pe = Number(r.pe_ratio || 0);
          const price = pe > 0 ? pe * eps : (Number(r.market_cap || 0) > 0 ? (Number(r.market_cap)) : 0); // Approximation if pe exists
          
          if (price <= 0) continue; // Need a price approximation

          const grahamMargin = graham > price ? ((graham - price) / graham) * 100 : 0;
          const earningsYield = (eps / price) * 100;
          const roe = Number(r.roe || 0);
          const de = Number(r.debt_equity || 0);
          const dy = Number(r.dividend_yield || 0);
          const nm = Number(r.net_margin || 0);

          // Scoring logic components (0-100 values)
          const sGraham = Math.max(0, Math.min(100, grahamMargin));
          const sEY = Math.max(0, Math.min(100, earningsYield));
          const sRoe = Math.max(0, Math.min(100, roe * 2)); // 50% ROE = 100 score
          const sDe = de > 0 ? Math.max(0, Math.min(100, (2 - de) / 2 * 100)) : 100; // lower is better
          const sDy = Math.max(0, Math.min(100, dy * 10)); // 10% yield = 100
          const sNm = Math.max(0, Math.min(100, nm * 2));

          const totalWeight = weights.graham + weights.earningsYield + weights.roe + weights.debtEquity + weights.dividendYield + weights.netMargin;
          const normalizedWeights = {
            graham: totalWeight > 0 ? weights.graham / totalWeight : 0,
            earningsYield: totalWeight > 0 ? weights.earningsYield / totalWeight : 0,
            roe: totalWeight > 0 ? weights.roe / totalWeight : 0,
            debtEquity: totalWeight > 0 ? weights.debtEquity / totalWeight : 0,
            dividendYield: totalWeight > 0 ? weights.dividendYield / totalWeight : 0,
            netMargin: totalWeight > 0 ? weights.netMargin / totalWeight : 0,
          };

          const rawScore = 
            (sGraham * normalizedWeights.graham) +
            (sEY * normalizedWeights.earningsYield) +
            (sRoe * normalizedWeights.roe) +
            (sDe * normalizedWeights.debtEquity) +
            (sDy * normalizedWeights.dividendYield) +
            (sNm * normalizedWeights.netMargin);

          if (rawScore >= minScore) {
            mappedData.push({
              symbol: sym,
              sector: meta.sector,
              bucket,
              graham,
              price,
              eps,
              grahamMargin,
              earningsYield,
              roe,
              debtEquity: de,
              dividendYield: dy,
              netMargin: nm,
              score: rawScore
            });
          }
        }
        
        let sorted = mappedData.sort((a, b) => {
          const av = a[sortCol];
          const bv = b[sortCol];
          if (typeof av === 'number' && typeof bv === 'number') {
            return sortAsc ? av - bv : bv - av;
          }
          if (typeof av === 'string' && typeof bv === 'string') {
            return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
          }
          return 0;
        });

        setApiData(sorted);
      } else {
        setApiData([]);
      }
      setDataLoaded(true);
      setLastRefreshed(new Date());
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Meta Connection Unavailable');
      setApiData([]);
    } finally {
      setIsRefreshing(false);
    }
  }, [lib, metadataLoaded, filterMcap, filterSector, maxPE, minScore, weights, sortCol, sortAsc, metadataMap]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const generateMockData = () => {
    const mockDb: RankerData[] = [
      { eps: 50, symbol: 'ITC', sector: 'FMCG', bucket: 'Large Cap (N100)', graham: 540.20, price: 412.50, grahamMargin: 23, earningsYield: 8.5, roe: 35, debtEquity: 0.1, dividendYield: 4.5, netMargin: 25, score: 0 },
      { eps: 35, symbol: 'ONGC', sector: 'Energy', bucket: 'Large Cap (N100)', graham: 330.00, price: 265.10, grahamMargin: 19, earningsYield: 15.2, roe: 18, debtEquity: 0.8, dividendYield: 7.2, netMargin: 12, score: 0 },
      { eps: 45, symbol: 'COALINDIA', sector: 'Energy', bucket: 'Large Cap (N100)', graham: 345.50, price: 280.00, grahamMargin: 18, earningsYield: 14.1, roe: 42, debtEquity: 0.2, dividendYield: 8.5, netMargin: 18, score: 0 },
      { eps: 20, symbol: 'NMDC', sector: 'Mining', bucket: 'Broader Market (N500)', graham: 255.80, price: 210.40, grahamMargin: 17, earningsYield: 12.5, roe: 28, debtEquity: 0.05, dividendYield: 6.2, netMargin: 15, score: 0 },
      { eps: 80, symbol: 'INFY', sector: 'IT Services', bucket: 'Large Cap (N100)', graham: 1850, price: 1420, grahamMargin: 23, earningsYield: 6.2, roe: 32, debtEquity: 0, dividendYield: 3.1, netMargin: 20, score: 0 },
      { eps: 65, symbol: 'SBI', sector: 'Financials', bucket: 'Large Cap (N100)', graham: 980, price: 750, grahamMargin: 23, earningsYield: 10.4, roe: 15, debtEquity: 1.2, dividendYield: 1.5, netMargin: 10, score: 0 }
    ];
    
    const mapped = mockDb.map(d => {
       const sGraham = Math.max(0, Math.min(100, d.grahamMargin));
       const sEY = Math.max(0, Math.min(100, d.earningsYield));
       const sRoe = Math.max(0, Math.min(100, d.roe * 2));
       const sDe = d.debtEquity > 0 ? Math.max(0, Math.min(100, (2 - d.debtEquity) / 2 * 100)) : 100;
       const sDy = Math.max(0, Math.min(100, d.dividendYield * 10));
       const sNm = Math.max(0, Math.min(100, d.netMargin * 2));

       const totalWeight = weights.graham + weights.earningsYield + weights.roe + weights.debtEquity + weights.dividendYield + weights.netMargin;
       const normalizedWeights = {
         graham: totalWeight > 0 ? weights.graham / totalWeight : 0,
         earningsYield: totalWeight > 0 ? weights.earningsYield / totalWeight : 0,
         roe: totalWeight > 0 ? weights.roe / totalWeight : 0,
         debtEquity: totalWeight > 0 ? weights.debtEquity / totalWeight : 0,
         dividendYield: totalWeight > 0 ? weights.dividendYield / totalWeight : 0,
         netMargin: totalWeight > 0 ? weights.netMargin / totalWeight : 0,
       };

       const score = 
        (sGraham * normalizedWeights.graham) +
        (sEY * normalizedWeights.earningsYield) +
        (sRoe * normalizedWeights.roe) +
        (sDe * normalizedWeights.debtEquity) +
        (sDy * normalizedWeights.dividendYield) +
        (sNm * normalizedWeights.netMargin);
       
       return { ...d, score };
    });
    
    let sorted = mapped.filter(x => x.score >= minScore).sort((a, b) => {
        const av = (a as any)[sortCol];
        const bv = (b as any)[sortCol];
        if (typeof av === 'number' && typeof bv === 'number') { return sortAsc ? av - bv : bv - av; }
        return 0;
    });

    setApiData(sorted);
    setDataLoaded(true);
    setLastRefreshed(new Date());
    setIsLoading(false);
  };

  const setWeight = (factor: keyof typeof weights, val: number) => {
      setWeights(prev => ({ ...prev, [factor]: val }));
  };

  const handleCopy = () => {
    const dataString = apiData 
      ? "Ticker\tSector\tPrice\tGraham\tMargin %\tScore\n" + apiData.map(r => `${r.symbol}\t${r.sector}\t${r.price.toFixed(2)}\t${r.graham.toFixed(2)}\t${r.grahamMargin.toFixed(1)}%\t${r.score.toFixed(1)}`).join('\n')
      : "";
    navigator.clipboard.writeText(dataString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const setSort = (col: keyof RankerData) => {
      if (sortCol === col) setSortAsc(!sortAsc);
      else { setSortCol(col); setSortAsc(false); }
  };

  const [isLoading, setIsLoading] = useState(false);

  return (
    <div className="bg-[#262730] rounded-lg flex flex-col h-full overflow-hidden">
      <div className="p-4 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center shrink-0">
        <span className="text-sm font-semibold uppercase tracking-wider text-[#fafafa] flex items-center gap-2">
          <Target size={16} className="text-green-400" />
          Value Ranker (Graham+)
        </span>
        <div className="flex gap-4 items-center">
            {lastRefreshed && <span className="text-[10px] text-[#888] font-mono">Last refreshed: {lastRefreshed.toLocaleTimeString()}</span>}
            <span className="text-[10px] font-mono text-[#666]">Module: _val_conn.fundamentals</span>
        </div>
      </div>

      <div className="p-4 flex-1 overflow-auto flex flex-col gap-4 relative">
        <div className="flex flex-col gap-3 bg-[#12141a] border border-white/5 p-4 rounded-lg shadow-sm shrink-0">
            <PresetChip
                module="ValueRanker"
                currentConfig={{ weights, minScore, maxPE, filterSector, filterMcap }}
                onLoad={(config) => {
                    const c = config as ValueRankerConfig;
                    setWeights(c.weights);
                    setMinScore(c.minScore);
                    setMaxPE(c.maxPE);
                    setFilterSector(c.filterSector);
                    setFilterMcap(c.filterMcap);
                }}
                accentColor="green"
            />

            <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mt-2">
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">Graham Margin</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.graham}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.graham} onChange={(e) => setWeight('graham', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">Earnings Yield</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.earningsYield}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.earningsYield} onChange={(e) => setWeight('earningsYield', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">ROE</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.roe}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.roe} onChange={(e) => setWeight('roe', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">Debt/Equity (Inv)</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.debtEquity}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.debtEquity} onChange={(e) => setWeight('debtEquity', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">Dividend Yield</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.dividendYield}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.dividendYield} onChange={(e) => setWeight('dividendYield', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
                <div className="flex flex-col">
                   <div className="flex justify-between items-center mb-1">
                      <label className="text-[10px] text-[#888] font-mono uppercase">Net Margin</label>
                      <span className="text-[10px] text-green-400 font-mono">{weights.netMargin}</span>
                   </div>
                   <input type="range" min="0" max="100" value={weights.netMargin} onChange={(e) => setWeight('netMargin', Number(e.target.value))} className="w-full accent-green-500" />
                </div>
            </div>

            <div className="flex flex-col md:flex-row gap-4 justify-between items-end mt-2 pt-4 border-t border-[#ffffff1a]">
                <div className="flex gap-4 flex-wrap">
                    <div className="flex flex-col">
                      <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Min Score</label>
                      <div className="flex items-center gap-2">
                        <input type="range" min="0" max="100" value={minScore} onChange={e => setMinScore(Number(e.target.value))} className="w-24 accent-green-500" />
                        <span className="text-[10px] font-mono w-6">{minScore}</span>
                      </div>
                    </div>
                    <div className="flex flex-col">
                      <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Max PE</label>
                      <input type="number" value={maxPE} onChange={e => setMaxPE(Number(e.target.value))} className="w-20 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] font-mono focus:border-green-500 outline-none" />
                    </div>
                    <div className="flex flex-col">
                      <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Sector</label>
                      <select value={filterSector} onChange={e => setFilterSector(e.target.value)} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] font-mono focus:border-green-500 outline-none">
                        <option value="All">All Sectors</option>
                        <option value="Technology">Technology</option>
                        <option value="Financial Services">Financials</option>
                        <option value="Industrials">Industrials</option>
                        <option value="Consumer Defensive">FMCG</option>
                        <option value="Basic Materials">Materials</option>
                        <option value="Energy">Energy</option>
                      </select>
                    </div>
                    <div className="flex flex-col">
                      <label className="text-[10px] text-[#888] font-mono uppercase mb-1">Market Cap</label>
                      <select value={filterMcap} onChange={e => setFilterMcap(e.target.value)} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] font-mono focus:border-green-500 outline-none">
                        <option value="All">All</option>
                        <option value="Large Cap (N100)">Large Cap (N100)</option>
                        <option value="Broader Market (N500)">Broader (N500)</option>
                        <option value="Small/Mid Cap">Small/Mid Cap</option>
                      </select>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
                    {isDemo && <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono">⚠️ DEMO MODE</span>}
                    {isRefreshing && <span className="text-xs text-green-400 font-mono animate-pulse">Scanning...</span>}
                </div>
            </div>
        </div>

        {dataLoaded && apiData.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 shrink-0">
            {apiData.slice(0, 3).map((topDog, i) => (
              <div key={topDog.symbol} onClick={() => onNavigate?.('Technical Chart', topDog.symbol)} className="bg-[#12141a] border border-[#ffffff1a] p-3 rounded-lg flex items-center justify-between hover:border-green-500/50 cursor-pointer group transition-colors shadow-sm">
                 <div>
                   <h3 className="font-bold text-white text-sm group-hover:text-green-400 inline-flex items-center gap-2">
                      <span className="text-green-500 opacity-50 px-1 py-0.5 bg-green-500/10 rounded text-[10px]">#{i+1}</span>
                      {topDog.symbol}
                   </h3>
                   <div className="text-[10px] text-[#888] mt-1 space-x-2 font-mono">
                     <span>Margin: {topDog.grahamMargin.toFixed(1)}%</span>
                     <span>PE: {(topDog.price/topDog.eps).toFixed(1)}</span>
                   </div>
                 </div>
                 <div className="text-right">
                   <div className="text-sm font-semibold text-white">{topDog.score.toFixed(1)}</div>
                   <div className="text-[10px] text-[#666] font-mono">Score</div>
                 </div>
              </div>
            ))}
          </div>
        )}

        <div className={`flex-1 border border-[#ffffff1a] rounded flex justify-start bg-black/40 overflow-hidden relative transition-opacity duration-300 ${isRefreshing || isLoading ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            className="absolute top-2 right-2 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors z-10"
            title="Copy Results"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <div className="w-full h-full overflow-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead className="sticky top-0 bg-[#1e2028] z-0 shadow-md">
                  <tr className="text-[#888] border-b border-[#ffffff1a]">
                    <th className="py-3 px-3 font-medium uppercase cursor-pointer hover:bg-[#ffffff0a] select-none text-nowrap" onClick={() => setSort('symbol')}>Symbol {sortCol==='symbol'&&(sortAsc?'↑':'↓')}</th>
                    <th className="py-3 px-3 font-medium text-nowrap">Sector</th>
                    <th className="py-3 px-3 font-medium text-nowrap">Cap</th>
                    <th className="py-3 px-3 font-medium text-right text-nowrap">Price</th>
                    <th className="py-3 px-3 font-medium text-right text-nowrap cursor-pointer hover:bg-[#ffffff0a] select-none" onClick={() => setSort('graham')}>Graham {sortCol==='graham'&&(sortAsc?'↑':'↓')}</th>
                    <th className="py-3 px-3 font-medium text-right text-nowrap cursor-pointer hover:bg-[#ffffff0a] select-none" onClick={() => setSort('grahamMargin')}>Margin {sortCol==='grahamMargin'&&(sortAsc?'↑':'↓')}</th>
                    <th className="py-3 px-3 font-medium text-right text-nowrap">Score</th>
                  </tr>
                </thead>
                <tbody className="text-[#ccc] divide-y divide-[#ffffff0a]">
                  {dataLoaded && apiData && apiData.map((row, idx) => (
                    <tr key={idx} className="hover:bg-[#ffffff05] transition-colors group">
                      <td className="py-2.5 px-3">
                         <span onClick={() => onNavigate?.('Technical Chart', row.symbol)} className="text-[#fafafa] font-bold cursor-pointer hover:text-green-400">
                           {row.symbol}
                         </span>
                      </td>
                      <td className="py-2.5 px-3 text-[#888] truncate max-w-[100px]">{row.sector}</td>
                      <td className="py-2.5 px-3 truncate max-w-[80px]">{row.bucket.split(' ')[0]}</td>
                      <td className="py-2.5 px-3 text-right">{row.price.toFixed(1)}</td>
                      <td className="py-2.5 px-3 text-right font-medium">{row.graham.toFixed(1)}</td>
                      <td className="py-2.5 px-3 text-right">
                         <span className={row.grahamMargin > 20 ? 'text-green-400' : 'text-[#bbb]'}>{(row.grahamMargin).toFixed(1)}%</span>
                      </td>
                      <td className="py-2.5 px-3 text-right font-bold w-[120px]">
                         <div className="flex items-center justify-end gap-2 w-full">
                           <span className="w-8 shrink-0">{row.score.toFixed(1)}</span>
                           <div className="w-16 h-1.5 bg-[#ffffff1a] rounded overflow-hidden flex shrink-0">
                               <div className="h-full bg-green-500" style={{ width: `${row.score}%` }} />
                           </div>
                         </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {dataLoaded && apiData.length === 0 && (
                <div className="w-full py-12 text-center text-[#666] text-xs font-mono flex flex-col items-center">
                    <Target size={24} className="mb-2 opacity-30" />
                    No stocks pass the current filters
                </div>
              )}
          </div>
        </div>
      </div>
    </div>
  );
}
