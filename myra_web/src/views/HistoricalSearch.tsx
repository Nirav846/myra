import { useState, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Search, Calendar, Activity, BarChart2, Table as TableIcon, Package, TrendingUp, BarChart } from 'lucide-react';
import { ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { SymbolSearch } from '../components/SymbolSearch';

export default function HistoricalSearchView({ lib }: { lib: Librarian }) {
  const [ticker, setTicker] = useState('RELIANCE');
  const [startDate, setStartDate] = useState(new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]); // 30 days ago
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<any[] | null>(null);
  const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  const handleSearch = async () => {
    if (!ticker) return;
    setLoading(true);
    setErrorMsg(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      const query = `SELECT date, open, high, low, close, volume, delivery_qty, delivery_pct FROM technical_data WHERE symbol = '${ticker}' AND date >= '${startDate}' AND date <= '${endDate}' ORDER BY date ASC`;
      const result = await lib.executeQuery('_tech_conn', query);

      if (result && result.length > 0) {
        const mapped = result.map((r: any) => ({
            date: r.date || r.Date || '',
            open: Number(r.open || r.Open || 0),
            high: Number(r.high || r.High || 0),
            low: Number(r.low || r.Low || 0),
            close: Number(r.close || r.Close || 0),
            volume: Number(r.volume || r.Volume || 0),
            delivery_qty: Number(r.delivery_qty || r.DeliveryQty || r.delivery || 0),
            delivery_pct: Number(r.delivery_pct || r.DeliveryPct || r.del_pct || 0),
            non_delivery: Number(r.volume || r.Volume || 0) - Number(r.delivery_qty || r.DeliveryQty || r.delivery || 0)
        }));
        setData(mapped);
      } else {
        setIsDemo(true);
        setErrorMsg('No data found for the given criteria.');
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
    let currentPrice = 2400;
    const mock = [];
    const start = new Date(startDate);
    const end = new Date(endDate);
    
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      if (d.getDay() === 0 || d.getDay() === 6) continue; // Skip weekends
      
      const volatility = currentPrice * 0.02;
      const open = currentPrice + (Math.random() - 0.5) * volatility;
      const close = open + (Math.random() - 0.5) * volatility;
      const high = Math.max(open, close) + Math.random() * volatility * 0.5;
      const low = Math.min(open, close) - Math.random() * volatility * 0.5;
      
      const volume = Math.floor(Math.random() * 5000000) + 1000000;
      // High variability in delivery for testing visualizations
      const delivery_pct = Number((Math.random() * 60 + 15).toFixed(2)); 
      const delivery_qty = Math.floor(volume * (delivery_pct / 100));
      const non_delivery = volume - delivery_qty;
      
      mock.push({
        date: d.toISOString().split('T')[0],
        close: Number(close.toFixed(2)),
        open: Number(open.toFixed(2)),
        high: Number(high.toFixed(2)),
        low: Number(low.toFixed(2)),
        volume,
        delivery_qty,
        delivery_pct,
        non_delivery
      });
      currentPrice = close;
    }
    setData(mock);
  };

  const summaryStats = useMemo(() => {
    if (!data || data.length === 0) return null;
    const totalVol = data.reduce((acc, curr) => acc + curr.volume, 0);
    const totalDel = data.reduce((acc, curr) => acc + curr.delivery_qty, 0);
    const avgDelPct = totalVol > 0 ? ((totalDel / totalVol) * 100).toFixed(2) : '0.00';
    const highestDel = [...data].sort((a,b) => b.delivery_pct - a.delivery_pct)[0];
    const avgDailyVol = Math.floor(totalVol / data.length);
    
    return { totalVol, totalDel, avgDelPct, highestDel, avgDailyVol };
  }, [data]);

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Activity size={20} className="text-blue-400" />
          Historical Engine & Delivery Scrutiny
        </h3>
        <div className="flex items-center gap-3">
          {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
          {isDemo && (
            <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
              ⚠️ SIMULATED DATA
            </span>
          )}
          <span className="text-xs text-[#888] font-mono">Module: query_layer.delivery</span>
        </div>
      </div>

      <div className="p-6 flex flex-col gap-6">
        {/* Search Controls */}
        <div className="flex flex-wrap gap-4 items-end bg-[#0e1117] p-4 rounded-lg border border-[#ffffff0a]">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Ticker Symbol</label>
            <SymbolSearch 
              lib={lib}
              initialValue={ticker}
              onSymbolSelect={setTicker}
              placeholder="e.g. RELIANCE"
              className="mt-1"
            />
          </div>
          
          <div className="w-40">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Start Date</label>
            <div className="relative">
              <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa] [color-scheme:dark]"
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
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <button 
            onClick={handleSearch}
            disabled={loading}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded text-sm transition-colors disabled:opacity-50 h-[38px] flex items-center justify-center min-w-[120px]"
          >
            {loading ? <Search size={16} className="animate-pulse" /> : 'Fetch Series'}
          </button>
        </div>

        {/* Dashboard Highlight Cards */}
        {data && summaryStats && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#1a1c24] border border-green-500/30 rounded p-4 flex flex-col justify-center relative overflow-hidden group">
              <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><Package size={100} /></div>
              <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><Package size={12}/> AGGREGATE DELIVERY %</span>
              <span className="text-2xl font-bold text-green-400">{summaryStats.avgDelPct}%</span>
              <span className="text-[10px] text-[#666] mt-1 font-mono">Total Qty: {summaryStats.totalDel.toLocaleString()}</span>
            </div>
            <div className="bg-[#1a1c24] border border-fuchsia-500/30 rounded p-4 flex flex-col justify-center relative overflow-hidden group">
              <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><TrendingUp size={100} /></div>
              <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><TrendingUp size={12}/> HIGH DELIVERY SPIKE</span>
              <span className="text-2xl font-bold text-fuchsia-400">{summaryStats.highestDel.delivery_pct}%</span>
              <span className="text-[10px] text-[#666] mt-1 font-mono">Recorded on: {summaryStats.highestDel.date}</span>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center relative overflow-hidden group">
              <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><BarChart size={100} /></div>
              <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><Activity size={12}/> AVERAGE DAILY VOLUME</span>
              <span className="text-2xl font-bold text-[#fafafa]">{summaryStats.avgDailyVol.toLocaleString()}</span>
              <span className="text-[10px] text-[#666] mt-1 font-mono">Across {data.length} trading days</span>
            </div>
          </div>
        )}

        {/* View Toggle & Content */}
        {data && (
          <div className="flex-1 flex flex-col border border-[#ffffff0a] rounded-lg overflow-hidden bg-[#0e1117]">
            <div className="flex bg-[#1a1c24] border-b border-[#ffffff0a] p-2 gap-2">
              <button 
                onClick={() => setViewMode('chart')}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === 'chart' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#ccc]'}`}
              >
                <BarChart2 size={14} /> OHLCV + Delivery Tracking
              </button>
              <button 
                onClick={() => setViewMode('table')}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === 'table' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#ccc]'}`}
              >
                <TableIcon size={14} /> Raw Vector Grid
              </button>
            </div>

            <div className="p-4 flex-1 min-h-[400px]">
              {viewMode === 'chart' ? (
                <div className="h-full w-full relative">
                  <ResponsiveContainer width="100%" height={400}>
                    <ComposedChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#222" vertical={false} />
                      <XAxis dataKey="date" stroke="#666" tick={{ fill: '#666', fontSize: 10 }} tickMargin={10} minTickGap={30} />
                      
                      {/* Left axis for price */}
                      <YAxis yAxisId="price" domain={['auto', 'auto']} stroke="#666" tick={{ fill: '#666', fontSize: 10 }} />
                      
                      {/* Hidden right axes for volume scale and percentage scale */}
                      <YAxis yAxisId="volume" orientation="right" hide={true} />
                      <YAxis yAxisId="pct" orientation="right" hide={true} domain={[0, 100]} />
                      
                      <Tooltip 
                        contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', borderRadius: '4px', fontSize: '12px' }}
                        itemStyle={{ color: '#ccc' }}
                        formatter={(value: any, name: string) => {
                          if (name === "Delivery %") return [`${value}%`, name];
                          if (name === "Delivery Qty" || name === "Intraday Qty") return [value.toLocaleString(), name];
                          return [value, name];
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px', color: '#888' }} />
                      
                      {/* Stacked Bars for Volume Decomposition */}
                      <Bar yAxisId="volume" dataKey="delivery_qty" stackId="vol" fill="#10b981" opacity={0.8} name="Delivery Qty" />
                      <Bar yAxisId="volume" dataKey="non_delivery" stackId="vol" fill="#333" opacity={0.6} name="Intraday Qty" />
                      
                      {/* Line for Price */}
                      <Line yAxisId="price" type="monotone" dataKey="close" stroke="#3b82f6" strokeWidth={2} dot={false} name="Closing Price (₹)" />
                      
                      {/* Delivery Percentage Tracking Line */}
                      <Line yAxisId="pct" type="monotone" dataKey="delivery_pct" stroke="#f43f5e" strokeWidth={1} dot={false} strokeDasharray="4 4" name="Delivery %" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="overflow-auto max-h-[400px] relative">
                  <table className="w-full text-left font-mono text-xs">
                    <thead className="sticky top-0 bg-[#0e1117] z-10">
                      <tr className="text-[#888] border-b border-[#ffffff1a]">
                        <th className="pb-2 px-2 font-medium uppercase">Date</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right">Close</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right">Volume</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right text-green-400">Delivery QTY</th>
                        <th className="pb-2 px-4 font-medium uppercase text-right text-fuchsia-400">Del (%)</th>
                      </tr>
                    </thead>
                    <tbody className="text-[#ccc]">
                      {data.map((row, idx) => (
                        <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                          <td className="py-2 px-2 text-[#888]">{row.date}</td>
                          <td className="py-2 px-2 text-right">{row.close.toFixed(2)}</td>
                          <td className="py-2 px-2 text-right text-[#666]">{row.volume.toLocaleString()}</td>
                          <td className="py-2 px-2 text-right text-green-400 font-medium">{row.delivery_qty.toLocaleString()}</td>
                          <td className="py-2 px-4 text-right">
                             <span className={`px-2 py-0.5 rounded ${row.delivery_pct > 50 ? 'bg-fuchsia-500/20 text-fuchsia-400 font-bold' : row.delivery_pct < 30 ? 'bg-red-500/10 text-red-500' : 'text-[#888]'}`}>
                                {row.delivery_pct}%
                             </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {!data && !loading && (
          <div className="flex-1 border border-[#ffffff0a] rounded flex items-center justify-center flex-col text-[#666] bg-[#0e1117] min-h-[300px]">
            <Search size={32} className="mb-3 opacity-20" />
            <p className="text-sm font-mono">Enter a ticker and date range to query the technical sidecar.</p>
          </div>
        )}
      </div>
    </div>
  );
}
