import { useState, useMemo, useEffect } from 'react';
import LeaderboardView from './views/Leaderboard';
import FVGScannerView from './views/FVGScanner';
import AIAnalysisView from './views/AIAnalysis';
import DataLakeView from './views/DataLake';
import ToolsView from './views/Tools';
import MissionControlView from './views/MissionControl';
import ValueRankerView from './views/ValueRanker';
import SettingsView from './views/Settings';
import HistoricalSearchView from './views/HistoricalSearch';
import SectorFlowView from './views/SectorFlow';
import GhostSimulatorView from './views/GhostSimulator';
import MultibaggerMatrixView from './views/MultibaggerMatrix';
import InstDOMView from './views/InstDOM';
import AdvancedChartView from './views/AdvancedChart';
import ReversionEngineView from './views/ReversionEngine';
import { getLibrarian } from './lib/Librarian';
import { useSettings } from './lib/SettingsContext';
import { AlertCircle, Terminal, Command, Settings as SettingsIcon, PanelLeftClose, PanelLeft } from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState('Mission Control');
  const [globalSelectedTicker, setGlobalSelectedTicker] = useState<string | undefined>();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isDesktopMenuExpanded, setIsDesktopMenuExpanded] = useState(true);
  const [logs, setLogs] = useState<string[]>([
    "[SYSTEM] MYRA Daemon starting...",
    "[DAEMON] Binding _inst_conn to local SQLite",
    "[DAEMON] Cache warmed: 4 tables"
  ]);
  const librarian = useMemo(() => getLibrarian(), []);
  const { settings } = useSettings();

  // Simulate incoming daemon logs for updates
  useEffect(() => {
    const interval = setInterval(() => {
      const messages = [
        "[SYNC] Parquet indicator lake optimized.",
        "[ALERT] High frequency tick detected on NQ.",
        "[SYNC] _tech_conn background vacuum complete.",
        "[DAEMON] Garbage collection freeing memory...",
        "[API] Fetching latest yield curves."
      ];
      setLogs(prev => {
        const newLogs = [...prev, messages[Math.floor(Math.random() * messages.length)]];
        return newLogs.slice(-4); // Keep last 4 logs
      });
    }, 8500);
    return () => clearInterval(interval);
  }, []);

  // Compute disconnected DBs
  const disconnectedDBs = Object.entries(librarian.health as Record<string, any>).filter(([_, status]) => !status.connected);

  // Dynamic Theme mappings based on SettingsContext
  const bgMain = settings.theme === 'pitch-black' ? 'bg-[#000000]' : 'bg-[#0e1117]';
  const bgSidebar = settings.theme === 'pitch-black' ? 'bg-[#0a0a0a]' : 'bg-[#262730]';
  const bgFooter = settings.theme === 'pitch-black' ? 'bg-[#050505]' : 'bg-[#0e1117]';
  const densityClass = settings.density === 'compact' ? 'p-3 gap-3 text-sm' : 'p-6 gap-6 text-base';

  return (
    <div className={`flex h-screen w-full ${bgMain} text-[#fafafa] font-sans overflow-hidden transition-colors relative`}>
      {isMobileMenuOpen && (
        <div className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
      )}
      {/* Sidebar Navigation */}
      <aside className={`${isDesktopMenuExpanded ? 'w-64' : 'w-16'} ${bgSidebar} flex flex-col border-r border-[#ffffff1a] absolute md:relative z-40 h-full !flex transform transition-all duration-300 ${isMobileMenuOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
        <div className={`p-2 border-b border-[#ffffff1a] flex flex-col items-center`}>
          <div className={`flex items-center justify-between w-full h-8 mb-2 ${!isDesktopMenuExpanded && 'justify-center'}`}>
            {isDesktopMenuExpanded && (
              <div className="flex items-center gap-2 overflow-hidden">
                <div className={`w-6 h-6 rounded-lg flex items-center justify-center font-bold italic text-white flex-shrink-0 transition-colors ${settings.animations ? 'hover:scale-105' : ''} bg-${settings.accentColor}-600`}>
                  <Command size={16} />
                </div>
                <h1 className="text-sm font-bold tracking-tight whitespace-nowrap">MYRA <span className={`text-${settings.accentColor}-400`}>v3.2</span></h1>
              </div>
            )}
            {!isDesktopMenuExpanded && (
               <div className={`w-6 h-6 rounded-lg flex items-center justify-center font-bold italic text-white flex-shrink-0 transition-colors ${settings.animations ? 'hover:scale-105' : ''} bg-${settings.accentColor}-600 mb-2`}>
                 <Command size={16} />
               </div>
            )}
            <div className="flex items-center gap-2">
              {isDesktopMenuExpanded && (
                <button className="hidden md:flex text-[#888] hover:text-white transition-colors" onClick={() => setIsDesktopMenuExpanded(false)} title="Collapse sidebar">
                  <PanelLeftClose size={16} />
                </button>
              )}
              <button className="md:hidden text-[#888] hover:text-white" onClick={() => setIsMobileMenuOpen(false)}>✕</button>
            </div>
          </div>
          {isDesktopMenuExpanded && (
            <div className={`flex items-center gap-2 mt-4 px-3 py-1.5 ${bgMain} rounded border border-[#ffffff1a] w-full`}>
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${disconnectedDBs.length > 0 ? 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.6)]' : 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]'}`}></div>
              <span className="text-[9px] uppercase font-mono tracking-widest text-[#888] whitespace-nowrap overflow-hidden text-ellipsis">Librarian {disconnectedDBs.length > 0 ? 'Degraded' : 'Active'}</span>
            </div>
          )}
          {!isDesktopMenuExpanded && (
            <div className={`mt-2 flex justify-center items-center h-4 w-4`}>
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${disconnectedDBs.length > 0 ? 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.6)]' : 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]'}`} title={`Librarian ${disconnectedDBs.length > 0 ? 'Degraded' : 'Active'}`}></div>
            </div>
          )}
        </div>
        
        <nav className="flex-1 py-4 flex flex-col space-y-1 overflow-x-hidden overflow-y-auto w-full px-2" style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
          {isDesktopMenuExpanded ? (
            <div className="px-3 py-2 text-[10px] font-semibold text-[#888] uppercase tracking-wider whitespace-nowrap">Navigation</div>
          ) : (
            <div className="h-6"></div>
          )}
          {[
            { id: 'Mission Control', icon: '🎛️'},
            { id: 'Leaderboard', icon: '📊'},
            { id: 'FVG Scanner', icon: '📡'},
            { id: 'Historical Search', icon: '🔍'},
            { id: 'Technical Chart', icon: '📈'},
            { id: 'Sector Flow', icon: '🚥'},
            { id: 'Value Ranker', icon: '🎯'},
            { id: 'Reversion Engine', icon: '🌀'},
            { id: 'Ghost Simulator', icon: '👻'},
            { id: 'Multibagger Matrix', icon: '🚀'},
            { id: 'Inst. DOM', icon: '🧱'},
            { id: 'Parquet Lake', icon: '🌊'},
            { id: 'Tools & Sync', icon: '⚙️'}
          ].map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setIsMobileMenuOpen(false);
                }}
                className={`w-full text-left flex items-center ${isDesktopMenuExpanded ? 'gap-3 px-3' : 'justify-center px-0'} py-2 rounded text-sm transition-colors ${
                  isActive 
                    ? `bg-${settings.accentColor}-500/20 text-${settings.accentColor}-300` 
                    : 'text-[#ccc] hover:bg-[#ffffff0a]'
                }`}
                title={!isDesktopMenuExpanded ? tab.id : undefined}
              >
                <span className="text-xl leading-none flex items-center justify-center">{tab.icon}</span> 
                {isDesktopMenuExpanded && <span className="whitespace-nowrap">{tab.id}</span>}
              </button>
            );
          })}
          
          {isDesktopMenuExpanded && (
            <div className="pt-6 px-3 py-2 text-[10px] font-semibold text-[#888] flex justify-between items-center uppercase tracking-wider whitespace-nowrap overflow-hidden">
              <span>Data Health (Path-Proof)</span>
            </div>
          )}
          {isDesktopMenuExpanded && (
            <div className="space-y-2 mt-2 px-3">
              {Object.entries(librarian.health as Record<string, any>).map(([dbName, status]) => (
                <div key={dbName} className="flex justify-between items-center text-[10px] font-mono">
                  <span className="text-[#666] whitespace-nowrap overflow-hidden text-ellipsis mr-2">{dbName.toUpperCase()}</span> 
                  <span className="flex items-center gap-1.5 shrink-0">
                    <span className={status.connected ? "text-green-400" : "text-red-400"}>
                      {status.connected ? "CONN" : "DISC"}
                    </span>
                    <div className={`w-1.5 h-1.5 rounded-full ${status.connected ? 'bg-green-400' : 'bg-red-400'} ${settings.animations ? 'animate-pulse' : ''}`} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </nav>

        <div className={`mt-auto flex flex-col border-t border-[#ffffff1a] py-2 shrink-0`}>
          {/* Settings Tab pushed to the bottom before hardware usage */}
          <button
            onClick={() => {
               setActiveTab('Settings');
               setIsMobileMenuOpen(false);
            }}
            className={`w-[calc(100%-1rem)] mx-auto mb-2 flex items-center ${isDesktopMenuExpanded ? 'gap-3 px-3' : 'justify-center px-0'} py-2 rounded text-sm transition-colors ${
              activeTab === 'Settings' 
                ? `bg-[#ffffff1a] text-white` 
                : 'text-[#ccc] hover:bg-[#ffffff0a]'
            }`}
            title={!isDesktopMenuExpanded ? 'Settings' : undefined}
          >
            <SettingsIcon size={16} className="text-[#888]" /> 
            {isDesktopMenuExpanded && <span className="whitespace-nowrap">Settings Config</span>}
          </button>
          
          {isDesktopMenuExpanded ? (
            <div className={`px-2 pt-2 pb-2`}>
              <div className="text-[9px] text-[#888] font-mono mb-1 uppercase">Hardware: AMD A8-7410</div>
              <div className="h-1 w-full bg-[#333] rounded-full overflow-hidden">
                <div className={`h-full bg-${settings.accentColor}-500 w-[14%]`}></div>
              </div>
              <div className="flex justify-between mt-1 text-[9px] font-mono text-[#888]">
                <span>Mem: 14% / 8GB</span><span>CPU: Base</span>
              </div>
            </div>
          ) : (
             <div className="px-2 pt-2 pb-2 flex flex-col items-center">
                 <div className={`h-8 w-1 flex-col justify-end flex bg-[#333] rounded overflow-hidden`} title="Mem: 14% / 8GB / AMD A8-7410">
                     <div className={`w-full bg-${settings.accentColor}-500 h-[14%]`}></div>
                 </div>
             </div>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className={`flex-1 overflow-x-hidden overflow-y-auto flex flex-col ${densityClass}`}>
          <header className="flex justify-between items-center w-full gap-2 shrink-0 h-8 mb-1">
            <div className="flex items-center gap-3 h-full">
              {!isDesktopMenuExpanded && (
                <button 
                  className="hidden md:flex p-2 bg-[#ffffff0a] border border-[#ffffff1a] rounded hover:bg-[#ffffff15] text-[#fafafa] items-center justify-center transition-colors"
                  onClick={() => setIsDesktopMenuExpanded(true)}
                  title="Expand sidebar"
                >
                  <PanelLeft size={16} />
                </button>
              )}
              <button 
                className="md:hidden p-2 bg-[#ffffff0a] border border-[#ffffff1a] rounded hover:bg-[#ffffff15] text-[#fafafa] flex items-center justify-center transition-colors"
                onClick={() => setIsMobileMenuOpen(true)}
              >
                <div className="space-y-1.5 w-5">
                  <div className="w-full h-0.5 bg-current"></div>
                  <div className="w-full h-0.5 bg-current"></div>
                  <div className="w-3/4 h-0.5 bg-current"></div>
                </div>
              </button>
              <div className="flex flex-col justify-center">
                <h2 className={`text-base font-semibold leading-none`}>Quantitative Engine Dashboard</h2>
                <p className="text-xs text-[#888]">Librarian v3.2: Myra React Bridge</p>
              </div>
            </div>
          </header>

          {/* Path-Proof Streamlit UI Error Simulators */}
          {disconnectedDBs.map(([dbName, status]) => (
            <div key={dbName} className="bg-red-950/40 border border-red-500/50 p-4 rounded-lg flex items-start gap-3 shrink-0">
                <AlertCircle className="text-red-400 flex-shrink-0 mt-0.5" size={16} />
              <div>
                <h3 className="text-red-400 text-sm font-semibold mb-1">Error: Database Missing ({dbName})</h3>
                <p className="text-[#ccc] text-xs font-mono">{(status as any).error}</p>
              </div>
            </div>
          ))}

          <div className="flex-1">
            <div className="max-w-6xl">
              {activeTab === 'Mission Control' && <MissionControlView lib={librarian} navigateTo={setActiveTab} />}
              {activeTab === 'Leaderboard' && <LeaderboardView lib={librarian} />}
              {activeTab === 'FVG Scanner' && <FVGScannerView lib={librarian} />}
              {activeTab === 'Historical Search' && <HistoricalSearchView lib={librarian} />}
              {activeTab === 'Technical Chart' && <AdvancedChartView lib={librarian} initialSymbol={globalSelectedTicker} />}
              {activeTab === 'Sector Flow' && <SectorFlowView lib={librarian} />}
              {activeTab === 'Value Ranker' && <ValueRankerView lib={librarian} />}
              {activeTab === 'Reversion Engine' && <ReversionEngineView lib={librarian} onNavigate={(tab, symbol) => { setActiveTab(tab); if (symbol) setGlobalSelectedTicker(symbol); }} />}
              {activeTab === 'Ghost Simulator' && <GhostSimulatorView lib={librarian} />}
              {activeTab === 'Multibagger Matrix' && <MultibaggerMatrixView lib={librarian} />}
              {activeTab === 'Inst. DOM' && <InstDOMView lib={librarian} />}
              {activeTab === 'Parquet Lake' && <DataLakeView lib={librarian} />}
              {activeTab === 'AEON Model' && <AIAnalysisView lib={librarian} />}
              {activeTab === 'Tools & Sync' && <ToolsView lib={librarian} />}
              {activeTab === 'Settings' && <SettingsView />}
            </div>
          </div>
        </div>

        {/* MYRA CLI Footer Replication */}
        <footer className={`h-10 ${bgFooter} border-t border-blue-500/30 shrink-0 flex items-center justify-between px-4 font-mono text-[11px] shadow-[0_-2px_10px_rgba(59,130,246,0.05)] transition-colors`}>
          <div className="flex items-center gap-4">
            <div>
              <span className={`text-${settings.accentColor}-400 font-bold`}>DB: </span>
              <span className="text-white">Connected (Core)</span>
              <span className="text-[#888]"> (4.2GB)</span>
            </div>
            <div className="text-[#555]">|</div>
            <div className="flex items-center gap-2">
              <span className="text-white font-bold">Status: </span>
              <span className="text-cyan-400">Sync: background_vacuum (100%)</span>
              <span className="text-white font-bold ml-2">AI-Trend: </span>
              <span className="text-green-400">BULLISH</span>
            </div>
          </div>
          
          <div className="flex items-center gap-3">
            <span className="text-cyan-400 font-bold">BCOPY(20/4)</span>
            <span className="text-[#555]">|</span>
            <span className="text-fuchsia-400 font-bold flex items-center gap-1.5">
              INST(<span className="text-green-400">LIVE</span>)
              <div className={`w-1.5 h-1.5 rounded-full bg-green-400 ml-0.5 ${settings.animations ? 'animate-pulse' : ''}`}></div>
            </span>
          </div>
        </footer>
      </main>
    </div>
  );
}
