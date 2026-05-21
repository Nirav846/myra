import { useState, useEffect } from 'react';
import { Activity, BarChart2, BrainCircuit, Target, Database, DatabaseZap, AlertTriangle } from 'lucide-react';
import { Librarian } from '../lib/Librarian';

const API_BASE = 'http://localhost:8000/api';
const ROOT_BASE = API_BASE.replace(/\/api$/, '');

interface BreadthRes {
  advances: number;
  declines: number;
  total: number;
  date: string | null;
}

export default function MissionControlView({ lib, navigateTo }: { lib: Librarian, navigateTo: (id: string) => void }) {
  const [breadth, setBreadth] = useState<BreadthRes | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<any>(null);
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [pledgeRisks, setPledgeRisks] = useState<any[] | null | undefined>(undefined);
  const [niftyOutlook, setNiftyOutlook] = useState<any>(null);
  const [divergence, setDivergence] = useState<any>(null);
  const [sebiAlerts, setSebiAlerts] = useState<any>(null);
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [bRes, pRes, mRes, pledgeRes, outRes, divRes, sebiRes] = await Promise.all([
          fetch(`${ROOT_BASE}/api/market-breadth`),
          fetch(`${ROOT_BASE}/api/tools/status`),
          fetch(`${ROOT_BASE}/api/finstack/morning-brief`).catch(() => null),
          fetch(`${ROOT_BASE}/api/finstack/scan-pledge-risks`).catch(() => null),
          fetch(`${ROOT_BASE}/api/finstack/nifty-outlook`).catch(() => null),
          fetch(`${ROOT_BASE}/api/finstack/fii-retail-divergence`).catch(() => null),
          fetch(`${ROOT_BASE}/api/finstack/sebi-alerts`).catch(() => null)
        ]);

        if (bRes && bRes.ok) {
          const bData = await bRes.json();
          setBreadth(bData);
        }
        if (pRes && pRes.ok) {
          const pData = await pRes.json();
          setPipelineStatus(pData);
        }
        if (mRes && mRes.ok) {
          const mData = await mRes.json();
          setMorningBrief(mData);
        }
        if (pledgeRes && pledgeRes.ok) {
          const pledgeData = await pledgeRes.json();
          setPledgeRisks(pledgeData);
        } else {
          setPledgeRisks(null);
        }
        if (outRes && outRes.ok) setNiftyOutlook(await outRes.json());
        if (divRes && divRes.ok) setDivergence(await divRes.json());
        if (sebiRes && sebiRes.ok) setSebiAlerts(await sebiRes.json());
        setIsOffline(false);
      } catch (err) {
        setIsOffline(true);
        setPledgeRisks(null);
        // Fallback/Mock values
        setBreadth({ advances: 245, declines: 182, total: 427, date: 'Mock' });
      }
    };
    fetchData();
  }, []);

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

  const isPipelineActive = pipelineStatus && isToday(pipelineStatus.ingest);

  let advPct = 50;
  let decPct = 50;
  if (breadth && breadth.total > 0) {
    advPct = Math.round((breadth.advances / breadth.total) * 100);
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
      {isOffline && (
        <div className="bg-yellow-500/10 border border-yellow-500/20 px-4 py-3 rounded flex items-center gap-3">
          <AlertTriangle size={16} className="text-yellow-500" />
          <span className="text-sm font-medium text-yellow-500">
            ⚠️ Backend offline – dashboard is simulated
          </span>
        </div>
      )}

      {/* System Metrics Strip */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex items-center justify-between">
          <div className="w-full">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Market Breadth (NIFTY)</div>
            {!breadth && !isOffline ? (
              <div className="text-sm text-[#ccc] py-1">Waiting for data...</div>
            ) : (
              <>
                <div className="text-xl font-bold flex flex-col sm:flex-row sm:items-baseline gap-2">
                  <span className="text-green-400">{breadth?.advances} ADV</span> 
                  <span className="hidden sm:inline text-[#555]">|</span>
                  <span className="text-red-400">{breadth?.declines} DEC</span>
                </div>
                <div className="w-full h-1 bg-[#333] mt-2 rounded overflow-hidden flex">
                  <div className="h-full bg-green-500 transition-all duration-500" style={{ width: `${advPct}%` }}></div>
                  <div className="h-full bg-red-500 transition-all duration-500" style={{ width: `${decPct}%` }}></div>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Nifty Outlook Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Nifty Outlook</div>
          {!niftyOutlook && !isOffline ? (
            <div className="text-sm text-[#ccc] py-1">Analyzing...</div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-bold font-mono ${getValueColor(niftyOutlook?.probability_up - 50)}`}>
                  {niftyOutlook?.probability_up}% UP
                </span>
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    niftyOutlook?.probability_up > 50 ? 'bg-green-500/10 text-green-400 border-green-500/20' : 
                    'bg-red-500/10 text-red-400 border-red-500/20'
                 }`}>
                  {niftyOutlook?.signal}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <div>
                  <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bull Factors</div>
                  <ul className="space-y-0.5 max-h-16 overflow-y-auto pr-1">
                    {niftyOutlook?.bull_factors?.map((f: string, i: number) => (
                      <li key={i} className="text-[9px] text-green-400 font-mono flex gap-1 leading-tight"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {!niftyOutlook?.bull_factors?.length && <li className="text-[9px] text-[#666] font-mono">None</li>}
                  </ul>
                </div>
                <div>
                  <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bear Factors</div>
                  <ul className="space-y-0.5 max-h-16 overflow-y-auto pr-1">
                    {niftyOutlook?.bear_factors?.map((f: string, i: number) => (
                      <li key={i} className="text-[9px] text-red-400 font-mono flex gap-1 leading-tight"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {!niftyOutlook?.bear_factors?.length && <li className="text-[9px] text-[#666] font-mono">None</li>}
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>

        {/* FII/Retail Divergence Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Smart Money vs Retail</div>
          {!divergence && !isOffline ? (
            <div className="text-sm text-[#ccc] py-1">Scanning flows...</div>
          ) : (
            <>
              <div className="text-sm font-bold text-[#fafafa] mb-1">{divergence?.signal || 'Neutral'}</div>
              <div className="mt-1">
                 <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    divergence?.confidence === 'High' ? 'bg-green-500/10 text-green-400 border-green-500/20' : 
                    divergence?.confidence === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' : 
                    'bg-[#ffffff05] text-[#888] border-[#ffffff1a]'
                 }`}>
                   {divergence?.confidence} Confidence
                 </span>
              </div>
            </>
          )}
        </div>

        {/* SEBI Alerts Widget */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">SEBI Enforcement</div>
          {!sebiAlerts && !isOffline ? (
            <div className="text-sm text-[#ccc] py-1">Checking alerts...</div>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-2">
                <span className={`text-xl font-bold font-mono ${sebiAlerts?.count > 0 ? 'text-red-400' : 'text-green-400'}`}>
                  {sebiAlerts?.count || 0}
                </span>
                <span className="text-[10px] text-[#666] font-mono uppercase">Recent Actions</span>
                {sebiAlerts?.count > 0 && (
                  <span className="ml-auto bg-red-500/10 text-red-400 text-[9px] font-mono px-1.5 py-0.5 rounded border border-red-500/20 uppercase">
                    Risk
                  </span>
                )}
              </div>
              
              {sebiAlerts?.count > 0 && (
                <details className="group mt-auto">
                  <summary className="text-[10px] text-[#888] font-mono cursor-pointer hover:text-[#ccc] transition-colors outline-none list-none flex justify-between items-center">
                    <span>View Companies</span>
                    <span className="text-[#555] group-open:rotate-180 transition-transform">▼</span>
                  </summary>
                  <div className="mt-2 max-h-20 overflow-y-auto pr-1 space-y-1">
                    {sebiAlerts?.recent_actions?.map((act: any, i: number) => (
                      <div key={i} className="flex justify-between items-center text-[9px] font-mono border-b border-[#ffffff1a] pb-1 last:border-0 last:pb-0">
                        <span className="text-[#fafafa] truncate pr-2" title={act.type}>{act.company}</span>
                        <span className="text-[#aaa] shrink-0">{act.date}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>

        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center">
          <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">⚠️ Pledge Risk Alert</div>
          {pledgeRisks === undefined ? (
             <div className="text-sm text-[#ccc] py-1">Scanning...</div>
          ) : pledgeRisks === null ? (
             <div className="text-[10px] text-[#666] font-mono mt-1">Pledge data unavailable</div>
          ) : (
             <>
                <div className="flex gap-3 mb-2">
                   <div className="bg-red-500/10 border border-red-500/20 px-2 py-1 rounded">
                      <span className="text-xs text-red-400 font-mono font-bold">{pledgeRisks.filter(r => r.risk_level === 'HIGH').length} HIGH</span>
                   </div>
                   <div className="bg-yellow-500/10 border border-yellow-500/20 px-2 py-1 rounded">
                      <span className="text-xs text-yellow-400 font-mono font-bold">{pledgeRisks.filter(r => r.risk_level === 'MEDIUM').length} MED</span>
                   </div>
                </div>
                {pledgeRisks.filter(r => r.risk_level === 'HIGH').length > 0 && (
                   <div className="max-h-16 overflow-y-auto pr-1 space-y-1">
                      {pledgeRisks.filter(r => r.risk_level === 'HIGH').map((r, i) => (
                         <div key={i} className="flex justify-between items-center text-[10px] font-mono">
                            <span className="text-[#fafafa] font-bold">{r.symbol}</span>
                            <span className="text-[#aaa]">{r.pledge_pct}% <span className="text-red-400 ml-1">+{r.qoq_change}%</span></span>
                         </div>
                      ))}
                   </div>
                )}
             </>
          )}
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex items-center justify-between">
          <div>
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Active SQLite Sidecars</div>
            <div className="text-xl font-bold text-[#fafafa] flex items-center gap-2">
              4 / 4 
              <span className="text-xs text-green-400 ml-1 bg-green-400/10 px-2 py-0.5 rounded font-mono">HEALTHY</span>
            </div>
            <div className="text-[10px] text-[#666] font-mono mt-2">_tech, _meta, _inst, _gov</div>
          </div>
          <DatabaseZap size={32} className="text-[#444]" />
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex items-center justify-between">
          <div>
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">System Architecture</div>
            <div className="text-xl font-bold text-[#fafafa]">Hybrid Local</div>
            <div className="text-[10px] text-[#666] font-mono mt-2 flex flex-col sm:flex-row sm:items-center sm:gap-2">
               <span>Mode: {!lib.isConnectedToLocalRepo ? 'Mock Simulation' : 'Connected to API'}</span>
               <span className="hidden sm:block">|</span>
               <span className={isPipelineActive ? 'text-green-400' : 'text-yellow-400'}>
                 Pipeline: {isPipelineActive ? 'Active' : 'Stale'}
               </span>
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

      {/* Morning Brief Widget */}
      {morningBrief && morningBrief.market_brief && (
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-4 mt-2">
          <div className="flex justify-between items-center border-b border-[#ffffff1a] pb-3">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-[#888]">Morning Brief</h3>
            <span className="text-[10px] text-[#555] font-mono">{morningBrief.timestamp}</span>
          </div>

          {/* Row 1: Market Indices */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">NIFTY 50</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{morningBrief.market_brief.nifty?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(morningBrief.market_brief.nifty?.change_pct)}`}>
                 {morningBrief.market_brief.nifty?.change > 0 ? '+' : ''}{morningBrief.market_brief.nifty?.change} ({morningBrief.market_brief.nifty?.change_pct > 0 ? '+' : ''}{morningBrief.market_brief.nifty?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">SENSEX</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{morningBrief.market_brief.sensex?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(morningBrief.market_brief.sensex?.change_pct)}`}>
                 {morningBrief.market_brief.sensex?.change > 0 ? '+' : ''}{morningBrief.market_brief.sensex?.change} ({morningBrief.market_brief.sensex?.change_pct > 0 ? '+' : ''}{morningBrief.market_brief.sensex?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">BANK NIFTY</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{morningBrief.market_brief.bank_nifty?.value}</div>
              <div className={`text-xs font-mono ${getValueColor(morningBrief.market_brief.bank_nifty?.change_pct)}`}>
                 {morningBrief.market_brief.bank_nifty?.change > 0 ? '+' : ''}{morningBrief.market_brief.bank_nifty?.change} ({morningBrief.market_brief.bank_nifty?.change_pct > 0 ? '+' : ''}{morningBrief.market_brief.bank_nifty?.change_pct}%)
              </div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">INDIA VIX</div>
              <div className="text-xs font-mono font-bold text-[#fafafa]">{morningBrief.pre_market?.india_vix?.current_vix}</div>
              <div className="text-[10px] font-mono text-[#ccc] truncate" title={morningBrief.pre_market?.india_vix?.signal}>{morningBrief.pre_market?.india_vix?.signal}</div>
            </div>
          </div>

          {/* Row 2: Top Movers */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-green-400 font-mono uppercase tracking-wider mb-2">Top Gainers</div>
              <div className="space-y-1">
                 {morningBrief.top_gainers?.map((g: any, i: number) => (
                    <div key={i} className="flex justify-between text-xs font-mono">
                      <span className="text-[#fafafa]">{g.symbol}</span>
                      <span className="text-[#ccc]">{g.ltp} <span className="text-green-400 ml-2">+{g.change_pct}%</span></span>
                    </div>
                 ))}
                 {(!morningBrief.top_gainers || morningBrief.top_gainers.length === 0) && (
                    <div className="text-xs font-mono text-[#666]">No data</div>
                 )}
              </div>
            </div>
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-red-400 font-mono uppercase tracking-wider mb-2">Top Losers</div>
              <div className="space-y-1">
                 {morningBrief.top_losers?.map((l: any, i: number) => (
                    <div key={i} className="flex justify-between text-xs font-mono">
                      <span className="text-[#fafafa]">{l.symbol}</span>
                      <span className="text-[#ccc]">{l.ltp} <span className="text-red-400 ml-2">{l.change_pct}%</span></span>
                    </div>
                 ))}
                 {(!morningBrief.top_losers || morningBrief.top_losers.length === 0) && (
                    <div className="text-xs font-mono text-[#666]">No data</div>
                 )}
              </div>
            </div>
          </div>

          {/* Row 3: Institutional Flow + Sector Performance */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
             <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">FII Net</div>
               <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.institutional_flow?.fii_net)}`}>{morningBrief.institutional_flow?.fii_net}</div>
             </div>
             <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">DII Net</div>
               <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.institutional_flow?.dii_net)}`}>{morningBrief.institutional_flow?.dii_net}</div>
             </div>
             <div className="col-span-2">
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">Leading Sectors</div>
               <div className="flex gap-2 flex-wrap text-[10px] font-mono">
                 {morningBrief.sector_performance?.map((s: any, i: number) => (
                    <span key={i} className="bg-[#0e1117] border border-[#ffffff1a] px-2 py-1 rounded text-[#ccc] whitespace-nowrap">
                       {s.sector} <span className={getValueColor(s.change_pct)}>{s.change_pct > 0 ? '+' : ''}{s.change_pct}%</span>
                    </span>
                 ))}
                 {(!morningBrief.sector_performance || morningBrief.sector_performance.length === 0) && (
                    <span className="text-[#666]">No sector data</span>
                 )}
               </div>
             </div>
          </div>

          {/* Row 4: Pre-Market Outlook */}
          <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">Pre-market Direction</div>
               <div className="flex items-center gap-2">
                 <span className="text-xs text-[#fafafa] font-bold font-mono">GIFT NIFTY: {morningBrief.pre_market?.gift_nifty?.value}</span>
                 <span className={`text-xs font-mono ${getValueColor(morningBrief.pre_market?.gift_nifty?.change)}`}>
                    {morningBrief.pre_market?.gift_nifty?.change > 0 ? '+' : ''}{morningBrief.pre_market?.gift_nifty?.change}
                 </span>
               </div>
               <div className="text-[10px] text-[#ccc] font-mono mt-1">
                  Probability Up: <span className={getValueColor(morningBrief.pre_market?.nifty_direction?.probability_up - 50)}>{morningBrief.pre_market?.nifty_direction?.probability_up}%</span> ({morningBrief.pre_market?.nifty_direction?.signal})
               </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
               <div>
                 <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bull Factors</div>
                 <ul className="space-y-0.5">
                    {morningBrief.pre_market?.nifty_direction?.bull_factors?.map((f: string, i: number) => (
                       <li key={i} className="text-[9px] text-green-400 font-mono flex gap-1"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {(!morningBrief.pre_market?.nifty_direction?.bull_factors || morningBrief.pre_market.nifty_direction.bull_factors.length === 0) && (
                       <li className="text-[10px] text-[#666] font-mono">None identified</li>
                    )}
                 </ul>
               </div>
               <div>
                 <div className="text-[9px] text-[#888] font-mono uppercase tracking-wider pb-1">Bear Factors</div>
                 <ul className="space-y-0.5">
                    {morningBrief.pre_market?.nifty_direction?.bear_factors?.map((f: string, i: number) => (
                       <li key={i} className="text-[9px] text-red-400 font-mono flex gap-1"><span className="shrink-0">•</span><span>{f}</span></li>
                    ))}
                    {(!morningBrief.pre_market?.nifty_direction?.bear_factors || morningBrief.pre_market.nifty_direction.bear_factors.length === 0) && (
                       <li className="text-[10px] text-[#666] font-mono">None identified</li>
                    )}
                 </ul>
               </div>
            </div>
          </div>

          {/* Row 5: Key Events */}
          {(morningBrief.key_events?.length > 0 || morningBrief.morning_text) && (
             <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
               <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-2">Key Events & Briefing</div>
               <div className="space-y-1.5 flex flex-col justify-center text-[#aaa] font-mono whitespace-pre-wrap text-[10px] leading-relaxed">
                 {morningBrief.key_events && morningBrief.key_events.length > 0 ? (
                    morningBrief.key_events.map((ev: string, i: number) => (
                       <div key={i} className="flex gap-2"><span className="text-[#666] shrink-0">•</span><span>{ev}</span></div>
                    ))
                 ) : (
                    morningBrief.morning_text
                 )}
               </div>
             </div>
          )}

          {/* Row 6: VIX Interpretation (Collapsible) */}
          <div className="border border-[#ffffff1a] rounded-lg overflow-hidden">
             <details className="group">
                <summary className="bg-[#1a1c24] cursor-pointer p-3 text-[10px] font-mono uppercase tracking-wider text-[#888] hover:text-[#ccc] transition-colors flex justify-between items-center list-none outline-none">
                  <span>VIX Interpretation ({morningBrief.pre_market?.india_vix?.current_vix})</span>
                  <span className="text-[#555] group-open:rotate-180 transition-transform">▼</span>
                </summary>
                <div className="p-3 pt-0 bg-[#1a1c24] text-[10px] font-mono text-[#aaa]">
                   {getVixInterpretation(morningBrief.pre_market?.india_vix)}
                </div>
             </details>
          </div>

        </div>
      )}
    </div>
  );
}
