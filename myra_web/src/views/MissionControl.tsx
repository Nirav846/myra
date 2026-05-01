import { Activity, BarChart2, BrainCircuit, Target, Database, DatabaseZap } from 'lucide-react';
import { Librarian } from '../lib/Librarian';

export default function MissionControlView({ lib, navigateTo }: { lib: Librarian, navigateTo: (id: string) => void }) {
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
        { label: 'Volume Spikes', action: () => {} },
        { label: 'RSI Divergence', action: () => {} }
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
        { label: 'Insider Trading', action: () => {} },
        { label: 'Smart Money Ignition', action: () => {} }
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
        { label: 'AEON Inference', action: () => navigateTo('AEON Model') },
        { label: 'Dilated CNN Forecast', action: () => {} },
        { label: 'Indicator Parquet Lake', action: () => navigateTo('Parquet Lake') }
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
        { label: 'Graham Model', action: () => navigateTo('Value Ranker') },
        { label: 'Fundamental Ranker', action: () => navigateTo('Value Ranker') },
        { label: 'Sector Analysis', action: () => {} }
      ]
    }
  ];

  return (
    <div className="flex flex-col gap-6">
      {/* System Metrics Strip */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex items-center justify-between">
          <div>
            <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Market Breadth (NIFTY)</div>
            <div className="text-xl font-bold flex flex-col sm:flex-row sm:items-baseline gap-2">
              <span className="text-green-400">245 ADV</span> 
              <span className="hidden sm:inline text-[#555]">|</span>
              <span className="text-red-400">182 DEC</span>
            </div>
            <div className="w-full h-1 bg-[#333] mt-2 rounded overflow-hidden flex">
              <div className="h-full bg-green-500 w-[57%]"></div>
              <div className="h-full bg-red-500 w-[43%]"></div>
            </div>
          </div>
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
            <div className="text-[10px] text-[#666] font-mono mt-2">Mode: {!lib.isConnectedToLocalRepo ? 'Mock Simulation' : 'Connected to API'}</div>
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
                  onClick={item.action}
                  className="text-left px-3 py-2 text-sm text-[#ccc] hover:text-white hover:bg-[#ffffff1a] rounded transition-colors group flex items-center justify-between"
                >
                  <span>{item.label}</span>
                  <span className="text-[10px] font-mono text-[#555] group-hover:text-[#888]">{'>'}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
