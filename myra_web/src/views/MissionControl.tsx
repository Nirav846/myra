import { useState, useEffect, useRef } from 'react';
import { Activity, BarChart2, BrainCircuit, Target, Database, RotateCw } from 'lucide-react';
import { Librarian } from '../lib/Librarian';
import { useLazyWidgetData } from '../hooks/useLazyWidgetData';
import { SymbolAutocomplete } from '../components/SymbolAutocomplete';

const API_BASE = 'http://localhost:8000/api';
const ROOT_BASE = API_BASE.replace(/\/api$/, '');

interface BreadthRes {
  advances: number;
  declines: number;
  total: number;
  date: string | null;
}

export default function MissionControlView({ lib, navigateTo }: { lib: Librarian, navigateTo: (id: string) => void }) {
  const [fiiSymbol, setFiiSymbol] = useState('RELIANCE');
  const [briefSymbol, setBriefSymbol] = useState('RELIANCE');
  const [timelineSymbol, setTimelineSymbol] = useState('RELIANCE');

  const breadthWidget = useLazyWidgetData<BreadthRes>('market_breadth', async () => {
    const res = await fetch(`${ROOT_BASE}/api/market-breadth`);
    if (!res.ok) throw new Error('Failed to load market breadth');
    return res.json();
  });

  const pipelineWidget = useLazyWidgetData<any>('pipeline_status', async () => {
    const res = await fetch(`${ROOT_BASE}/api/tools/status`);
    if (!res.ok) throw new Error('Failed to load pipeline status');
    return res.json();
  });

  const briefWidget = useLazyWidgetData<any>('morning_brief', async () => {
    const res = await fetch(`${ROOT_BASE}/api/finstack/morning-brief`);
    if (!res.ok) throw new Error('Failed to load morning brief');
    return res.json();
  });

  const niftyWidget = useLazyWidgetData<any>('nifty_outlook', async () => {
    const res = await fetch(`${ROOT_BASE}/api/finstack/nifty-outlook`);
    if (!res.ok) throw new Error('Failed to load nifty outlook');
    return res.json();
  });

  const divergenceWidget = useLazyWidgetData<any>('fii_divergence', async () => {
    const sym = fiiSymbol || 'RELIANCE';
    const res = await fetch(`${ROOT_BASE}/api/finstack/fii-retail-divergence?symbol=${sym}`);
    if (!res.ok) throw new Error('Failed to load divergence data');
    return res.json();
  });

  const unusualWidget = useLazyWidgetData<any>('unusual_activity', async () => {
    const sym = fiiSymbol || 'RELIANCE';
    const res = await fetch(`${ROOT_BASE}/api/finstack/unusual-activity?symbol=${sym}`);
    if (!res.ok) throw new Error('Failed to load unusual activity');
    return res.json();
  });

  const stockBrief = useLazyWidgetData<any>('stock_brief', async () => {
    const sym = briefSymbol || 'RELIANCE';
    const res = await fetch(`${ROOT_BASE}/api/finstack/stock-brief?symbol=${sym}`);
    if (!res.ok) throw new Error('Failed to load stock brief');
    return res.json();
  });

  const timelineWidget = useLazyWidgetData<any>('stock_timeline', async () => {
    const sym = timelineSymbol || 'RELIANCE';
    const res = await fetch(`${ROOT_BASE}/api/finstack/stock-timeline?symbol=${sym}`);
    if (!res.ok) throw new Error('Failed to load stock timeline');
    return res.json();
  });

  const fiiMount = useRef(true);
  useEffect(() => {
    if (fiiMount.current) { fiiMount.current = false; return; }
    if (divergenceWidget.data !== null || divergenceWidget.autoRefresh) {
      divergenceWidget.fetchData();
    }
  }, [fiiSymbol]);

  const briefMount = useRef(true);
  useEffect(() => {
    if (briefMount.current) { briefMount.current = false; return; }
    if (stockBrief.data !== null || stockBrief.autoRefresh) {
      stockBrief.fetchData();
    }
  }, [briefSymbol]);

  const timelineMount = useRef(true);
  useEffect(() => {
    if (timelineMount.current) { timelineMount.current = false; return; }
    if (timelineWidget.data !== null || timelineWidget.autoRefresh) {
      timelineWidget.fetchData();
    }
  }, [timelineSymbol]);

  const categories = [
    {
      title: 'Technicals',
      color: 'yellow',
      borderColor: 'border-yellow-500/50',
      bgColor: 'bg-yellow-500/10',
      textColor: 'text-yellow-400',
      icon: <Activity size={24} />,
      items: [
        { label: 'FVG Scanner', action: () => navigateTo('FVG Scanner') },
        { label: 'Reversion Engine', action: () => navigateTo('Reversion Engine') },
        { label: 'Sector Flow', action: () => navigateTo('Sector Flow') }
      ]
    },
    {
      title: 'Institutional',
      color: 'fuchsia',
      borderColor: 'border-fuchsia-500/50',
      bgColor: 'bg-fuchsia-500/10',
      textColor: 'text-fuchsia-400',
      icon: <BarChart2 size={24} />,
      items: [
        { label: 'Deals Leaderboard', action: () => navigateTo('Leaderboard') },
        { label: 'Delivery Volume Profile', action: () => navigateTo('Delivery Volume Profile') },
        { label: 'Ghost Simulator', action: () => navigateTo('Ghost Simulator') }
      ]
    },
    {
      title: 'ML / EXP',
      color: 'cyan',
      borderColor: 'border-cyan-500/50',
      bgColor: 'bg-cyan-500/10',
      textColor: 'text-cyan-400',
      icon: <BrainCircuit size={24} />,
      items: [
        { label: 'Multibagger Matrix', action: () => navigateTo('Multibagger Matrix') },
        { label: 'Historical Search', action: () => navigateTo('Historical Search') },
        { label: 'Data Lake', action: () => navigateTo('Parquet Lake') }
      ]
    },
    {
      title: 'Value',
      color: 'green',
      borderColor: 'border-green-500/50',
      bgColor: 'bg-green-500/10',
      textColor: 'text-green-400',
      icon: <Target size={24} />,
      items: [
        { label: 'Value Ranker', action: () => navigateTo('Value Ranker') },
        { label: 'Sector Analysis', action: () => navigateTo('Sector Flow') },
        { label: 'Graham Model', action: null }
      ]
    }
  ];

  const isToday = (dateStr: string) => {
    if (!dateStr || dateStr === 'Never') return false;
    try {
      const d = new Date(dateStr);
      const today = new Date();
      return d.getDate() === today.getDate() && 
             d.getMonth() === today.getMonth() && 
             d.getFullYear() === today.getFullYear();
    } catch {
      return false;
    }
  };

  const isPipelineActive = pipelineWidget.data && isToday(pipelineWidget.data.ingest);

  let advPct = 50;
  let decPct = 50;
  if (breadthWidget.data && breadthWidget.data.total > 0) {
    advPct = Math.round((breadthWidget.data.advances / breadthWidget.data.total) * 100);
    decPct = 100 - advPct;
  }

  const getValueColor = (val: any) => {
    const num = typeof val === 'string' ? parseFloat(val) : val;
    if (!isNaN(num) && typeof num === 'number') {
      return num > 0 ? 'text-green-400' : num < 0 ? 'text-red-400' : 'text-[#fafafa]';
    }
    if (typeof val === 'string') {
      const s = val.toLowerCase();
      if (s.includes('bull') || s.includes('positive') || s.includes('buy') || s.includes('up')) return 'text-green-400';
      if (s.includes('bear') || s.includes('negative') || s.includes('sell') || s.includes('down')) return 'text-red-400';
    }
    return 'text-[#fafafa]';
  };

  const getVixInterpretation = (vixObj: any) => {
    if (!vixObj || !vixObj.interpretation) return "Interpretation unavailable";
    const val = vixObj.current_vix;
    if (val < 13) return vixObj.interpretation.below_13;
    if (val < 18) return vixObj.interpretation['13_to_18'];
    if (val < 25) return vixObj.interpretation['18_to_25'];
    return vixObj.interpretation.above_25;
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Morning Brief Widget */}
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-4">
        <div className="flex items-center justify-between border-b border-[#ffffff1a] pb-3">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-[#888]">Morning Brief</h3>
          <div className="flex items-center gap-2">
            <button onClick={briefWidget.fetchData} disabled={briefWidget.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
              <RotateCw size={14} className={briefWidget.loading ? 'animate-spin' : ''} />
            </button>
            <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
              <input id="morning-brief-autorefresh" name="auto-refresh" type="checkbox" checked={briefWidget.autoRefresh} onChange={e => briefWidget.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
              Auto-refresh
            </label>
          </div>
        </div>
        {briefWidget.loading && !briefWidget.data ? (
          <div className="text-sm text-[#ccc] py-8 text-center">Loading morning brief...</div>
        ) : briefWidget.error ? (
          <div className="text-[10px] text-red-400 font-mono text-center py-8">{briefWidget.error}</div>
        ) : !briefWidget.data?.market_brief ? (
          <div className="text-sm text-[#666] py-8 text-center font-mono">Click Refresh to load morning brief</div>
        ) : (
          <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">NIFTY 50</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{briefWidget.data.market_brief.nifty?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(briefWidget.data.market_brief.nifty?.change_pct)}`}>
                 {briefWidget.data.market_brief.nifty?.change > 0 ? '+' : ''}{briefWidget.data.market_brief.nifty?.change} ({briefWidget.data.market_brief.nifty?.change_pct > 0 ? '+' : ''}{briefWidget.data.market_brief.nifty?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">SENSEX</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{briefWidget.data.market_brief.sensex?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(briefWidget.data.market_brief.sensex?.change_pct)}`}>
                 {briefWidget.data.market_brief.sensex?.change > 0 ? '+' : ''}{briefWidget.data.market_brief.sensex?.change} ({briefWidget.data.market_brief.sensex?.change_pct > 0 ? '+' : ''}{briefWidget.data.market_brief.sensex?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">BANK NIFTY</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{briefWidget.data.market_brief.bank_nifty?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(briefWidget.data.market_brief.bank_nifty?.change_pct)}`}>
                 {briefWidget.data.market_brief.bank_nifty?.change > 0 ? '+' : ''}{briefWidget.data.market_brief.bank_nifty?.change} ({briefWidget.data.market_brief.bank_nifty?.change_pct > 0 ? '+' : ''}{briefWidget.data.market_brief.bank_nifty?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">INDIA VIX</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{briefWidget.data.pre_market?.india_vix?.current_vix}</div>
              <div className="text-[10px] font-mono text-[#ccc] truncate" title={briefWidget.data.pre_market?.india_vix?.signal}>{briefWidget.data.pre_market?.india_vix?.signal}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-green-400 font-mono uppercase tracking-wider mb-2">Top Gainers</div>
              <div className="space-y-1">
                 {briefWidget.data.top_gainers?.map((g: any, i: number) => (
                    <div key={i} className="flex justify-between text-xs font-mono">
                      <span className="text-[#fafafa]">{g.symbol}</span>
                      <span className="text-[#ccc]">{g.ltp} <span className="text-green-400 ml-2">+{g.change_pct}%</span></span>
                    </div>
                 ))}
                 {(!briefWidget.data.top_gainers || briefWidget.data.top_gainers.length === 0) && (
                    <div className="text-xs font-mono text-[#666]">No data</div>
                 )}
              </div>
            </div>
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-red-400 font-mono uppercase tracking-wider mb-2">Top Losers</div>
              <div className="space-y-1">
                 {briefWidget.data.top_losers?.map((l: any, i: number) => (
                    <div key={i} className="flex justify-between text-xs font-mono">
                      <span className="text-[#fafafa]">{l.symbol}</span>
                      <span className="text-[#ccc]">{l.ltp} <span className="text-red-400 ml-2">{l.change_pct}%</span></span>
                    </div>
                 ))}
                 {(!briefWidget.data.top_losers || briefWidget.data.top_losers.length === 0) && (
                    <div className="text-xs font-mono text-[#666]">No data</div>
                 )}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
             <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">FII Net</div>
               <div className={`text-xs font-mono font-bold ${getValueColor(briefWidget.data.institutional_flow?.fii_net)}`}>{briefWidget.data.institutional_flow?.fii_net}</div>
             </div>
             <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">DII Net</div>
               <div className={`text-xs font-mono font-bold ${getValueColor(briefWidget.data.institutional_flow?.dii_net)}`}>{briefWidget.data.institutional_flow?.dii_net}</div>
             </div>
             <div className="col-span-2">
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">Leading Sectors</div>
               <div className="flex gap-2 flex-wrap text-[10px] font-mono">
                 {briefWidget.data.sector_performance?.map((s: any, i: number) => (
                    <span key={i} className="bg-[#0e1117] border border-[#ffffff1a] px-2 py-1 rounded text-[#ccc] whitespace-nowrap">
                       {s.sector} <span className={getValueColor(s.change_pct)}>{s.change_pct > 0 ? '+' : ''}{s.change_pct}%</span>
                    </span>
                 ))}
                 {(!briefWidget.data.sector_performance || briefWidget.data.sector_performance.length === 0) && (
                    <span className="text-[#666]">No sector data</span>
                 )}
               </div>
             </div>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">Pre-market Direction</div>
               <div className="flex items-center gap-2">
                 <span className="text-xs text-[#fafafa] font-bold font-mono">GIFT NIFTY: {briefWidget.data.pre_market?.gift_nifty?.value}</span>
                 <span className={`text-xs font-mono ${getValueColor(briefWidget.data.pre_market?.gift_nifty?.change)}`}>
                    {briefWidget.data.pre_market?.gift_nifty?.change > 0 ? '+' : ''}{briefWidget.data.pre_market?.gift_nifty?.change}
                 </span>
               </div>
               <div className="text-[10px] text-[#ccc] font-mono mt-1">
                  Probability Up: <span className={getValueColor(briefWidget.data.pre_market?.nifty_direction?.probability_up - 50)}>{briefWidget.data.pre_market?.nifty_direction?.probability_up}%</span> ({briefWidget.data.pre_market?.nifty_direction?.signal})
               </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
               <div>
                 <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bull Factors</div>
                 <ul className="space-y-0.5">
                    {briefWidget.data.pre_market?.nifty_direction?.bull_factors?.map((f: string, i: number) => (
                       <li key={i} className="text-[9px] text-green-400 font-mono flex gap-1"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {(!briefWidget.data.pre_market?.nifty_direction?.bull_factors || briefWidget.data.pre_market.nifty_direction.bull_factors.length === 0) && (
                       <li className="text-[10px] text-[#666] font-mono">None identified</li>
                    )}
                 </ul>
               </div>
               <div>
                 <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bear Factors</div>
                 <ul className="space-y-0.5">
                    {briefWidget.data.pre_market?.nifty_direction?.bear_factors?.map((f: string, i: number) => (
                       <li key={i} className="text-[9px] text-red-400 font-mono flex gap-1"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {(!briefWidget.data.pre_market?.nifty_direction?.bear_factors || briefWidget.data.pre_market.nifty_direction.bear_factors.length === 0) && (
                       <li className="text-[10px] text-[#666] font-mono">None identified</li>
                    )}
                 </ul>
               </div>
            </div>
          </div>

          {(briefWidget.data.key_events?.length > 0 || briefWidget.data.morning_text) && (
             <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-2">Key Events & Briefing</div>
               <div className="space-y-1.5 flex flex-col justify-center text-[#aaa] font-mono whitespace-pre-wrap text-[10px] leading-relaxed">
                 {briefWidget.data.key_events && briefWidget.data.key_events.length > 0 ? (
                    briefWidget.data.key_events.map((ev: string, i: number) => (
                       <div key={i} className="flex gap-2"><span className="text-[#666] shrink-0">•</span><span>{ev}</span></div>
                    ))
                 ) : (
                    briefWidget.data.morning_text
                 )}
               </div>
             </div>
          )}

          <div className="border border-[#ffffff1a] rounded-lg overflow-hidden">
             <details className="group">
                <summary className="bg-[#1a1c24] cursor-pointer p-3 text-[10px] font-mono uppercase tracking-wider text-[#888] hover:text-[#ccc] transition-colors flex justify-between items-center list-none outline-none">
                  <span>VIX Interpretation ({briefWidget.data.pre_market?.india_vix?.current_vix})</span>
                  <span className="text-[#555] group-open:rotate-180 transition-transform">▼</span>
                </summary>
                <div className="p-3 pt-0 bg-[#1a1c24] text-[10px] font-mono text-[#aaa]">
                   {getVixInterpretation(briefWidget.data.pre_market?.india_vix)}
                </div>
             </details>
          </div>
          </>
        )}
      </div>

      {/* System Metrics Strip */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Market Breadth (NIFTY)</div>
            <div className="flex items-center gap-2">
              <button onClick={breadthWidget.fetchData} disabled={breadthWidget.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
                <RotateCw size={14} className={breadthWidget.loading ? 'animate-spin' : ''} />
              </button>
              <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
                <input type="checkbox" checked={breadthWidget.autoRefresh} onChange={e => breadthWidget.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
                Auto-refresh
              </label>
            </div>
          </div>
          {breadthWidget.loading && !breadthWidget.data ? (
            <div className="text-sm text-[#ccc] py-1">Waiting for data...</div>
          ) : breadthWidget.error ? (
            <div className="text-[10px] text-red-400 font-mono mt-1">{breadthWidget.error}</div>
          ) : !breadthWidget.data ? (
            <div className="text-sm text-[#666] py-1 font-mono">Click Refresh to load</div>
          ) : (
            <>
              <div className="text-xl font-bold flex flex-col sm:flex-row sm:items-baseline gap-2">
                <span className="text-green-400">{breadthWidget.data.advances} ADV</span>
                <span className="hidden sm:inline text-[#555]">|</span>
                <span className="text-red-400">{breadthWidget.data.declines} DEC</span>
              </div>
              <div className="w-full h-1 bg-[#333] mt-2 rounded overflow-hidden flex">
                <div className="h-full bg-green-500 transition-all duration-500" style={{ width: `${advPct}%` }}></div>
                <div className="h-full bg-red-500 transition-all duration-500" style={{ width: `${decPct}%` }}></div>
              </div>
            </>
          )}
        </div>

        {/* Nifty Outlook Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Nifty Outlook</div>
            <div className="flex items-center gap-2">
              <button onClick={niftyWidget.fetchData} disabled={niftyWidget.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
                <RotateCw size={14} className={niftyWidget.loading ? 'animate-spin' : ''} />
              </button>
              <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
                <input type="checkbox" checked={niftyWidget.autoRefresh} onChange={e => niftyWidget.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
                Auto-refresh
              </label>
            </div>
          </div>
          {niftyWidget.loading && !niftyWidget.data ? (
            <div className="text-sm text-[#ccc] py-1">Analyzing...</div>
          ) : niftyWidget.error ? (
            <div className="text-[10px] text-red-400 font-mono mt-1">{niftyWidget.error}</div>
          ) : !niftyWidget.data ? (
            <div className="text-sm text-[#666] py-1 font-mono">Click Refresh to load</div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-bold font-mono ${getValueColor(niftyWidget.data.probability_up - 50)}`}>
                  {niftyWidget.data.probability_up}% UP
                </span>
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    niftyWidget.data.probability_up > 50 ? 'bg-green-500/10 text-green-400 border-green-500/20' : 
                    'bg-red-500/10 text-red-400 border-red-500/20'
                 }`}>
                  {niftyWidget.data.signal}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <div>
                  <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bull Factors</div>
                  <ul className="space-y-0.5 max-h-16 overflow-y-auto pr-1">
                    {niftyWidget.data.bull_factors?.map((f: string, i: number) => (
                      <li key={i} className="text-[9px] text-green-400 font-mono flex gap-1 leading-tight"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {!niftyWidget.data.bull_factors?.length && <li className="text-[9px] text-[#666] font-mono">None</li>}
                  </ul>
                </div>
                <div>
                  <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bear Factors</div>
                  <ul className="space-y-0.5 max-h-16 overflow-y-auto pr-1">
                    {niftyWidget.data.bear_factors?.map((f: string, i: number) => (
                      <li key={i} className="text-[9px] text-red-400 font-mono flex gap-1 leading-tight"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {!niftyWidget.data.bear_factors?.length && <li className="text-[9px] text-[#666] font-mono">None</li>}
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>

        {/* FII/Retail Divergence Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Smart Money vs Retail</div>
            <div className="flex items-center gap-2">
              <button onClick={divergenceWidget.fetchData} disabled={divergenceWidget.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
                <RotateCw size={14} className={divergenceWidget.loading ? 'animate-spin' : ''} />
              </button>
              <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
                <input type="checkbox" checked={divergenceWidget.autoRefresh} onChange={e => divergenceWidget.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
                Auto-refresh
              </label>
            </div>
          </div>
          <div className="mb-2">
            <SymbolAutocomplete value={fiiSymbol} onSelect={setFiiSymbol} placeholder="Symbol..." />
          </div>
          {divergenceWidget.loading && !divergenceWidget.data ? (
            <div className="text-sm text-[#ccc] py-1">Scanning flows...</div>
          ) : divergenceWidget.error ? (
            <div className="text-[10px] text-red-400 font-mono mt-1">{divergenceWidget.error}</div>
          ) : !divergenceWidget.data ? (
            <div className="text-sm text-[#666] py-1 font-mono">Click Refresh to load</div>
          ) : (
            <>
              <div className="text-sm font-bold text-[#fafafa] mb-1">{divergenceWidget.data.signal || 'Neutral'}</div>
              <div className="mt-1">
                 <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    divergenceWidget.data.confidence === 'High' ? 'bg-green-500/10 text-green-400 border-green-500/20' : 
                    divergenceWidget.data.confidence === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' : 
                    'bg-[#ffffff05] text-[#888] border-[#ffffff1a]'
                 }`}>
                   {divergenceWidget.data.confidence} Confidence
                 </span>
              </div>
            </>
          )}
        </div>

        {/* Stock Brief (AI Multi‑Agent Debate) Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Stock Brief (AI Debate)</div>
            <div className="flex items-center gap-2">
              <button onClick={stockBrief.fetchData} disabled={stockBrief.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
                <RotateCw size={14} className={stockBrief.loading ? 'animate-spin' : ''} />
              </button>
              <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
                <input type="checkbox" checked={stockBrief.autoRefresh} onChange={e => stockBrief.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
                Auto-refresh
              </label>
            </div>
          </div>
          <div className="mb-2">
            <SymbolAutocomplete value={briefSymbol} onSelect={setBriefSymbol} placeholder="Symbol..." />
          </div>
          {stockBrief.loading && !stockBrief.data ? (
            <div className="text-sm text-[#ccc] py-1">Running AI debate...</div>
          ) : stockBrief.error ? (
            <div className="text-[10px] text-red-400 font-mono mt-1">{stockBrief.error}</div>
          ) : !stockBrief.data ? (
            <div className="text-sm text-[#666] py-1 font-mono">Click Refresh to load</div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-bold font-mono ${getValueColor(stockBrief.data.consensus === 'Bullish' ? 1 : stockBrief.data.consensus === 'Bearish' ? -1 : 0)}`}>
                  {stockBrief.data.consensus || 'Neutral'}
                </span>
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                  stockBrief.data.confidence === 'High' ? 'bg-green-500/10 text-green-400 border-green-500/20' :
                  stockBrief.data.confidence === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' :
                  'bg-[#ffffff05] text-[#888] border-[#ffffff1a]'
                }`}>
                  {stockBrief.data.confidence || ''} Confidence
                </span>
              </div>
              {stockBrief.data.debate_summary && (
                <div className="text-[9px] text-[#aaa] font-mono leading-relaxed mt-1 max-h-20 overflow-y-auto pr-1">
                  {stockBrief.data.debate_summary}
                </div>
              )}
            </>
          )}
        </div>

        {/* Stock Timeline Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Stock Timeline</div>
            <div className="flex items-center gap-2">
              <button onClick={timelineWidget.fetchData} disabled={timelineWidget.loading} className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40" title="Refresh">
                <RotateCw size={14} className={timelineWidget.loading ? 'animate-spin' : ''} />
              </button>
              <label className="flex items-center gap-1 text-[9px] text-[#666] font-mono cursor-pointer select-none">
                <input type="checkbox" checked={timelineWidget.autoRefresh} onChange={e => timelineWidget.setAutoRefresh(e.target.checked)} className="accent-yellow-500 w-2.5 h-2.5" />
                Auto-refresh
              </label>
            </div>
          </div>
          <div className="mb-2">
            <SymbolAutocomplete value={timelineSymbol} onSelect={setTimelineSymbol} placeholder="Symbol..." />
          </div>
          {timelineWidget.loading && !timelineWidget.data ? (
            <div className="text-sm text-[#ccc] py-1">Loading timeline...</div>
          ) : timelineWidget.error ? (
            <div className="text-[10px] text-red-400 font-mono mt-1">{timelineWidget.error}</div>
          ) : !timelineWidget.data ? (
            <div className="text-sm text-[#666] py-1 font-mono">Click Refresh to load</div>
          ) : (
            <>
              {Array.isArray(timelineWidget.data.events) && timelineWidget.data.events.length > 0 ? (
                <div className="max-h-24 overflow-y-auto pr-1 space-y-1">
                  {timelineWidget.data.events.map((ev: any, i: number) => (
                    <div key={i} className="flex items-start gap-2 text-[9px] font-mono border-b border-[#ffffff1a] pb-1 last:border-0">
                      <span className="shrink-0 text-[#666] mt-0.5">{ev.date || ''}</span>
                      <span className="text-[#ccc] leading-tight">{ev.headline || ev.title || ev.detail || ''}</span>
                      {ev.importance && (
                        <span className={`shrink-0 px-1 rounded text-[8px] ${
                          String(ev.importance).toLowerCase() === 'high' ? 'bg-red-500/10 text-red-400' :
                          String(ev.importance).toLowerCase() === 'medium' ? 'bg-yellow-500/10 text-yellow-400' :
                          'bg-[#ffffff0a] text-[#888]'
                        }`}>{ev.importance}</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-[10px] text-[#666] font-mono mt-1">No timeline events</div>
              )}
            </>
          )}
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex items-center justify-between">
          <div>
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">System Architecture</div>
            <div className="text-xl font-bold text-[#fafafa]">Hybrid Local</div>
            <div className="text-[10px] text-[#666] font-mono mt-2 flex flex-col sm:flex-row sm:items-center sm:gap-2">
               <span>Mode: {!lib.isConnectedToLocalRepo ? 'Mock Simulation' : 'Connected to API'}</span>
               <span className="hidden sm:block">|</span>
               {pipelineWidget.loading ? (
                 <span className="text-[#888]">Pipeline: ...</span>
               ) : pipelineWidget.error ? (
                 <span className="text-red-400">Pipeline: Error</span>
               ) : (
                 <span className={isPipelineActive ? 'text-green-400' : 'text-yellow-400'}>
                   Pipeline: {isPipelineActive ? 'Active' : 'Stale'}
                 </span>
               )}
            </div>
          </div>
          <Database size={32} className="text-[#444]" />
        </div>
      </div>

      {/* Tactical Command Grid */}
      <h3 className="text-sm font-semibold uppercase tracking-wider text-[#888] mt-2 border-b border-[#ffffff1a] pb-2">
        Tactical Command Grid
      </h3>
      
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {categories.map((cat, idx) => (
          <div key={idx} className={`bg-[#0e1117] border ${cat.borderColor} rounded-xl overflow-hidden flex flex-col transition-all hover:shadow-lg focus-within:ring-2 focus-within:ring-${cat.color}-500/50`}>
            {/* Header Banner */}
            <div className={`${cat.bgColor} border-b ${cat.borderColor} p-4 flex items-center gap-3`}>
              <div className={`${cat.textColor}`}>
                {cat.icon}
              </div>
              <h3 className={`font-bold ${cat.textColor} tracking-wide`}>{cat.title}</h3>
            </div>
            {/* Command Links */}
            <div className="flex flex-col p-2 space-y-1">
              {cat.items.map((item, i) => (
                <button 
                  key={i} 
                  onClick={() => item.action && item.action()}
                  disabled={!item.action}
                  className={`text-left px-3 py-2 text-sm text-[#ccc] rounded transition-colors group flex items-center justify-between ${
                    item.action 
                      ? 'hover:text-white hover:bg-[#ffffff1a]' 
                      : 'opacity-50 cursor-not-allowed'
                  }`}
                  title={!item.action ? '(soon)' : undefined}
                >
                  <span>{item.label} {!item.action && <span className="text-[10px] ml-1">(soon)</span>}</span>
                  <span className={`text-[10px] font-mono ${item.action ? 'text-[#555] group-hover:text-[#888]' : 'text-transparent'}`}>{'>'}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>


    </div>
  );
}
