import { useState, useEffect } from 'react';
import { Librarian } from '../lib/Librarian';
import { Map as MapIcon, Activity, TrendingUp, Calendar, Search } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';

export default function SectorFlowView({ lib }: { lib: Librarian }) {
  const [data, setData] = useState<any[]>([]);
  const [startDate, setStartDate] = useState(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]); // Default 7 days ago
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [filterMcap, setFilterMcap] = useState<string>('All');
  const [filterSector, setFilterSector] = useState<string>('All');
  const [groupBy, setGroupBy] = useState<'sector'|'index'>('sector');
  const [availableSectors, setAvailableSectors] = useState<string[]>([]);

  useEffect(() => {
    fetchSectorFlows();
  }, [groupBy, filterMcap, filterSector]);

  const fetchSectorFlows = async () => {
    setLoading(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      const symbolsQuery = `SELECT symbol as ticker, sector, in_nifty500 FROM symbols_master`;
      const symbolsResult = await lib.executeQuery('_meta_conn', symbolsQuery, {}, 12000);
      
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

      const metaMap = new Map();
      const sectors = new Set<string>();
      
      if (symbolsResult) {
         for (const m of symbolsResult) {
            const indices = indicesMap.get(m.ticker) || [];
            let bucket = "Deep Frontier";
            if (indices.some((i: string) => i.includes('NIFTY 50') && !i.includes('NEXT'))) {
                bucket = "Large Cap (N50)";
            } else if (indices.some((i: string) => i.includes('NIFTY NEXT 50'))) {
                bucket = "Large Cap (N100)";
            } else if (m.in_nifty500 === 1 || indices.some((i: string) => i.includes('NIFTY 500'))) {
                bucket = "Broader Market (N500)";
            }
            const normalizedSector = (m.sector && m.sector.trim() !== '') ? m.sector : 'Uncharted Sector';
            sectors.add(normalizedSector);
            metaMap.set(m.ticker, {
                sector: normalizedSector,
                bucket: bucket
            });
         }
      }
      
      setAvailableSectors(Array.from(sectors).sort());

      const techQuery = `
        SELECT symbol as ticker, SUM(delivery * close) as raw_flow, AVG((delivery * 100.0) / NULLIF(volume, 0)) as delivery_pct
        FROM technical_data
        WHERE date >= '${startDate}' AND date <= '${endDate}'
        GROUP BY symbol
      `;
      const techResult = await lib.executeQuery('_tech_conn', techQuery, {}, 12000);

      if (techResult && techResult.length > 0) {
         const flowMap: any = {};
         for (const t of techResult) {
            const meta = metaMap.get(t.ticker) || { sector: 'Uncharted Sector', bucket: 'Deep Frontier' };
            
            if (filterMcap !== 'All' && meta.bucket !== filterMcap) continue;
            if (filterSector !== 'All' && meta.sector !== filterSector) continue;
            
            const groupKey = groupBy === 'sector' ? meta.sector : meta.bucket;
            
            if (!flowMap[groupKey]) {
               flowMap[groupKey] = { capital_flow: 0, sum_delivery: 0, count: 0 };
            }
            flowMap[groupKey].capital_flow += t.raw_flow || 0;
            if (t.delivery_pct) {
               flowMap[groupKey].sum_delivery += t.delivery_pct;
               flowMap[groupKey].count += 1;
            }
         }

         const finalData = Object.keys(flowMap).map(key => ({
            name: key,
            flow: flowMap[key].capital_flow,
            avg_del: flowMap[key].count > 0 ? (flowMap[key].sum_delivery / flowMap[key].count) : 0
         })).sort((a, b) => b.flow - a.flow);
         
         setData(finalData);
      } else {
        setIsDemo(true);
        setErrorMsg('No data returned for timeframe.');
        generateMockData();
      }
    } catch (e: any) {
      console.error(e);
      setIsDemo(true);
      setErrorMsg(e.message || 'Database unavailable - generating mock data.');
      generateMockData();
    } finally {
      setLoading(false);
    }
  };

  const generateMockData = () => {
    const start = new Date(startDate);
    const end = new Date(endDate);
    const diffTime = Math.abs(end.getTime() - start.getTime());
    const diffDays = Math.max(1, Math.ceil(diffTime / (1000 * 60 * 60 * 24)));
    const scale = (diffDays / 7);

    if (groupBy === 'sector') {
        setData([
          { name: 'IT Services', flow: 4500000000 * scale, avg_del: 68.2 },
          { name: 'Financials', flow: 3800000000 * scale, avg_del: 55.4 },
          { name: 'Auto', flow: 2100000000 * scale, avg_del: 61.1 },
          { name: 'Pharma', flow: 1800000000 * scale, avg_del: 42.8 },
          { name: 'FMCG', flow: 1200000000 * scale, avg_del: 71.5 },
          { name: 'Energy', flow: 950000000 * scale, avg_del: 35.2 },
          { name: 'Metals', flow: -400000000 * scale, avg_del: 22.1 },
        ]);
    } else {
        setData([
          { name: 'Large Cap (N50)', flow: 9500000000 * scale, avg_del: 62.2 },
          { name: 'Large Cap (N100)', flow: 4200000000 * scale, avg_del: 58.4 },
          { name: 'Broader Market (N500)', flow: 2100000000 * scale, avg_del: 60.1 },
          { name: 'Deep Frontier', flow: 600000000 * scale, avg_del: 45.8 },
        ]);
    }
  };

  const formatCurrency = (val: number) => {
    if (Math.abs(val) > 1000000000) return `₹${(val / 1000000000).toFixed(1)}B`;
    if (Math.abs(val) > 1000000) return `₹${(val / 1000000).toFixed(1)}M`;
    return `₹${val}`;
  };

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <MapIcon size={20} className="text-cyan-400" />
          Sector Rotation & Liquidity Heatmap
        </h3>
        <div className="flex items-center gap-3">
          {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
          {isDemo && (
             <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
               ⚠️ SIMULATED DATA
             </span>
          )}
          <span className="text-xs text-[#888] font-mono">Module: meta.cross_flow</span>
        </div>
      </div>

      <div className="p-6 flex flex-col gap-6">
        
        {/* Date Range Filter Controls */}
        <div className="flex flex-wrap gap-4 items-end bg-[#0e1117] p-4 rounded-lg border border-[#ffffff0a]">
          <div className="w-48">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Group By</label>
            <div className="flex bg-[#1a1c24] rounded border border-[#ffffff1a] items-center h-[38px] p-1">
               <button
                  className={`flex-1 h-full rounded text-xs font-mono transition-colors ${groupBy === 'sector' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#fff]'}`}
                  onClick={() => setGroupBy('sector')}
               >
                 Sector
               </button>
               <button
                  className={`flex-1 h-full rounded text-xs font-mono transition-colors ${groupBy === 'index' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#fff]'}`}
                  onClick={() => setGroupBy('index')}
               >
                 Index
               </button>
            </div>
          </div>
          <div className="flex-1 min-w-[150px]">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Market Cap Category</label>
            <select 
                value={filterMcap} 
                onChange={(e) => setFilterMcap(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 h-[38px] text-sm text-[#fafafa] font-mono select-none outline-none focus:border-cyan-500"
            >
                <option className="bg-[#1a1c24] text-[#fafafa]" value="All">All</option>
                <option className="bg-[#1a1c24] text-[#fafafa]" value="Large Cap (N50)">Large Cap (N50)</option>
                <option className="bg-[#1a1c24] text-[#fafafa]" value="Large Cap (N100)">Large Cap (N100)</option>
                <option className="bg-[#1a1c24] text-[#fafafa]" value="Broader Market (N500)">Broader Market (N500)</option>
                <option className="bg-[#1a1c24] text-[#fafafa]" value="Deep Frontier">Deep Frontier</option>
            </select>
          </div>
          <div className="flex-1 min-w-[150px]">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Filter Sector</label>
            <select 
                value={filterSector} 
                onChange={(e) => setFilterSector(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 h-[38px] text-sm text-[#fafafa] font-mono select-none outline-none focus:border-cyan-500"
            >
                <option className="bg-[#1a1c24] text-[#fafafa]" value="All">All Sectors</option>
                {availableSectors.map(s => <option className="bg-[#1a1c24] text-[#fafafa]" key={s} value={s}>{s}</option>)}
                {isDemo && availableSectors.length === 0 && (
                    <>
                        <option className="bg-[#1a1c24] text-[#fafafa]" value="IT Services">IT Services</option>
                        <option className="bg-[#1a1c24] text-[#fafafa]" value="Financials">Financials</option>
                        <option className="bg-[#1a1c24] text-[#fafafa]" value="Energy">Energy</option>
                    </>
                )}
            </select>
          </div>
          
          <div className="w-36">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Start Date</label>
            <div className="relative">
              <Calendar size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-8 pr-2 h-[38px] text-xs focus:outline-none focus:border-cyan-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <div className="w-36">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">End Date</label>
            <div className="relative">
              <Calendar size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={endDate}
                onChange={e => setEndDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-8 pr-2 h-[38px] text-xs focus:outline-none focus:border-cyan-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <button 
            onClick={fetchSectorFlows}
            disabled={loading}
            className="px-6 h-[38px] bg-cyan-600 hover:bg-cyan-700 text-white font-medium rounded text-sm transition-colors disabled:opacity-50 flex items-center justify-center min-w-[120px] gap-2"
          >
            {loading ? <Search size={16} className="animate-pulse" /> : <Activity size={16} />}
            {loading ? 'Crunching...' : 'Analyze Flow'}
          </button>
        </div>

        <div className="bg-[#0e1117] border border-[#ffffff0a] p-4 rounded-lg flex items-center gap-3">
          <TrendingUp className="text-cyan-400" size={24} />
          <div>
             <h4 className="font-semibold text-sm">Net Capital Flow (Custom Range)</h4>
             <p className="text-xs text-[#888] mt-1 pr-6">Measures total capital absorbed by institutional delivery across correlated sectors or indices. Darker bars indicate higher underlying delivery percentages over the selected period.</p>
          </div>
        </div>

        <div className="flex-1 bg-[#1a1c24] rounded-lg border border-[#ffffff0a] p-4 min-h-[400px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 50 }}>
              <XAxis 
                dataKey="name" 
                stroke="#888" 
                tick={{ fill: '#888', fontSize: 11 }} 
                angle={-45} 
                textAnchor="end" 
              />
              <YAxis 
                stroke="#666" 
                tickFormatter={formatCurrency} 
                tick={{ fill: '#666', fontSize: 10 }}
              />
              <Tooltip 
                cursor={{ fill: '#ffffff0a' }}
                contentStyle={{ backgroundColor: '#0e1117', border: '1px solid #333', borderRadius: '4px' }}
                formatter={(value: number, name: string) => {
                  if (name === "Net Flow") return formatCurrency(value);
                  return value;
                }}
              />
              <Bar dataKey="flow" name="Net Flow">
                {data.map((entry, index) => {
                   // Color intensity based on delivery percentage
                   const isNegative = entry.flow < 0;
                   const intensity = entry.avg_del > 60 ? '500' : entry.avg_del > 40 ? '600' : '800';
                   const color = isNegative ? '#ef4444' : (intensity === '500' ? '#06b6d4' : intensity === '600' ? '#0891b2' : '#155e75');
                   return <Cell key={`cell-${index}`} fill={color} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        
        <div className="flex gap-4 justify-end text-xs font-mono text-[#666]">
           <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-cyan-400 rounded-sm"></div> Strong Accumulation (&gt;60% Del)</div>
           <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-cyan-700 rounded-sm"></div> Weak Absorption</div>
           <div className="flex items-center gap-1.5"><div className="w-3 h-3 bg-red-500 rounded-sm"></div> Capital Outflow</div>
        </div>
      </div>
    </div>
  );
}
