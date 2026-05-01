import { useState, useEffect } from 'react';
import { Librarian } from '../lib/Librarian';
import { Map, Activity, TrendingUp, Calendar, Search } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';

export default function SectorFlowView({ lib }: { lib: Librarian }) {
  const [data, setData] = useState<any[]>([]);
  const [startDate, setStartDate] = useState(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]); // Default 7 days ago
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    fetchSectorFlows();
  }, []);

  const fetchSectorFlows = async () => {
    setLoading(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      // In production, this JOINs raw_ohlcv delivery data with fundamentals categorization
      const query = `
        SELECT f.sector, SUM(r.delivery_qty * r.close) as capital_flow, AVG(r.delivery_pct) as avg_delivery 
        FROM fundamentals f 
        JOIN technical_data r ON f.symbol = r.symbol 
        WHERE r.date >= '${startDate}' AND r.date <= '${endDate}'
        GROUP BY f.sector 
        ORDER BY capital_flow DESC
      `;
      const result = await lib.executeQuery('_meta_conn', query); // Abstracting the JOIN via logic
      
      if (result && result.length > 0) {
        setData(result);
      } else {
        setIsDemo(true);
        setErrorMsg('No data returned for timeframe.');
        generateMockSectors();
      }
    } catch (e: any) {
      console.error(e);
      setIsDemo(true);
      setErrorMsg(e.message || 'Database unavailable - generating mock data.');
      generateMockSectors();
    } finally {
      setLoading(false);
    }
  };

  const generateMockSectors = () => {
    // Math logic based on the date range interval to make mocks realistic
    const start = new Date(startDate);
    const end = new Date(endDate);
    const diffTime = Math.abs(end.getTime() - start.getTime());
    const diffDays = Math.max(1, Math.ceil(diffTime / (1000 * 60 * 60 * 24)));
    
    // Scale multiplier based on how long the duration is
    const scale = (diffDays / 7);

    setData([
      { sector: 'IT Services', flow: 4500000000 * scale, avg_del: 68.2 },
      { sector: 'Financials', flow: 3800000000 * scale, avg_del: 55.4 },
      { sector: 'Auto', flow: 2100000000 * scale, avg_del: 61.1 },
      { sector: 'Pharma', flow: 1800000000 * scale, avg_del: 42.8 },
      { sector: 'FMCG', flow: 1200000000 * scale, avg_del: 71.5 },
      { sector: 'Energy', flow: 950000000 * scale, avg_del: 35.2 },
      { sector: 'Metals', flow: -400000000 * scale, avg_del: 22.1 }, // Outflow simulation
    ]);
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
          <Map size={20} className="text-cyan-400" />
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
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Sector Metric</label>
            <div className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 py-2 text-sm text-[#888] font-mono select-none">
              Net Volume-Weighted Delivery Accumulation
            </div>
          </div>
          
          <div className="w-40">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Start Date</label>
            <div className="relative">
              <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-cyan-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <div className="w-40">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">End Date</label>
            <div className="relative">
              <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={endDate}
                onChange={e => setEndDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-cyan-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <button 
            onClick={fetchSectorFlows}
            disabled={loading}
            className="px-6 py-2 bg-cyan-600 hover:bg-cyan-700 text-white font-medium rounded text-sm transition-colors disabled:opacity-50 h-[38px] flex items-center justify-center min-w-[120px] gap-2"
          >
            {loading ? <Search size={16} className="animate-pulse" /> : <Activity size={16} />}
            {loading ? 'Crunching...' : 'Analyze Flow'}
          </button>
        </div>

        <div className="bg-[#0e1117] border border-[#ffffff0a] p-4 rounded-lg flex items-center gap-3">
          <TrendingUp className="text-cyan-400" size={24} />
          <div>
             <h4 className="font-semibold text-sm">Net Capital Flow (Custom Range)</h4>
             <p className="text-xs text-[#888] mt-1 pr-6">Measures total capital absorbed by institutional delivery across correlated sectors. Darker bars indicate higher underlying delivery percentages over the selected period.</p>
          </div>
        </div>

        <div className="flex-1 bg-[#1a1c24] rounded-lg border border-[#ffffff0a] p-4 min-h-[400px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 50 }}>
              <XAxis 
                dataKey="sector" 
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
