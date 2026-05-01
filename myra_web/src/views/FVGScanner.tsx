import { Librarian } from '../lib/Librarian';
import { useState, useEffect } from 'react';
import { Copy, Check, RefreshCw } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, ReferenceLine } from 'recharts';

const mockChartData: Record<string, any> = {
  'NQ': {
    prices: [
      { time: '09:30', price: 18400 },
      { time: '09:45', price: 18450 },
      { time: '10:00', price: 18600 },
      { time: '10:15', price: 18520 },
      { time: '10:30', price: 18480 },
      { time: '10:45', price: 18550 },
      { time: '11:00', price: 18650 },
    ],
    domain: [18350, 18700],
    fvgTop: 18520,
    fvgBottom: 18450,
    entry: 18480,
    sl: 18430,
    exit: 18620,
    type: 'Bullish'
  },
  'ES': {
    prices: [
      { time: '09:30', price: 5250 },
      { time: '09:45', price: 5240 },
      { time: '10:00', price: 5180 },
      { time: '10:15', price: 5210 },
      { time: '10:30', price: 5230 },
      { time: '10:45', price: 5190 },
      { time: '11:00', price: 5150 },
    ],
    domain: [5130, 5270],
    fvgTop: 5240,
    fvgBottom: 5210,
    entry: 5225,
    sl: 5255,
    exit: 5160,
    type: 'Bearish'
  },
  'BTC': {
    prices: [
      { time: '04:00', price: 68000 },
      { time: '08:00', price: 68500 },
      { time: '12:00', price: 71000 },
      { time: '16:00', price: 69500 },
      { time: '20:00', price: 69000 },
      { time: '00:00', price: 70500 },
      { time: '04:00', price: 71500 },
    ],
    domain: [67000, 72000],
    fvgTop: 69500,
    fvgBottom: 68500,
    entry: 69000,
    sl: 68200,
    exit: 71200,
    type: 'Bullish'
  }
};

