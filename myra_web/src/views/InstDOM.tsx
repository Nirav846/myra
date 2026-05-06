import { useState, useEffect } from 'react';
import { Librarian } from '../lib/Librarian';
import { Layers, AlignRight, Activity } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

export default function InstDOMView({ lib }: { lib: Librarian }) {
  const [data, setData] = useState<any[]>([]);
  const [ticker, setTicker] = useState('NIFTY');
  const [lookbackDays, setLookbackDays] = useState(30);
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchProfile();
  }, [ticker, lookbackDays]);

  const fetchProfile = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    try {
      // In production, this groups Volume by Price Bins using SQL ROUND() or width_bucket() logic
      const query = `
        SELECT ROUND(close, -1) as price_level, SUM(volume) as total_vol, SUM(delivery_qty) as total_del 
        FROM technical_data 
        WHERE symbol = '${ticker}' AND date >= date('now', '-${lookbackDays} days')
        GROUP BY price_level 
        ORDER BY price_level DESC
      `;
      const result = await lib.executeQuery('_inst_conn', query, {}, 10000); // 10s timeout
      
      if (result && result.length > 0) {
        setData(result.map((r: any) => ({
          price: r.price_level,
          delivery: Number(r.total_del || 0),
          intraday: Number(r.total_vol || 0) - Number(r.total_del || 0)
        })));
      } else {
        setIsDemo(true);
        generateMockProfile();
      }
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || "Query failed. Local sidecar may be offline.");
      setIsDemo(true);
      generateMockProfile();
    } finally {
      setIsLoading(false);
    }
  };

  const generateMockProfile = () => {
    const mock = [];
    const basePrice = ticker === 'NIFTY' ? 22000 : 2400;
    
    // Create a bell-curve-like volume profile simulating a Point of Control
    for(let i=15; i>=-15; i--) {
       const price = basePrice + (i * 10);
       // Gaussian approximation weight
       const weight = Math.exp(-(i*i)/100); 
       
       const totalVol = Math.floor(Math.random() * 200000 + (1000000 * weight));
       // If price is near POC (i=0 to 2), Institutional Delivery spikes aggressively
       const delivery_pct = (i >= -2 && i <= 2) ? 0.65 : (Math.random() * 0.2 + 0.1); 
       const delivery = Math.floor(totalVol * delivery_pct);
       
       mock.push({ 
         price: price.toString(), 
         delivery, 
         intraday: totalVol - delivery 
       });
    }
    setData(mock);
  };

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <AlignRight size={20} className="text-orange-400" />
          Institutional DOM (Volume Profile)
        </h3>
        <span className="text-xs text-[#888] font-mono">Module: _inst_conn.profile</span>
      </div>

      <div className="p-6 flex flex-col gap-6">
        {/* Controls */}
        <div className="flex flex-wrap gap-4 items-center bg-[#0e1117] p-3 rounded-lg border border-[#ffffff0a]">
          <div className="flex items-center gap-3">
            <label className="text-xs font-mono text-[#888] uppercase">Target Ticker</label>
            <input 
              type="text" 
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              className="w-32 bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa] uppercase"
            />
          </div>
          
          <div className="flex items-center gap-3 border-l border-[#ffffff1a] pl-4">
            <label className="text-xs font-mono text-[#888] uppercase">Lookback Range</label>
            <select 
              value={lookbackDays} 
              onChange={e => setLookbackDays(Number(e.target.value))}
              className="w-32 bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa]"
            >
              <option className="bg-[#1a1c24] text-[#fafafa]" value={7}>Last 7 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={30}>Last 30 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={90}>Last 90 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={180}>Last 180 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={365}>1 Year / YTD</option>
            </select>
          </div>
          
          <div className="ml-auto flex items-center gap-3">
             {errorMsg && <span className="text-[10px] text-red-400 font-mono px-2 py-1 bg-red-500/10 rounded border border-red-500/20">{errorMsg}</span>}
             {isDemo && <span className="text-[10px] text-yellow-500 font-mono px-2 py-1 bg-yellow-500/10 rounded border border-yellow-500/20 px-2">⚠️ DEMO DATA</span>}
             {isLoading && <span className="text-[10px] text-blue-400 font-mono animate-pulse">Calculating DOM...</span>}
            <span className="text-[10px] text-fuchsia-400 font-mono hidden md:flex items-center gap-2">
              <Activity size={12}/> Detects Hidden Liquidity
            </span>
          </div>
        </div>

        {/* DOM Profile Chart */}
        <div className="flex-1 bg-[#0e1117] rounded-lg border border-[#ffffff0a] p-4 min-h-[400px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#222" horizontal={false} />
              <XAxis type="number" stroke="#666" tick={{ fill: '#666', fontSize: 10 }} />
              <YAxis 
                type="category" 
                dataKey="price" 
                stroke="#ccc" 
                tick={{ fill: '#ccc', fontSize: 11, fontWeight: 'bold' }} 
                width={80} 
              />
              <Tooltip 
                cursor={{ fill: '#ffffff0a' }}
                contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', borderRadius: '4px', fontSize: '12px' }}
                formatter={(value: number) => value.toLocaleString()}
              />
              <Bar dataKey="delivery" stackId="a" fill="#10b981" name="Institutional Delivery" />
              <Bar dataKey="intraday" stackId="a" fill="#333333" name="Intraday / Speculation" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
