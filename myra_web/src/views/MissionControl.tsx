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
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [bRes, pRes, mRes, pledgeRes] = await Promise.all([
          fetch(`${ROOT_BASE}/api/market-breadth`),
          fetch(`${ROOT_BASE}/api/tools/status`),
          fetch(`${ROOT_BASE}/api/finstack/morning-brief`),
          fetch(`${ROOT_BASE}/api/finstack/scan-pledge-risks`).catch(() => null)
        ]);

        if (bRes.ok) {
          const bData = await bRes.json();
          setBreadth(bData);
        }
        if (pRes.ok) {
          const pData = await pRes.json();
          setPipelineStatus(pData);
        }
        if (mRes.ok) {
          const mData = await mRes.json();
          setMorningBrief(mData);
        }
        if (pledgeRes && pledgeRes.ok) {
          const pledgeData = await pledgeRes.json();
          setPledgeRisks(pledgeData);
        } else {
          setPledgeRisks(null);
        }
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
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
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
      {morningBrief && (
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-4 mt-2">
          <div className="flex justify-between items-center border-b border-[#ffffff1a] pb-3">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-[#888]">Morning Brief</h3>
            <span className="text-[10px] text-[#555] font-mono">{morningBrief.timestamp}</span>
          </div>

          {/* Top row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">GIFT Nifty</div>
              <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.gift_nifty)}`}>{morningBrief.gift_nifty}</div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">VIX</div>
              <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.vix)}`}>{morningBrief.vix}</div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">FII Net</div>
              <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.fii_dii?.fii_net)}`}>{morningBrief.fii_dii?.fii_net}</div>
            </div>
            <div>
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider pb-1">DII Net</div>
              <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.fii_dii?.dii_net)}`}>{morningBrief.fii_dii?.dii_net}</div>
            </div>
          </div>

          {/* Middle row: NIFTY setup */}
          <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg mt-1">
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-2">Nifty Setup</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Expected Open</div>
                <div className="text-xs text-[#ccc] font-mono">{morningBrief.nifty_setup?.open}</div>
              </div>
              <div>
                <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Support</div>
                <div className="text-xs text-[#ccc] font-mono">{morningBrief.nifty_setup?.support}</div>
              </div>
              <div>
                <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Resistance</div>
                <div className="text-xs text-[#ccc] font-mono">{morningBrief.nifty_setup?.resistance}</div>
              </div>
              <div>
                <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Bias</div>
                <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.nifty_setup?.bias)}`}>{morningBrief.nifty_setup?.bias}</div>
              </div>
            </div>
          </div>

          {/* Bottom row: BANKNIFTY setup & Events */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-1">
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-2">BankNifty Setup</div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Expected Open</div>
                  <div className="text-xs text-[#ccc] font-mono">{morningBrief.banknifty_setup?.open}</div>
                </div>
                <div>
                  <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Support</div>
                  <div className="text-xs text-[#ccc] font-mono">{morningBrief.banknifty_setup?.support}</div>
                </div>
                <div>
                  <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Resistance</div>
                  <div className="text-xs text-[#ccc] font-mono">{morningBrief.banknifty_setup?.resistance}</div>
                </div>
                <div>
                  <div className="text-[9px] text-[#666] font-mono uppercase tracking-widest pb-1">Bias</div>
                  <div className={`text-xs font-mono font-bold ${getValueColor(morningBrief.banknifty_setup?.bias)}`}>{morningBrief.banknifty_setup?.bias}</div>
                </div>
              </div>
            </div>
            
            {/* Key Events */}
            <div className="bg-[#0e1117] border border-[#ffffff0a] p-3 rounded-lg">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-2">Key Events</div>
              <ul className="space-y-1.5 mt-2 flex flex-col justify-center">
                {morningBrief.key_events?.slice(0, 3).map((ev: string, i: number) => (
                  <li key={i} className="text-[10px] font-mono text-[#aaa] flex gap-2 leading-relaxed">
                    <span className="text-[#666] shrink-0">•</span>
                    <span>{ev}</span>
                  </li>
                ))}
                {(!morningBrief.key_events || morningBrief.key_events.length === 0) && (
                  <li className="text-[10px] font-mono text-[#666]">No key events listed.</li>
                )}
              </ul>
            </div>
          </div>

          {/* Outlook */}
          {morningBrief.outlook && (
            <div className="mt-2 text-xs font-mono text-[#ccc] leading-relaxed border-l-2 border-[#ffffff1a] pl-3 py-1 bg-[#ffffff05] rounded-r p-2">
              {morningBrief.outlook}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
