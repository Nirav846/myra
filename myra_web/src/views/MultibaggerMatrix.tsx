import { useState, useEffect, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, ShieldAlert, Zap, ArrowUpDown } from 'lucide-react';
import { ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis, Tooltip, CartesianGrid, Cell, ReferenceLine } from 'recharts';

export default function MultibaggerMatrixView({ lib }: { lib: Librarian }) {
  const [data, setData] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(false);

  // Dynamic States for Toggles
  const [excludeCyclical, setExcludeCyclical] = useState(true);
  const [requireAccumulation, setRequireAccumulation] = useState(true);
  const [minRoe, setMinRoe] = useState(10);
  const [minEps, setMinEps] = useState(10);
  const [days, setDays] = useState(180);
  
  // Sorting State
  const [sortConfig, setSortConfig] = useState<{ key: string, direction: 'asc' | 'desc' }>({ key: 'multibagger_index', direction: 'desc' });

  useEffect(() => {
    fetchMultibaggerCandidates();
  }, [excludeCyclical, requireAccumulation, minRoe, minEps, days]);

  const fetchMultibaggerCandidates = async () => {
    setIsLoading(true);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      let query = `
        SELECT f.symbol as ticker, f.sector, f.roe, f.eps, AVG(r.delivery_pct) as accumulation
        FROM fundamentals f
        JOIN technical_data r ON f.symbol = r.symbol
        WHERE r.date >= date('now', '-${days} days') 
          AND f.roe > ${minRoe} 
          AND f.eps > ${minEps}
      `;

      if (excludeCyclical) {
        query += ` AND f.sector NOT IN ('Metals', 'Chemicals', 'Energy', 'Mining', 'Materials')`;
      }
      
      query += ` GROUP BY f.symbol`;
      
      if (requireAccumulation) {
        query += ` HAVING accumulation >= 50`;
      }

      query += ` ORDER BY (f.roe * f.eps) DESC LIMIT 25`;
      
      const result = await lib.executeQuery('_meta_conn', query, {}, 12000);
      
      if (result && result.length > 0) {
        const mapped = result.map((d: any) => ({
          ...d,
          multibagger_index: Math.round((Number(d.roe) * 0.4) + (Number(d.eps) * 0.4) + (Number(d.accumulation) * 0.2)),
          moat_score: Number(d.roe) > 25 ? 'Monopoly' : Number(d.roe) > 15 ? 'High' : 'Medium'
        }));
        setData(mapped);
      } else {
        setIsDemo(true);
        generateMockCandidates();
      }
    } catch (e: any) {
      console.error(e);
      setIsDemo(true);
      generateMockCandidates();
    } finally {
      setIsLoading(false);
    }
  };

  const generateMockCandidates = () => {
    const rawMock = [
      { ticker: 'DIXON', sector: 'Manufacturing', roe: 28.4, eps: 45.2, accumulation: 68.5, moat_score: 'High', risk: 'Medium' },
      { ticker: 'KPITTECH', sector: 'IT Services', roe: 22.1, eps: 38.6, accumulation: 72.1, moat_score: 'High', risk: 'Low' },
      { ticker: 'TRENT', sector: 'Retail', roe: 19.5, eps: 55.4, accumulation: 61.2, moat_score: 'Very High', risk: 'Medium' },
      { ticker: 'POLYCAB', sector: 'Industrials', roe: 25.8, eps: 28.9, accumulation: 58.4, moat_score: 'High', risk: 'Low' },
      { ticker: 'HAL', sector: 'Defense', roe: 32.4, eps: 22.1, accumulation: 75.8, moat_score: 'Monopoly', risk: 'Low' },
      { ticker: 'ZOMATO', sector: 'Consumer Tech', roe: 12.5, eps: 65.4, accumulation: 54.2, moat_score: 'High', risk: 'High' },
      { ticker: 'CDSL', sector: 'Financials', roe: 42.1, eps: 28.5, accumulation: 66.7, moat_score: 'Monopoly', risk: 'Low' },
      { ticker: 'KAYNES', sector: 'Manufacturing', roe: 18.2, eps: 52.1, accumulation: 69.4, moat_score: 'Medium', risk: 'High' },
      { ticker: 'TATASTEEL', sector: 'Metals', roe: 14.2, eps: 18.5, accumulation: 42.1, moat_score: 'Low', risk: 'High' }, 
      { ticker: 'RELIANCE', sector: 'Energy', roe: 11.5, eps: 14.2, accumulation: 50.1, moat_score: 'High', risk: 'Medium' } 
    ];

    let filtered = rawMock;
    if (excludeCyclical) {
      const cyclicals = ['Metals', 'Chemicals', 'Energy', 'Mining'];
      filtered = filtered.filter(f => !cyclicals.includes(f.sector));
    }
    
    filtered = filtered.filter(f => f.roe >= minRoe && f.eps >= minEps);
    if (requireAccumulation) {
      filtered = filtered.filter(f => f.accumulation >= 50);
    }

    const calculated = filtered.map(d => ({
       ...d, 
       multibagger_index: Math.round((d.roe * 0.4) + (d.eps * 0.4) + (d.accumulation * 0.2)),
       moat_score: d.roe > 25 ? 'Monopoly' : d.roe > 15 ? 'High' : 'Medium'
    }));

    setData(calculated);
  };

  const handleSort = (key: string) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === 'desc' ? 'asc' : 'desc'
    }));
  };

  const sortedData = useMemo(() => {
    let sortableItems = [...data];
    if (sortConfig !== null) {
      sortableItems.sort((a, b) => {
        if (a[sortConfig.key] < b[sortConfig.key]) {
          return sortConfig.direction === 'asc' ? -1 : 1;
        }
        if (a[sortConfig.key] > b[sortConfig.key]) {
          return sortConfig.direction === 'asc' ? 1 : -1;
        }
        return 0;
      });
    }
    return sortableItems;
  }, [data, sortConfig]);

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Rocket size={20} className="text-orange-500" />
          Multibagger Discovery Matrix
        </h3>
        <div className="flex gap-2 items-center">
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
               <Zap size={16} /> The "Magic Quadrant"
             </h4>
             <p className="text-xs text-[#ccc] leading-relaxed">
               Graham valuation ignores exponential growth. This matrix plots <strong>Return on Invested Capital (ROIC)</strong> against <strong>Revenue CAGR</strong> to identify systemic compounders. Targets in the top-right quadrant represent superior structural alphas.
             </p>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] p-4 rounded-lg flex-1 border-t-4 border-t-orange-500/50">
            <h4 className="font-semibold text-[#fafafa] text-sm mb-3">Risk Toggles / Logic State</h4>
            <div className="space-y-3">
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
               
               {/* Dynamic Threshold Inputs */}
               <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[#ffffff1a]">
                  <div className="flex flex-col">
                    <label className="text-[10px] text-[#888] font-mono mb-1">Min ROE (%)</label>
                    <input type="number" value={minRoe} onChange={(e) => setMinRoe(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"/>
                  </div>
                  <div className="flex flex-col">
                    <label className="text-[10px] text-[#888] font-mono mb-1">Min EPS (₹)</label>
                    <input type="number" value={minEps} onChange={(e) => setMinEps(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"/>
                  </div>
                  <div className="flex flex-col col-span-2">
                     <label className="text-[10px] text-[#888] font-mono mb-1">Lookback Period (Days)</label>
                     <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                       <option value={30}>30 Days</option>
                       <option value={90}>90 Days</option>
                       <option value={180}>180 Days</option>
                       <option value={365}>1 Year</option>
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
              <h4 className="text-xs font-mono text-[#888] uppercase mb-2 absolute top-4 left-4 z-10">ROE vs EPS Landscape</h4>
             <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#222" />
                  <XAxis type="number" dataKey="eps" name="EPS" unit="₹" stroke="#666" tick={{fill:'#666', fontSize:10}} />
                  <YAxis type="number" dataKey="roe" name="ROE" unit="%" stroke="#666" tick={{fill:'#666', fontSize:10}} />
                  <ZAxis type="number" dataKey="multibagger_index" range={[100, 500]} name="Score" />
                  <Tooltip 
                    cursor={{strokeDasharray: '3 3'}}
                    contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', fontSize: '12px', color: '#fff', borderRadius: '4px' }}
                  />
                  {/* Quadrant Lines matching ideal 'compounder' thresholds */}
                  <ReferenceLine x={25} stroke="#ffffff22" strokeDasharray="5 5" label={{ position: 'insideTopRight', value: 'High Growth', fill: '#ffffff44', fontSize: 10 }} />
                  <ReferenceLine y={20} stroke="#ffffff22" strokeDasharray="5 5" label={{ position: 'insideTopLeft', value: 'High ROIC', fill: '#ffffff44', fontSize: 10 }} />
                  
                  <Scatter name="Candidates" data={sortedData} fill="#f97316">
                    {sortedData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.moat_score === 'Monopoly' ? '#a855f7' : (entry.accumulation >= 65 ? '#22c55e' : '#f97316')} />
                    ))}
                  </Scatter>
                </ScatterChart>
             </ResponsiveContainer>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] rounded-lg overflow-hidden flex-1">
            <div className="overflow-x-auto max-h-64">
              <table className="w-full text-left font-mono text-xs relative">
                <thead className="bg-[#1a1c24] border-b border-[#ffffff1a] sticky top-0 z-10">
                  <tr className="text-[#888]">
                    <th className="py-3 px-4 font-medium uppercase cursor-pointer hover:text-white" onClick={() => handleSort('ticker')}>Ticker <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase cursor-pointer hover:text-white" onClick={() => handleSort('sector')}>Sector <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white" onClick={() => handleSort('roe')}>ROE <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white" onClick={() => handleSort('eps')}>EPS <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase text-right text-green-400 cursor-pointer hover:text-green-300" onClick={() => handleSort('accumulation')}>Accumulation <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase text-center cursor-pointer hover:text-white" onClick={() => handleSort('moat_score')}>Moat <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
                    <th className="py-3 px-4 font-medium uppercase text-right text-orange-400 cursor-pointer hover:text-orange-300" onClick={() => handleSort('multibagger_index')}>MB Index <ArrowUpDown size={10} className="inline ml-1 opacity-50"/></th>
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
                      <td className="py-2.5 px-4 text-right text-cyan-400">{row.roe}%</td>
                      <td className="py-2.5 px-4 text-right">₹{row.eps}</td>
                      <td className="py-2.5 px-4 text-right text-green-400">{row.accumulation}%</td>
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
