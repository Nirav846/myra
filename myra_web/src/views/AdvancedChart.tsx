import { useState, useEffect, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import Plot from 'react-plotly.js';
import { Search, Plus, X, BarChart2, PanelLeftClose, Settings2, Info } from 'lucide-react';
import { SymbolSearch } from '../components/SymbolSearch';

// Math Helpers
const calculateSMA = (data: any[], period: number) => {
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else {
      let sum = 0;
      for (let j = 0; j < period; j++) {
        sum += data[i - j].close;
      }
      result.push(sum / period);
    }
  }
  return result;
};

const calculateRSI = (data: any[], period: number) => {
  const result: number[] = [];
  let gains = 0, losses = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const diff = data[i].close - data[i - 1].close;
    if (i < period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      result.push(NaN);
    } else if (i === period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      const rs = (gains/period) / (losses/period === 0 ? 1 : losses/period);
      result.push(100 - (100 / (1 + rs)));
    } else {
      const gain = diff >= 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      gains = (gains * (period - 1) + gain) / period;
      losses = (losses * (period - 1) + loss) / period;
      const rs = gains / (losses === 0 ? 1 : losses);
      result.push(100 - (100 / (1 + rs)));
    }
  }
  return result;
};

const calculateATR = (data: any[], period: number) => {
  const result: number[] = [];
  let trSum = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const high = data[i].high;
    const low = data[i].low;
    const prevClose = data[i-1].close;
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    
    if (i < period) {
      trSum += tr;
      result.push(NaN);
    } else if (i === period) {
      trSum += tr;
      result.push(trSum / period);
    } else {
      const prevAtr = result[i - 1];
      result.push((prevAtr * (period - 1) + tr) / period);
    }
  }
  return result;
};

const usePersistedState = <T,>(key: string, initialValue: T): [T, (val: T) => void] => {
  const [state, setState] = useState<T>(() => {
    const saved = localStorage.getItem(key);
    if (saved !== null) {
      try {
        return JSON.parse(saved);
      } catch (e) {
        return initialValue;
      }
    }
    return initialValue;
  });

  const setValue = (value: T) => {
    setState(value);
    localStorage.setItem(key, JSON.stringify(value));
  };

  return [state, setValue];
};