export default function FVGScannerView({ lib }: { lib: Librarian }) {
  const [copied, setCopied] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(!lib.isConnectedToLocalRepo);
  const [fvgData, setFvgData] = useState<any[]>([]);

  // Concurrency Guard: Load only once on mount, no intervals.
  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setIsRefreshing(true);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      // Check connection
      await lib.executeQuery('_tech_conn', 'SELECT 1');
      
      // Fetch last 10 days of technical data for all symbols
      const query = `
        SELECT date, symbol, open, high, low, close, volume 
        FROM technical_data 
        WHERE date >= date('now', '-10 days')
        ORDER BY symbol ASC, date ASC
      `;
      const result = await lib.executeQuery('_tech_conn', query, {}, 10000);
      
      if (result && result.length > 0) {
        // Calculate FVGs using JavaScript (equivalent to pandas shift)
        const fvgResults: any[] = [];
        const groupedBySymbol = result.reduce((acc: any, row: any) => {
          if (!acc[row.symbol]) acc[row.symbol] = [];
          acc[row.symbol].push(row);
          return acc;
        }, {});
        
        for (const symbol in groupedBySymbol) {
          const symbolData = groupedBySymbol[symbol];
          
          for (let i = 2; i < symbolData.length; i++) {
            const current = symbolData[i];
            const prev2 = symbolData[i - 2];
            
            // Bullish FVG: current low > prev2 high
            if (current.low > prev2.high) {
              fvgResults.push({
                symbol: symbol,
                date: current.date,
                type: 'Bull Gap',
                gapTop: current.low,
                gapBottom: prev2.high,
                timeframe: '1D'
              });
            }
            // Bearish FVG: current high < prev2 low
            else if (current.high < prev2.low) {
              fvgResults.push({
                symbol: symbol,
                date: current.date,
                type: 'Bear Gap',
                gapTop: prev2.low,
                gapBottom: current.high,
                timeframe: '1D'
              });
            }
          }
        }
        
        // Sort by date descending (most recent first)
        fvgResults.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
        setFvgData(fvgResults.slice(0, 20)); // Show top 20 recent FVGs
      } else {
        setFvgData([]);
      }
      
      setIsDemo(false);
    } catch (e) {
      console.error('FVG Scanner error:', e);
      setIsDemo(true);
      setFvgData([]);
    }
    
    setDataLoaded(true);
    setLastRefreshed(new Date());
    setIsRefreshing(false);
  };

  const handleCopy = () => {
    const dataString = "Timeframe\tAsset\tFVG_Type\n15m\tNQ\tBullish\n1H\tES\tBearish\n4H\tBTC\tBullish";
    navigator.clipboard.writeText(dataString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const chartInfo = selectedAsset ? mockChartData[selectedAsset] : null;

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

      <div className="p-4 space-y-4">
        
        <div className="flex justify-between items-center gap-4 bg-[#0e1117] border border-[#ffffff0a] p-3 rounded">
          <div className="text-[#88d] font-mono text-[11px]">
            {isDemo ? '⚠️ Demo Mode - Connect to local repository' : `Found ${fvgData.length} recent FVGs`}
          </div>
          <button 
            onClick={fetchData}
            disabled={isRefreshing}
            className="flex items-center gap-2 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#fafafa] transition-colors disabled:opacity-50"
            title="Concurrency Guard: Manual Refresh Only"
          >
            <RefreshCw size={12} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Querying..." : "Refresh"}
          </button>
        </div>

        {lastRefreshed && (
          <div className="text-[10px] text-[#666] font-mono mb-2">
            Last update: {lastRefreshed.toLocaleTimeString()}
          </div>
        )}

        <div className={`overflow-x-auto relative group transition-opacity duration-300 ${isRefreshing ? 'opacity-50' : 'opacity-100'}`}>
          <button 
            onClick={handleCopy}
            className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors z-10"
            title="Copy Table Data"
          >
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
          </button>
          
          <table className="w-full text-left font-mono text-xs cursor-default">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff1a]">
                <th className="pb-2 px-2 font-medium uppercase">Timeframe</th>
                <th className="pb-2 px-2 font-medium uppercase">Asset</th>
                <th className="pb-2 px-2 font-medium uppercase">Date Formed</th>
                <th className="pb-2 px-2 font-medium uppercase">FVG Type</th>
                <th className="pb-2 px-2 font-medium uppercase">Gap Top</th>
                <th className="pb-2 px-2 font-medium uppercase">Gap Bottom</th>
              </tr>
            </thead>
            <tbody className="text-[#ccc]">
              {dataLoaded && fvgData.length > 0 && (
                fvgData.map((fvg, idx) => (
                  <tr 
                    key={idx}
                    onClick={() => setSelectedAsset(fvg.symbol)}
                    className={`border-b border-[#ffffff0a] hover:bg-[#ffffff10] transition-colors cursor-pointer ${selectedAsset === fvg.symbol ? 'bg-[#ffffff0a]' : ''}`}
                  >
                    <td className="py-2 px-2">{fvg.timeframe}</td>
                    <td className="py-2 px-2 text-[#fafafa] font-bold">{fvg.symbol}</td>
                    <td className="py-2 px-2">{fvg.date}</td>
                    <td className={`py-2 px-2 ${fvg.type === 'Bull Gap' ? 'text-green-400' : 'text-red-400'}`}>{fvg.type}</td>
                    <td className="py-2 px-2">{fvg.gapTop.toFixed(2)}</td>
                    <td className="py-2 px-2">{fvg.gapBottom.toFixed(2)}</td>
                  </tr>
                ))
              )}
              {dataLoaded && fvgData.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-[#666]">
                    {isDemo ? 'Connect to local repository to see real data' : 'No recent FVGs found.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {!dataLoaded && !isRefreshing && (
            <div className="w-full py-8 text-center text-[#666] text-xs font-mono">No data loaded.</div>
          )}
        </div>

        {/* Dynamic Chart Area */}
        <div className="h-64 border border-[#ffffff1a] bg-[#ffffff05] rounded flex items-center justify-center text-[#666] font-mono mt-4 relative overflow-hidden">
          {!selectedAsset ? (
            <span className="text-xs">Click a ticker to render FVG chart</span>
          ) : (
            <div className="w-full h-full p-2 flex flex-col">
              <div className="flex justify-between items-center px-4 pt-2">
                <span className="text-white text-xs font-bold font-sans">{selectedAsset} (Active FVG)</span>
                <span className="text-[10px] uppercase tracking-wider text-[#888]">
                  Entry: {chartInfo.entry} | SL: {chartInfo.sl} | Exit: {chartInfo.exit}
                </span>
              </div>
              <div className="flex-1 w-full min-h-0 mt-2">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartInfo.prices} margin={{ top: 5, right: 20, left: -20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#ffffff1a" vertical={false} />
                    <XAxis dataKey="time" stroke="#888" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis domain={chartInfo.domain} stroke="#888" fontSize={10} tickLine={false} axisLine={false} tickFormatter={(val) => val.toLocaleString()} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#0e1117', border: '1px solid #ffffff1a', fontSize: '10px' }}
                      itemStyle={{ color: '#fff' }}
                    />
                    
                    {/* FVG Area (Highlighting the Gap) */}
                    <ReferenceArea 
                      y1={chartInfo.fvgBottom} 
                      y2={chartInfo.fvgTop} 
                      style={{
                        fill: chartInfo.type === 'Bullish' ? '#22c55e' : '#ef4444',
                        fillOpacity: 0.15
                      }}
                    />
                    
                    {/* Metrics Lines */}
                    <ReferenceLine y={chartInfo.entry} stroke="#3b82f6" strokeDasharray="3 3" label={{ position: 'insideTopLeft', value: 'ENTRY', fill: '#3b82f6', fontSize: 9 }} />
                    <ReferenceLine y={chartInfo.exit} stroke="#a855f7" strokeDasharray="3 3" label={{ position: 'insideTopLeft', value: 'TARGET', fill: '#a855f7', fontSize: 9 }} />
                    <ReferenceLine y={chartInfo.sl} stroke="#ef4444" strokeDasharray="3 3" label={{ position: 'insideBottomLeft', value: 'STOP', fill: '#ef4444', fontSize: 9 }} />
                    
                    {/* Price Line */}
                    <Line type="monotone" dataKey="price" stroke="#fafafa" strokeWidth={2} dot={{ r: 3, fill: '#1a1c24', stroke: '#fafafa' }} activeDot={{ r: 5 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