export default function AdvancedChartView({ lib }: { lib: Librarian }) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [symbols, setSymbols] = useState<string[]>(['RELIANCE']);
  const [searchInput, setSearchInput] = useState('');
  const [range, setRange] = useState('1M');
  
  const [dataCache, setDataCache] = useState<Record<string, any[]>>({});
  
  // Overlays
  const [showSma20, setShowSma20] = usePersistedState('chart-showSma20', false);
  const [showSma50, setShowSma50] = usePersistedState('chart-showSma50', false);
  const [showSma150, setShowSma150] = usePersistedState('chart-showSma150', false);
  const [showSma200, setShowSma200] = usePersistedState('chart-showSma200', false);
  const [showFvg, setShowFvg] = usePersistedState('chart-showFvg', true);
  const [showFibonacci, setShowFibonacci] = usePersistedState('chart-showFibonacci', false);

  const [showVwap, setShowVwap] = usePersistedState('chart-showVwap', true);
  const [showVolume, setShowVolume] = usePersistedState('chart-showVolume', true);
  const [showDelivery, setShowDelivery] = usePersistedState('chart-showDelivery', false);
  const [showDeliveryProfile, setShowDeliveryProfile] = usePersistedState('chart-showDeliveryProfile', false);
  const [showDeliverySR, setShowDeliverySR] = usePersistedState('chart-showDeliverySR', false);
  const [showSmartMoney, setShowSmartMoney] = usePersistedState('chart-showSmartMoney', false);

  const [showDelAD, setShowDelAD] = usePersistedState('chart-showDelAD', false);
  const [showDelVwapBands, setShowDelVwapBands] = usePersistedState('chart-showDelVwapBands', false);
  const [showLiqVoids, setShowLiqVoids] = usePersistedState('chart-showLiqVoids', false);
  const [showInstBlocks, setShowInstBlocks] = usePersistedState('chart-showInstBlocks', false);
  
  const [showRsi, setShowRsi] = usePersistedState('chart-showRsi', true);
  
  // Custom indicators on Price
  const [showNiftyOut, setShowNiftyOut] = usePersistedState('chart-showNiftyOut', false);
  const [showLogScale, setShowLogScale] = usePersistedState('chart-showLogScale', false);

  const [showSwings, setShowSwings] = usePersistedState('chart-showSwings', true);

  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    const start = new Date();
    if (range === '1M') start.setMonth(start.getMonth() - 1);
    else if (range === '3M') start.setMonth(start.getMonth() - 3);
    else if (range === '6M') start.setMonth(start.getMonth() - 6);
    else if (range === '1Y') start.setFullYear(start.getFullYear() - 1);
    else start.setFullYear(start.getFullYear() - 10);
    return { startDate: start.toISOString().split('T')[0], endDate: end.toISOString().split('T')[0] };
  }, [range]);

  const fetchSymbolData = async (symbol: string) => {
    try {
      const query = `SELECT *, COALESCE(delivery, delivery_qty) as delivery_final, COALESCE(volume, trades) as volume_final FROM technical_data WHERE symbol = '${symbol}' AND date >= '${startDate}' AND date <= '${endDate}' ORDER BY date ASC`;
      const result = await lib.executeQuery('_tech_conn', query, {}, 10000);
      if (result && result.length > 0) {
          setDataCache(prev => ({...prev, [symbol]: result}));
      } else {
          setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol)}));
      }
    } catch (e) {
      console.error(e);
      setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol)}));
    }
  };

  const generateMockData = (sym: string) => {
    const mock = [];
    let currentPrice = Math.random() * 1000 + 100;
    const start = new Date(startDate);
    const end = new Date(endDate);
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
        if (d.getDay() === 0 || d.getDay() === 6) continue;
        const volatility = currentPrice * 0.02;
        const open = currentPrice + (Math.random() - 0.5) * volatility;
        const close = open + (Math.random() - 0.5) * volatility;
        const high = Math.max(open, close) + Math.random() * volatility * 0.5;
        const low = Math.min(open, close) - Math.random() * volatility * 0.5;
        const volume = Math.floor(Math.random() * 5000000) + 100000;
        
        mock.push({
            date: d.toISOString().split('T')[0],
            open, high, low, close, volume,
            vwap: close * (1 + (Math.random() - 0.5) * 0.01),
            delivery_final: volume * (Math.random() * 0.6 + 0.2), // Mock delivery_final
            delivery_pct: Math.random() * 100,
            delivery_ratio: Math.random() * 4,
            stock_return: (Math.random() - 0.5) * 2,
            volatility_compression_score: Math.random(),
            relative_volume_score: Math.random() * 2,
            nifty_outperformance_score: (Math.random() - 0.5),
            delivery_ma_60: volume * (Math.random() * 0.5 + 0.3),
            bullish_fvg: Math.random() > 0.95 ? 1 : 0,
            bearish_fvg: Math.random() > 0.95 ? 1 : 0,
            fvg_top: high + volatility,
            fvg_bottom: low - volatility,
            fvg_freshness: Math.random(),
            fvg_boundary: Math.random() > 0.9 ? low - volatility : null,
            swing_high: Math.random() > 0.9 ? 1 : null,
            swing_low: Math.random() > 0.9 ? 1 : null,
            trend_alignment: Math.floor(Math.random() * 5) - 2
        });
        currentPrice = close;
    }
    return mock;
  };

  // Re-fetch when range changes or symbols added
  useEffect(() => {
    symbols.forEach(sym => {
       fetchSymbolData(sym);
    });
  }, [symbols, range]); // Depends on range so changing range refetches all

  const addSymbol = () => {
    const sym = searchInput.trim().toUpperCase();
    if (sym && !symbols.includes(sym) && symbols.length < 4) {
      setSymbols([...symbols, sym]);
      setSearchInput('');
    }
  };

  const removeSymbol = (sym: string) => {
    setSymbols(symbols.filter(s => s !== sym));
  };

  // Helper to safely format numbers
  const formatNumber = (value: any, digits: number = 2) => {
    const num = Number(value);
    return isNaN(num) ? 'N/A' : num.toFixed(digits);
  };

  return (
    <div className="flex bg-[#0e1117] min-h-[600px] border border-[#ffffff1a] rounded overflow-hidden relative">
      {/* Sidebar Controls */}
      <div className={`${sidebarOpen ? 'w-64 p-4 border-r' : 'w-0 p-0 border-r-0'} bg-[#1a1c24] border-[#ffffff1a] flex flex-col gap-6 shrink-0 transition-all duration-300 overflow-hidden`}>
         <div className="w-[224px]"> {/* Fixed width inner container */}
             <div className="flex justify-between items-center mb-3">
                 <h3 className="text-xs font-bold text-[#fafafa] uppercase tracking-widest flex items-center gap-2"><BarChart2 size={16} className="text-cyan-400"/> Compare</h3>
                 <button onClick={() => setSidebarOpen(false)} className="text-[#888] hover:text-white transition-colors" title="Collapse Panel">
                     <PanelLeftClose size={16} />
                 </button>
             </div>
             
             {/* Selected Symbols */}
             <div className="flex flex-wrap gap-2 mb-3">
                 {symbols.map(sym => (
                    <div key={sym} className="flex items-center gap-1 bg-cyan-500/20 text-cyan-300 text-xs px-2 py-1 rounded border border-cyan-500/30">
                       <span className="font-mono">{sym}</span>
                       <button onClick={() => removeSymbol(sym)} className="hover:text-white"><X size={12} /></button>
                    </div>
                 ))}
             </div>
             
             {/* Add Symbol Input */}
             {symbols.length < 4 && (
             <SymbolSearch 
                 lib={lib}
                 onSymbolSelect={(sym) => {
                     if (sym && !symbols.includes(sym)) {
                         setSymbols(prev => [...prev, sym]);
                     }
                 }}
                 placeholder="Add symbol..."
                 clearOnSelect={true}
             />
             )}
         </div>

         {/* View Range */}
         <div className="w-[224px]">
            <h4 className="text-[10px] font-mono text-[#888] uppercase mb-2">Time Horizon</h4>
            <div className="flex flex-wrap gap-1 bg-[#0e1117] p-1 rounded border border-[#ffffff1a]">
                {['1M', '3M', '6M', '1Y', 'All'].map(r => (
                   <button 
                     key={r} 
                     onClick={() => setRange(r)} 
                     className={`flex-1 px-1 py-1 text-[10px] rounded font-mono transition-colors ${range === r ? 'bg-cyan-500/20 text-cyan-400' : 'text-[#888] hover:bg-[#ffffff1a]'}`}
                   >{r}</button>
                ))}
            </div>
         </div>

         {/* Indicators Menu */}
         <div className="w-[224px]">
            <h4 className="text-[10px] font-mono text-[#888] uppercase mb-2">Overlays & Metrics</h4>
            <div className="space-y-2 h-[250px] overflow-y-auto pr-2 custom-scrollbar">
               {[
                 { id: 'sma20', label: 'SMA (20)', state: showSma20, set: setShowSma20 },
                 { id: 'sma50', label: 'SMA (50)', state: showSma50, set: setShowSma50 },
                 { id: 'sma150', label: 'SMA (150)', state: showSma150, set: setShowSma150 },
                 { id: 'sma200', label: 'SMA (200)', state: showSma200, set: setShowSma200 },
                 { id: 'vwap', label: 'VWAP', state: showVwap, set: setShowVwap },
                 { id: 'fvg', label: 'Fair Value Gaps', state: showFvg, set: setShowFvg },
                 { id: 'fibonacci', label: 'Auto Fibonacci', state: showFibonacci, set: setShowFibonacci },
                 { id: 'swings', label: 'Swing Points', state: showSwings, set: setShowSwings },
                 { id: 'nifty', label: 'Nifty Outperf.', state: showNiftyOut, set: setShowNiftyOut },
                 { id: 'logscale', label: 'Log Scale', state: showLogScale, set: setShowLogScale },
                 { id: 'volume', label: 'Volume Pane', state: showVolume, set: setShowVolume },
                 { id: 'delivery', label: 'Delivery Pane', state: showDelivery, set: setShowDelivery },
                 { id: 'del_profile', label: 'Delivery Profile', state: showDeliveryProfile, set: setShowDeliveryProfile, desc: 'Histogram of delivery volume by price level.' },
                 { id: 'del_sr', label: 'Delivery Auto S/R', state: showDeliverySR, set: setShowDeliverySR, desc: 'Auto-draws support/resistance at high delivery price levels.' },
                 { id: 'smart_money', label: 'Smart Money Prints', state: showSmartMoney, set: setShowSmartMoney, desc: 'Highlights bars with 1.5x average volume and > 60% delivery ratio.' },
                 { id: 'del_ad', label: 'Delivery A/D', state: showDelAD, set: setShowDelAD, desc: 'Accumulation/Distribution strictly using delivery volume.' },
                 { id: 'del_vwap_bands', label: 'Del. VWAP Bands', state: showDelVwapBands, set: setShowDelVwapBands, desc: 'VWAP weighted by delivery volume with 1.5 standard deviation bands.' },
                 { id: 'liq_voids', label: 'Liquidity Voids', state: showLiqVoids, set: setShowLiqVoids, desc: 'Shaded areas where large price movement occurred on low relative volume (potential gap fills).' },
                 { id: 'inst_blocks', label: 'Inst. Blocks', state: showInstBlocks, set: setShowInstBlocks, desc: 'Massive volume anomalies (> 3.5x average) paired with > 65% delivery.' },
                 { id: 'rsi', label: 'RSI Pane', state: showRsi, set: setShowRsi }
               ].map(toggle => (
                   <div key={toggle.id} className="group relative flex items-center gap-2">
                       <label className="flex items-center gap-2 cursor-pointer flex-1 group-hover:text-white">
                           <input type="checkbox" checked={toggle.state} onChange={e => toggle.set(e.target.checked)} className="accent-cyan-500 w-3 h-3" />
                           <span className="text-xs font-mono text-[#ccc] group-hover:text-white transition-colors">{toggle.label}</span>
                       </label>
                       {toggle.desc && (
                           <div className="relative cursor-help group/tooltip" title={toggle.desc}>
                               <Info size={12} className="text-[#666] hover:text-cyan-400 outline-none" />
                           </div>
                       )}
                   </div>
               ))}
            </div>
         </div>
      </div>

      {/* Main Charts Area */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6 relative">
          {!sidebarOpen && (
              <button 
                 onClick={() => setSidebarOpen(true)}
                 className="absolute top-4 left-0 z-10 bg-[#1a1c24] border border-[#ffffff1a] border-l-0 p-2 rounded-r text-[#888] hover:text-white transition-all shadow-lg hover:bg-[#2a2c34]"
                 title="Open Settings Panel"
              >
                 <Settings2 size={16} />
              </button>
          )}
          {symbols.map((sym, idx) => {
              const data = dataCache[sym];
              if (!data) return <div key={sym} className="h-[400px] flex items-center justify-center font-mono text-[#888] text-xs animate-pulse">Loading {sym}...</div>;
              
              const dates = data.map(d => d.date);
              const opens = data.map(d => d.open);
              const highs = data.map(d => d.high);
              const lows = data.map(d => d.low);
              const closes = data.map(d => d.close);
              const volumes = data.map(d => {
                  const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume);
                  return isNaN(vol) ? 0 : vol;
              });
              
              const vwap = data.map(d => d.vwap);
              const deliveryFinal = data.map(d => {
                  const delVal = d.delivery_final ? Number(d.delivery_final) : 0;
                  return isNaN(delVal) ? 0 : delVal;
              });
              const deliveryPct = data.map((d, i) => {
                  if (d.delivery_pct != null && !isNaN(d.delivery_pct)) return d.delivery_pct;
                  const delVal = deliveryFinal[i];
                  const vol = d.volume || 1;
                  return (delVal / vol) * 100;
              });
              const deliveryRatio = data.map(d => d.delivery_ratio);
              const stockReturn = data.map(d => d.stock_return);
              const volComp = data.map(d => d.volatility_compression_score);
              const relVol = data.map(d => d.relative_volume_score);
              const niftyOut = data.map(d => d.nifty_outperformance_score);
              const trendAlignment = data.map(d => d.trend_alignment);
              const volumeColors = data.map(d => d.close >= d.open ? '#22c55e' : '#ef4444');
              const deliveryColorsInverse = data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e');
              const deliveryMa60 = data.map(d => d.delivery_ma_60);

              // Compute SMAs and Indicators
              const sma20 = showSma20 ? calculateSMA(data, 20) : [];
              const sma50 = showSma50 ? calculateSMA(data, 50) : [];
              const sma150 = showSma150 ? calculateSMA(data, 150) : [];
              const sma200 = showSma200 ? calculateSMA(data, 200) : [];
              const rsiObj = showRsi ? calculateRSI(data, 14) : [];
              
              // Swings
              const swingHighsDates = showSwings ? data.filter(d => d.swing_high === 1).map(d => d.date) : [];
              const swingHighsValues = showSwings ? data.filter(d => d.swing_high === 1).map(d => d.high) : [];
              const swingLowsDates = showSwings ? data.filter(d => d.swing_low === 1).map(d => d.date) : [];
              const swingLowsValues = showSwings ? data.filter(d => d.swing_low === 1).map(d => d.low) : [];

              // Shapes for FVGs & Reference lines
              const shapes: any[] = [];
              if (showFibonacci && data.length > 0) {
                  const minLow = Math.min(...lows);
                  const maxHigh = Math.max(...highs);
                  const diff = maxHigh - minLow;
                  const levels = [
                      { ratio: 0, color: 'rgba(255, 255, 255, 0.5)' },
                      { ratio: 0.236, color: 'rgba(244, 63, 94, 0.5)' },
                      { ratio: 0.382, color: 'rgba(250, 204, 21, 0.5)' },
                      { ratio: 0.5, color: 'rgba(74, 222, 128, 0.5)' },
                      { ratio: 0.618, color: 'rgba(56, 189, 248, 0.5)' },
                      { ratio: 0.786, color: 'rgba(168, 85, 247, 0.5)' },
                      { ratio: 1, color: 'rgba(255, 255, 255, 0.5)' }
                  ];
                  levels.forEach(level => {
                      const y = maxHigh - (diff * level.ratio);
                      shapes.push({
                          type: 'line',
                          xref: 'paper', x0: 0, x1: 1,
                          yref: 'y', y0: y, y1: y,
                          line: { color: level.color, width: 1, dash: 'dot' },
                          // @ts-ignore
                          label: { text: `Fib ${(level.ratio * 100).toFixed(1)}% (${y.toFixed(2)})`, font: { size: 10, color: level.color }, textposition: 'start' }
                      });
                  });
              }

              if (showFvg) {
                  data.forEach((d, i) => {
                      const freshness = d.fvg_freshness ?? 1;
                      const opacity = (0.05 + (freshness * 0.15)).toFixed(2); // 15-20% opacity
                      
                      if (d.bullish_fvg === 1 && d.fvg_top && d.fvg_bottom) {
                          shapes.push({
                              type: 'rect',
                              layer: 'below',
                              xref: 'x', yref: 'y',
                              x0: d.date, x1: data[i+5]?.date || dates[dates.length-1], // Extrapolate
                              y0: d.fvg_bottom, y1: d.fvg_top,
                              fillcolor: `rgba(74, 222, 128, ${opacity})`, // pale green
                              line: { width: 0 }
                          });
                      }
                      if (d.bearish_fvg === 1 && d.fvg_top && d.fvg_bottom) {
                          shapes.push({
                              type: 'rect',
                              layer: 'below',
                              xref: 'x', yref: 'y',
                              x0: d.date, x1: data[i+5]?.date || dates[dates.length-1],
                              y0: d.fvg_bottom, y1: d.fvg_top,
                              fillcolor: `rgba(248, 113, 113, ${opacity})`, // pale red
                              line: { width: 0 }
                          });
                      }
                  });
              }

              if (showRsi) {
                  shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y3', y0: 70, y1: 70, line: { color: '#666', dash: 'dash', width: 1 } });
                  shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y3', y0: 30, y1: 30, line: { color: '#666', dash: 'dash', width: 1 } });
              }

              // Smart Money Footprint Points
              const smartMoneyX: any[] = [];
              const smartMoneyY: any[] = [];
              const smartMoneyText: any[] = [];

              if (showSmartMoney) {
                  // Calculate moving average of volume to find relative volume
                  const avgVol = calculateSMA(data.map(d => ({close: d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1})), 20);
                  data.forEach((d, i) => {
                       const vol = volumes[i];
                       const delPct = deliveryPct[i];
                       const avgV = avgVol[i] || 1;
                       
                       // Criteria: High relative volume (>2x 20MA) AND high delivery % (>60%)
                       if (vol > avgV * 1.5 && delPct > 60) {
                           smartMoneyX.push(d.date);
                           const isBullish = d.close >= d.open;
                           smartMoneyY.push(isBullish ? d.low * 0.98 : d.high * 1.02);
                           smartMoneyText.push(`Smart Money<br>Vol: ${(vol/1e6).toFixed(2)}M<br>Del: ${delPct.toFixed(1)}%`);
                       }
                  });
              }

              // Delivery Profile & SR
              const deliveryProfileY: number[] = [];
              const deliveryProfileX: number[] = [];
              const deliveryProfileColors: string[] = [];

              if (showDeliveryProfile || showDeliverySR) {
                  const minL = Math.min(...lows);
                  const maxH = Math.max(...highs);
                  if (maxH > minL) {
                      const bins = 60; 
                      const binSize = (maxH - minL) / bins;
                      const profile = new Array(bins).fill(0);
                      const buyProfile = new Array(bins).fill(0);
                      const sellProfile = new Array(bins).fill(0);

                      data.forEach((d, i) => {
                          const typicalPrice = (d.high + d.low + d.close) / 3;
                          let binIdx = Math.floor((typicalPrice - minL) / binSize);
                          if (binIdx >= bins) binIdx = bins - 1;
                          if (binIdx < 0) binIdx = 0;
                          
                          const delv = deliveryFinal[i];
                          profile[binIdx] += delv;
                          if (d.close >= d.open) buyProfile[binIdx] += delv;
                          else sellProfile[binIdx] += delv;
                      });

                      for (let i = 0; i < bins; i++) {
                          deliveryProfileY.push(minL + (i * binSize) + (binSize / 2));
                          deliveryProfileX.push(profile[i]);
                          deliveryProfileColors.push(buyProfile[i] >= sellProfile[i] ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)');
                      }

                      if (showDeliverySR) {
                          const maxProf = Math.max(...profile);
                          for (let i = 2; i < bins - 2; i++) {
                             if (profile[i] > profile[i-1] && profile[i] > profile[i-2] &&
                                 profile[i] > profile[i+1] && profile[i] > profile[i+2] && 
                                 profile[i] > maxProf * 0.3) {
                                 
                                 const y = minL + (i * binSize) + (binSize / 2);
                                 shapes.push({
                                     type: 'line',
                                     xref: 'paper', x0: 0, x1: 1,
                                     yref: 'y', y0: y, y1: y,
                                     line: { color: 'rgba(234, 179, 8, 0.6)', width: 2, dash: 'dot' },
                                     label: { text: `Del SR`, font: { size: 10, color: '#eab308' }, textposition: 'end' }
                                 });
                             }
                          }
                      }
                  }
              }

              // 4 Intelligent Indicators
              const delAdLine: number[] = [];
              let currentAd = 0;
              
              let vwapSum = 0;
              let volSum = 0;
              let squaredDevSum = 0;
              const delVwapBandsUpper: number[] = [];
              const delVwapBandsLower: number[] = [];
              const delVwapMid: number[] = [];
              
              const instBlocksX: string[] = [];
              const instBlocksY: number[] = [];
              const instBlocksText: string[] = [];
              
              // Only compute if we need to to save CPU
              if (showDelAD || showDelVwapBands || showLiqVoids || showInstBlocks) {
                  const atr14 = calculateSMA(data.map(d => ({close: Math.abs(d.high - d.low)})), 14);
                  const volData = data.map(d => ({ close: d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1 }));
                  const avgVol20 = calculateSMA(volData, 20);

                  data.forEach((d, i) => {
                      const vol = volumes[i];
                      const delPct = deliveryPct[i] || 0;
                      const delVol = vol * (delPct / 100);
                      
                      // 1. Delivery A/D
                      let multiplier = (d.high === d.low) ? 0 : ((d.close - d.low) - (d.high - d.close)) / (d.high - d.low);
                      currentAd += multiplier * delVol;
                      delAdLine.push(currentAd);
                      
                      // 2. Delivery VWAP Bands
                      const typPrice = (d.high + d.low + d.close) / 3;
                      if (showDelVwapBands) {
                          vwapSum += typPrice * delVol;
                          volSum += delVol;
                          const cvwap = volSum > 0 ? vwapSum / volSum : typPrice;
                          delVwapMid.push(cvwap);
                          
                          squaredDevSum += delVol * Math.pow(typPrice - cvwap, 2);
                          const stdDev = volSum > 0 ? Math.sqrt(squaredDevSum / volSum) : 0;
                          
                          delVwapBandsUpper.push(cvwap + (stdDev * 1.5)); // 1.5 StdDev
                          delVwapBandsLower.push(cvwap - (stdDev * 1.5));
                      }
                      
                      // 3. Liquidity Voids
                      if (showLiqVoids && i > 14) {
                          const bodySize = Math.abs(d.close - d.open);
                          const avgBody = atr14[i] || 1;
                          const avgV = avgVol20[i] || 1;
                          
                          // large body > 1.5x ATR but volume < average -> weak participation / void
                          if (bodySize > avgBody * 1.5 && vol < avgV * 0.9) {
                              shapes.push({
                                  type: 'rect',
                                  xref: 'x', x0: dates[i-1] || dates[i], x1: dates[i+1] || dates[i],
                                  yref: 'paper', y0: 0, y1: 1,
                                  fillcolor: 'rgba(236, 72, 153, 0.08)', // Soft pink vertical band
                                  line: { width: 0 },
                                  layer: 'below'
                              });
                          }
                      }
                      
                      // 4. Institutional Blocks
                      if (showInstBlocks && i > 20) {
                          const avgV = avgVol20[i] || 1;
                          // Volume > 3.5x average AND high delivery > 65% implies institutional block
                          if (vol > avgV * 3.5 && delPct > 65) {
                              instBlocksX.push(d.date);
                              const isBullish = d.close >= d.open;
                              instBlocksY.push(isBullish ? d.low * 0.99 : d.high * 1.01);
                              instBlocksText.push(`Inst. Block<br>Del Vol: ${(delVol/1e6).toFixed(2)}M`);
                          }
                      }
                  });
              }

              // Dynamic layout domains
              let currentY = 0;
              const gap = 0.04;
              const paneHeight = 0.16;
              
              const rsiDomain = showRsi ? [currentY, currentY + paneHeight] : [0, 0];
              if (showRsi) currentY += paneHeight + gap;
              
              const delAdDomain = showDelAD ? [currentY, currentY + paneHeight] : [0, 0];
              if (showDelAD) currentY += paneHeight + gap;
              
              const delDomain = showDelivery ? [currentY, currentY + paneHeight] : [0, 0];
              if (showDelivery) currentY += paneHeight + gap;
              
              const volDomain = showVolume ? [currentY, currentY + paneHeight] : [0, 0];
              if (showVolume) currentY += paneHeight + gap;
              
              const priceDomain = [Math.max(0.1, currentY), 1];

              return (
                 <div key={sym} className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex flex-col h-[500px]">
                    <div className="px-4 py-2 border-b border-[#ffffff1a] font-mono font-bold text-lg text-white flex justify-between items-center bg-[#0e1117]">
                        {sym}
                        <div className="flex gap-4 text-xs font-normal">
                           <span className="text-[#888]">LTP: <span className="text-[#fafafa]">{formatNumber(closes[closes.length-1])}</span></span>
                           <span className="text-[#888]">Vol: <span className="text-[#fafafa]">{formatNumber(volumes[volumes.length-1] / 1000000, 2)}M</span></span>
                           {deliveryPct[deliveryPct.length-1] !== undefined && <span className="text-[#888]">Delivery: <span className="text-[#fafafa]">{formatNumber(deliveryPct[deliveryPct.length-1], 1)}%</span></span>}
                           {trendAlignment[trendAlignment.length-1] !== undefined && <span className="text-[#888]">Trend: <span className={`font-bold ${trendAlignment[trendAlignment.length-1] > 0 ? 'text-green-400' : (trendAlignment[trendAlignment.length-1] < 0 ? 'text-red-400' : 'text-[#fafafa]')}`}>{trendAlignment[trendAlignment.length-1]}</span></span>}
                        </div>
                    </div>
                    <div className="flex-1 w-full relative">
                        <Plot
                             data={[
                                 // Main Candlestick
                                 {
                                     type: 'candlestick',
                                     x: dates, open: opens, high: highs, low: lows, close: closes,
                                     name: sym, yaxis: 'y',
                                     increasing: {line: {color: '#22c55e', width: 1.5}, fillcolor: '#1a1c24'}, // hollow body
                                     decreasing: {line: {color: '#ef4444', width: 1.5}, fillcolor: '#ef4444'}, // filled body
                                     customdata: dates.map((_, i) => {
                                         const vol = volumes[i] || 0;
                                         const volStr = vol >= 1000000 
                                             ? (vol / 1000000).toFixed(2) + 'M'
                                             : (vol / 1000).toFixed(1) + 'k';
                                         return [
                                             formatNumber(deliveryPct[i], 1),
                                             formatNumber(relVol[i], 2),
                                             formatNumber(volComp[i], 2),
                                             formatNumber(stockReturn[i], 2),
                                             volStr
                                         ];
                                     }),
                                     hovertemplate: 
                                         '<b>%{x}</b><br><br>' +
                                         'O: %{open:.2f}<br>' +
                                         'H: %{high:.2f}<br>' +
                                         'L: %{low:.2f}<br>' +
                                         'C: %{close:.2f}<br>' +
                                         '<br>Vol: %{customdata[4]}<br>' +
                                         'Del%: %{customdata[0]}<br>' +
                                         'Rel Vol: %{customdata[1]}<br>' +
                                         'Vol Comp: %{customdata[2]}<br>' +
                                         'Ret: %{customdata[3]}%<extra></extra>'
                                 },
                                 // Overlays
                                 ...(showVwap ? [{ type: 'scattergl', mode: 'lines', x: dates, y: vwap, name: 'VWAP', line: { color: '#888', width: 1.5, dash: 'dot' }, yaxis: 'y', hovertemplate: '%{y:.2f}' } as any] : []),
                                 ...(showSma20 ? [{ type: 'scattergl', mode: 'lines', x: dates, y: sma20, name: 'SMA20', line: { color: '#eab308', width: 1 }, yaxis: 'y', hovertemplate: '%{y:.2f}' } as any] : []),
                                 ...(showSma50 ? [{ type: 'scattergl', mode: 'lines', x: dates, y: sma50, name: 'SMA50', line: { color: '#0ea5e9', width: 1 }, yaxis: 'y', hovertemplate: '%{y:.2f}' } as any] : []),
                                 ...(showSma150 ? [{ type: 'scattergl', mode: 'lines', x: dates, y: sma150, name: 'SMA150', line: { color: '#d946ef', width: 1.5 }, yaxis: 'y', hovertemplate: '%{y:.2f}' } as any] : []),
                                 ...(showSma200 ? [{ type: 'scattergl', mode: 'lines', x: dates, y: sma200, name: 'SMA200', line: { color: '#f97316', width: 1.5 }, yaxis: 'y', hovertemplate: '%{y:.2f}' } as any] : []),
                                 ...(showNiftyOut ? [{ type: 'scattergl', mode: 'lines', x: dates, y: niftyOut, name: 'Nifty Outperf.', line: { color: '#a855f7', width: 1.5 }, yaxis: 'y4', opacity: 0.8, fill: 'tozeroy', fillcolor: 'rgba(168, 85, 247, 0.1)', hovertemplate: '%{y:.2f}%' } as any] : []),
                                 
                                 // Swing points
                                 ...(showSwings && swingHighsDates.length > 0 ? [{ type: 'scattergl', mode: 'markers+text', x: swingHighsDates, y: swingHighsValues, name: 'Swing High', marker: { symbol: 'triangle-down', size: 8, color: '#ef4444' }, text: swingHighsValues.map(v => v.toFixed(1)), textposition: 'top center', textfont: {size: 9, color: '#ef4444'}, yaxis: 'y' } as any] : []),
                                 ...(showSwings && swingLowsDates.length > 0 ? [{ type: 'scattergl', mode: 'markers+text', x: swingLowsDates, y: swingLowsValues, name: 'Swing Low', marker: { symbol: 'triangle-up', size: 8, color: '#22c55e' }, text: swingLowsValues.map(v => v.toFixed(1)), textposition: 'bottom center', textfont: {size: 9, color: '#22c55e'}, yaxis: 'y' } as any] : []),
                                 
                                 // Smart Money Footprint
                                 ...(showSmartMoney && smartMoneyX.length > 0 ? [{
                                     type: 'scattergl',
                                     mode: 'markers+text',
                                     x: smartMoneyX, y: smartMoneyY,
                                     text: smartMoneyText,
                                     textposition: 'top center',
                                     textfont: {size: 10, color: '#facc15'},
                                     marker: { size: 12, color: 'rgba(250, 204, 21, 0.4)', symbol: 'circle', line: {color: '#facc15', width: 2} },
                                     name: 'Smart Money',
                                     yaxis: 'y',
                                     hoverinfo: 'text'
                                 } as any] : []),

                                 // Institutional Blocks Marker
                                 ...(showInstBlocks && instBlocksX.length > 0 ? [{
                                     type: 'scattergl',
                                     mode: 'markers+text',
                                     x: instBlocksX, y: instBlocksY,
                                     text: instBlocksText,
                                     textposition: 'top center',
                                     textfont: {size: 12, color: '#22d3ee', weight: 'bold'},
                                     marker: { size: 24, color: 'rgba(34, 211, 238, 0.9)', symbol: 'star-diamond', line: {color: '#ffffff', width: 1.5} },
                                     name: 'Inst. Block',
                                     yaxis: 'y',
                                     hoverinfo: 'text'
                                 } as any] : []),

                                 // Delivery VWAP Bands
                                 ...(showDelVwapBands ? [
                                     { type: 'scattergl', mode: 'lines', x: dates, y: delVwapBandsUpper, name: 'Del. VWAP High', line: { color: 'rgba(251, 146, 60, 0.7)', width: 1.5, dash: 'dot' }, yaxis: 'y', hoverinfo: 'none' } as any,
                                     { type: 'scattergl', mode: 'lines', x: dates, y: delVwapBandsLower, name: 'Del. VWAP Low', line: { color: 'rgba(251, 146, 60, 0.7)', width: 1.5, dash: 'dot' }, fill: 'tonexty', fillcolor: 'rgba(251, 146, 60, 0.1)', yaxis: 'y', hoverinfo: 'none' } as any,
                                     { type: 'scattergl', mode: 'lines', x: dates, y: delVwapMid, name: 'Del. VWAP', line: { color: '#fbbf24', width: 2, dash: 'solid' }, yaxis: 'y', hovertemplate: 'Del VWAP: %{y:.2f}' } as any
                                 ] : []),

                                 // Delivery Profile Overlay
                                 ...(showDeliveryProfile && deliveryProfileX.length > 0 ? [{
                                     type: 'bar',
                                     orientation: 'h',
                                     x: deliveryProfileX, y: deliveryProfileY,
                                     marker: { color: deliveryProfileColors },
                                     xaxis: 'x2',
                                     yaxis: 'y',
                                     hoverinfo: 'skip',
                                     name: 'Del Profile'
                                 } as any] : []),

                                 // Volume Histogram Pane
                                 ...(showVolume ? [{
                                     type: 'bar',
                                     x: dates, y: volumes,
                                     name: 'Vol',
                                     yaxis: 'y2',
                                     marker: { color: volumeColors },
                                     hovertemplate: '%{y:.2s}'
                                 } as any] : []),

                                 // Delivery Percent Pane
                                 ...(showDelivery ? [{
                                     type: 'bar',
                                     x: dates, y: deliveryFinal,
                                     name: 'Del Qty',
                                     yaxis: 'y5',
                                     marker: { color: deliveryColorsInverse, opacity: 0.8 },
                                     hovertemplate: '%{y:.2s}<br>Pct: %{customdata[0]}%<extra></extra>'
                                 }, {
                                     type: 'scattergl',
                                     mode: 'lines',
                                     x: dates, y: deliveryMa60,
                                     name: 'Del MA(60)',
                                     yaxis: 'y5',
                                     line: { color: '#f59e0b', width: 2 },
                                     hovertemplate: 'MA: %{y:.2s}<extra></extra>'
                                 } as any] : []),
                                 
                                 // RSI
                                 ...(showRsi ? [
                                     { type: 'scattergl', mode: 'lines', x: dates, y: rsiObj, name: 'RSI(14)', line: { color: '#8b5cf6', width: 1.5 }, yaxis: 'y3', hovertemplate: '%{y:.1f}' } as any
                                 ] : []),
                                 
                                 // Delivery A/D
                                 ...(showDelAD ? [
                                     { type: 'scattergl', mode: 'lines', x: dates, y: delAdLine, name: 'Del. A/D', line: { color: '#ec4899', width: 1.5 }, yaxis: 'y6', hovertemplate: 'Val: %{y:.2s}', fill: 'tozeroy', fillcolor: 'rgba(236,72,153,0.1)' } as any
                                 ] : [])
                             ]}
                             layout={{
                                 autosize: true,
                                 margin: { l: 40, r: 40, t: 10, b: 24 }, // minimal padding
                                 plot_bgcolor: '#1a1c24',
                                 paper_bgcolor: '#1a1c24',
                                 font: { color: '#888', family: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' },
                                 showlegend: false,
                                 hovermode: 'x unified',
                                 hoverlabel: { bgcolor: 'rgba(14, 17, 23, 0.4)', bordercolor: 'rgba(255, 255, 255, 0.1)', font: { family: 'ui-monospace', size: 11 } },
                                 dragmode: 'pan',
                                 xaxis: {
                                    rangeslider: { visible: false },
                                    showgrid: false,
                                    type: 'date',
                                    tickformatstops: [
                                        { dtickrange: [null, 86400000], value: "%H:%M" }, // < 1d
                                        { dtickrange: [86400000, 7776000000], value: "%e %b" }, // 1d to 3m
                                        { dtickrange: [7776000000, "M12"], value: "%b '%y" }, // 3m to 1y
                                        { dtickrange: ["M12", null], value: "%Y" }, // > 1y
                                    ],
                                    showspikes: true,
                                    spikemode: 'across',
                                    spikedash: 'solid',
                                    spikecolor: '#333',
                                    spikethickness: 1,
                                 },
                                 xaxis2: {
                                    overlaying: 'x',
                                    side: 'top',
                                    showgrid: false,
                                    zeroline: false,
                                    showticklabels: false,
                                    range: [0, Math.max(...deliveryProfileX, 1) * 3] // Bars take up max 1/3 of chart
                                 },
                                 yaxis: {
                                    domain: priceDomain,
                                    gridcolor: 'rgba(255,255,255,0.05)',
                                    zeroline: false,
                                    autorange: true,
                                    type: showLogScale ? 'log' : 'linear',
                                 },
                                 yaxis2: {
                                    domain: volDomain,
                                    gridcolor: 'rgba(255,255,255,0.05)',
                                    zeroline: false,
                                    showticklabels: true,
                                    tickformat: '.2s',
                                    visible: showVolume
                                 },
                                 yaxis3: {
                                    domain: rsiDomain,
                                    gridcolor: 'rgba(255,255,255,0.05)',
                                    zeroline: false,
                                    tickvals: [0, 30, 50, 70, 100],
                                    visible: showRsi
                                 },
                                 yaxis4: {
                                    domain: priceDomain,
                                    side: 'right',
                                    overlaying: 'y',
                                    showgrid: false,
                                    zeroline: false,
                                    visible: showNiftyOut
                                 },
                                 yaxis5: {
                                    domain: delDomain,
                                    gridcolor: 'rgba(255,255,255,0.05)',
                                    zeroline: false,
                                    tickformat: '.2s',
                                    visible: showDelivery
                                 },
                                 yaxis6: {
                                    domain: delAdDomain,
                                    gridcolor: 'rgba(255,255,255,0.05)',
                                    zeroline: false,
                                    tickformat: '.2s',
                                    visible: showDelAD,
                                    title: { text: 'Del. A/D', font: { size: 10, color: '#888' } }
                                 },
                                 shapes: shapes
                             }}
                             config={{
                                 responsive: true,
                                 displayModeBar: true,
                                 modeBarButtonsToAdd: ['drawline', 'drawopenpath', 'drawcircle', 'drawrect', 'eraseshape'] as any[],
                                 modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                                 displaylogo: false,
                                 scrollZoom: true
                             }}
                             style={{ width: '100%', height: '100%' }}
                        />
                    </div>
                 </div>
              );
          })}
          
          {symbols.length === 0 && (
             <div className="h-full w-full flex items-center justify-center flex-col text-[#666]">
                <BarChart2 size={48} className="mb-4 opacity-20" />
                <p className="font-mono text-sm">Add a symbol to view Technical Analysis.</p>
             </div>
          )}
      </div>
    </div>
  );
}