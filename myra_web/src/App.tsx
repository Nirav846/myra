import { useState, useEffect } from 'react';
import LeaderboardView from './views/Leaderboard';
import FVGScannerView from './views/FVGScanner';
import AIAnalysisView from './views/AIAnalysis';
import DataLakeView from './views/DataLake';
import ToolsView from './views/Tools';
import MissionControlView from './views/MissionControl';
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
import { useHealthStatus } from './hooks/useHealthStatus';
import { AlertManager } from './lib/AlertManager';
import { DebugPanel } from './components/DebugPanel';
import { SavedWorkspaces } from './components/SavedWorkspaces';
import { AlertCircle, Terminal, Command, Settings as SettingsIcon, PanelLeftClose, PanelLeft } from 'lucide-react';
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom';

const TABS = [
  { id: 'Mission Control', path: '/mission-control', icon: '🎛️'},
  { id: 'Leaderboard', path: '/leaderboard', icon: '📊'},
  { id: 'FVG Scanner', path: '/fvg-scanner', icon: '📡'},
  { id: 'Historical Search', path: '/historical-search', icon: '🔍'},
  { id: 'Technical Chart', path: '/chart', icon: '📈'},
  { id: 'Sector Flow', path: '/sector-flow', icon: '🚥'},
  { id: 'Reversion Engine', path: '/reversion-engine', icon: '🌀'},
  { id: 'Ghost Simulator', path: '/ghost-simulator', icon: '👻'},
  { id: 'Multibagger Matrix', path: '/multibagger-matrix', icon: '🚀'},
  { id: 'Inst. DOM', path: '/inst-dom', icon: '🧱'},
  { id: 'Parquet Lake', path: '/parquet-lake', icon: '🌊'},
  { id: 'Tools & Sync', path: '/tools', icon: '⚙️'}
];

const ACCENT_MAP: Record<string, { bg600: string; bg50020: string; text300: string; bg500: string; text400: string }> = {
  indigo: { bg600: 'bg-indigo-600', bg50020: 'bg-indigo-500/20', text300: 'text-indigo-300', bg500: 'bg-indigo-500', text400: 'text-indigo-400' },
  cyan: { bg600: 'bg-cyan-600', bg50020: 'bg-cyan-500/20', text300: 'text-cyan-300', bg500: 'bg-cyan-500', text400: 'text-cyan-400' },
  fuchsia: { bg600: 'bg-fuchsia-600', bg50020: 'bg-fuchsia-500/20', text300: 'text-fuchsia-300', bg500: 'bg-fuchsia-500', text400: 'text-fuchsia-400' },
  green: { bg600: 'bg-green-600', bg50020: 'bg-green-500/20', text300: 'text-green-300', bg500: 'bg-green-500', text400: 'text-green-400' },
};

const librarian = getLibrarian();

interface HealthStatus {
  connected: boolean;
  error?: string;
  count?: number;
}

const API_BASE = 'http://localhost:8000';

function formatDate(dateStr: string) {
  if (!dateStr || dateStr === 'Never') return 'Never';
  try {
    const d = new Date(dateStr);
    const day = d.getDate().toString().padStart(2, '0');
    const month = (d.getMonth() + 1).toString().padStart(2, '0');
    return `${day}/${month}`;
  } catch {
    return 'Never';
  }
}

function isToday(dateStr: string) {
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
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const activeTab = TABS.find(t => location.pathname.startsWith(t.path))?.id || 
                    (location.pathname === '/settings' ? 'Settings' : 'Mission Control');

  const [globalSelectedTicker, setGlobalSelectedTicker] = useState<string | undefined>();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isDesktopMenuExpanded, setIsDesktopMenuExpanded] = useState(false);
  
  const { settings } = useSettings();
  const { health, isConnected } = useHealthStatus();
  
  const [pipelineStatus, setPipelineStatus] = useState<any>(null);
  const [dbSize, setDbSize] = useState<string>("N/A");
  const [logs, setLogs] = useState<string[]>(["[SYSTEM] Offline mode – no logs"]);

  // Fetch Live Data
  useEffect(() => {
    const fetchLiveData = async () => {
      if (!isConnected) {
        setLogs(["[SYSTEM] Offline mode – no logs"]);
        return;
      }
      try {
        const [statusRes, sizeRes, logsRes] = await Promise.all([
          fetch(`${API_BASE}/api/tools/status`),
          fetch(`${API_BASE}/api/db-size`),
          fetch(`${API_BASE}/api/logs/recent`)
        ]);

        if (statusRes.ok) {
          const data = await statusRes.json();
          setPipelineStatus(data);
        }
        if (sizeRes.ok) {
          const data = await sizeRes.json();
          setDbSize(`${(data.size_mb / 1024).toFixed(1)}GB`);
        } else {
          setDbSize("N/A");
        }
        if (logsRes.ok) {
          const data = await logsRes.json();
          setLogs(data.logs);
        }
      } catch (err) {
        console.warn("Backend not reachable for live stats.");
        setDbSize("N/A");
        setLogs(["[SYSTEM] Offline mode – no logs"]);
      }
    };

    fetchLiveData();
    const intervalTime = 30000;
    const interval = setInterval(() => {
      if (!document.hidden) fetchLiveData();
    }, intervalTime);

    return () => clearInterval(interval);
  }, [isConnected]);

  // Compute disconnected DBs
  const disconnectedDBs = Object.entries(health as Record<string, HealthStatus>).filter(([_, status]) => !status.connected);

  // Dynamic Theme mappings based on SettingsContext
  const bgMain = settings.theme === 'pitch-black' ? 'bg-[#000000]' : 'bg-[#0e1117]';
  const bgSidebar = settings.theme === 'pitch-black' ? 'bg-[#0a0a0a]' : 'bg-[#262730]';
  const bgFooter = settings.theme === 'pitch-black' ? 'bg-[#050505]' : 'bg-[#0e1117]';
  const densityClass = settings.density === 'compact' ? 'p-3 gap-3 text-sm' : 'p-6 gap-6 text-base';
  
  const accent = ACCENT_MAP[settings.accentColor] || ACCENT_MAP['indigo'];

  return (
    <div className={`flex h-screen w-full ${bgMain} text-[#fafafa] font-sans overflow-hidden transition-colors relative`}>
      <AlertManager />
      <DebugPanel />
      {isMobileMenuOpen && (
        <div className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
      )}
      {/* Sidebar Navigation */}
      <aside className={`${isDesktopMenuExpanded ? 'w-64' : 'w-16'} ${bgSidebar} flex flex-col border-[#ffffff1a] absolute md:relative z-40 h-full !flex transform transition-all duration-300 ${settings.sidebarPosition === 'Right' ? 'border-l order-last right-0' : 'border-r left-0'} ${isMobileMenuOpen ? 'translate-x-0' : (settings.sidebarPosition === 'Right' ? 'translate-x-full md:translate-x-0' : '-translate-x-full md:translate-x-0')}`}>
        <div className={`p-2 border-b border-[#ffffff1a] flex flex-col items-center`}>
          <div className={`flex items-center justify-between w-full h-6 ${!isDesktopMenuExpanded && 'justify-center'}`}>
            {isDesktopMenuExpanded && (
              <div className="flex items-center gap-2 overflow-hidden">
                <div className={`w-6 h-6 rounded-md flex items-center justify-center font-bold italic text-white flex-shrink-0 transition-colors ${settings.animations ? 'hover:scale-105' : ''} ${accent.bg600}`}>
                  <Command size={14} />
                </div>
                <h1 className="text-sm font-bold tracking-tight whitespace-nowrap">MYRA <span className={`${accent.text400}`}>v3.2</span></h1>
              </div>
            )}
            {!isDesktopMenuExpanded && (
               <div className={`w-6 h-6 rounded-md flex items-center justify-center font-bold italic text-white flex-shrink-0 transition-colors ${settings.animations ? 'hover:scale-105' : ''} ${accent.bg600}`}>
                 <Command size={14} />
               </div>
            )}
            <div className="flex items-center gap-1">
              {isDesktopMenuExpanded && (
                <button className="hidden md:flex p-1 text-[#888] hover:text-white transition-colors" onClick={() => setIsDesktopMenuExpanded(false)} title="Collapse sidebar">
                  <PanelLeftClose size={14} />
                </button>
              )}
              <button className="md:hidden text-[#888] hover:text-white" onClick={() => setIsMobileMenuOpen(false)}>✕</button>
            </div>
          </div>
          {isDesktopMenuExpanded && (
            <div className={`flex items-center gap-1.5 mt-2 px-2 py-1 ${bgMain} rounded border border-[#ffffff1a] w-full`}>
              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${disconnectedDBs.length > 0 ? 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.6)]' : 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]'}`}></div>
              <span className="text-[9px] uppercase font-mono tracking-widest text-[#888] whitespace-nowrap overflow-hidden text-ellipsis">Librarian {disconnectedDBs.length > 0 ? 'Degraded' : 'Active'}</span>
            </div>
          )}
          {!isDesktopMenuExpanded && (
            <div className={`mt-2 flex justify-center items-center h-2 w-2`}>
              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${disconnectedDBs.length > 0 ? 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.6)]' : 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]'}`} title={`Librarian ${disconnectedDBs.length > 0 ? 'Degraded' : 'Active'}`}></div>
            </div>
          )}
        </div>
        
        <nav className="flex-1 py-4 flex flex-col space-y-1 overflow-x-hidden overflow-y-auto w-full px-2" style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
          {isDesktopMenuExpanded ? (
            <div className="px-3 py-2 text-[10px] font-semibold text-[#888] uppercase tracking-wider whitespace-nowrap">Navigation</div>
          ) : (
            <div className="h-6"></div>
          )}
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => {
                  navigate(tab.path);
                  setIsMobileMenuOpen(false);
                }}
                className={`w-full text-left flex items-center ${isDesktopMenuExpanded ? 'gap-3 px-3' : 'justify-center px-0'} py-2 rounded text-sm transition-colors ${
                  isActive 
                    ? `${accent.bg50020} ${accent.text300}` 
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
              {Object.entries(librarian.health as Record<string, HealthStatus>).map(([dbName, status]) => (
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
               navigate('/settings');
               setIsMobileMenuOpen(false);
            }}
            className={`w-[calc(100%-1rem)] mx-auto mb-2 flex items-center ${isDesktopMenuExpanded ? 'gap-3 px-3' : 'justify-center px-0'} py-2 rounded text-sm transition-colors ${
              activeTab === 'Settings' 
                ? `bg-[#ffffff1a] text-white` 
                : 'text-[#ccc] hover:bg-[#ffffff0a]'
            }`}
            title={!isDesktopMenuExpanded ? 'Settings' : undefined}
          >
            <SettingsIcon size={18} className="text-[#888]" /> 
            {isDesktopMenuExpanded && <span className="whitespace-nowrap">Settings Config</span>}
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className={`flex-1 overflow-x-hidden overflow-y-auto flex flex-col ${densityClass}`}>
          <header className="flex justify-between items-center w-full gap-2 shrink-0 h-8 mb-1">
            <div className="flex items-center gap-2 h-full">
              {!isDesktopMenuExpanded && (
                <button 
                  className="hidden md:flex p-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded hover:bg-[#ffffff15] text-[#fafafa] items-center justify-center transition-colors"
                  onClick={() => setIsDesktopMenuExpanded(true)}
                  title="Expand sidebar"
                >
                  <PanelLeft size={16} />
                </button>
              )}
              <button 
                className="md:hidden p-1.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded hover:bg-[#ffffff15] text-[#fafafa] flex items-center justify-center transition-colors"
                onClick={() => setIsMobileMenuOpen(true)}
              >
                <div className="space-y-1 w-4">
                  <div className="w-full h-0.5 bg-current"></div>
                  <div className="w-full h-0.5 bg-current"></div>
                  <div className="w-3/4 h-0.5 bg-current"></div>
                </div>
              </button>
              <div className="flex items-baseline gap-2">
                <h2 className="text-base font-semibold leading-none">Quantitative Engine Dashboard</h2>
                <p className="text-[10px] text-[#888] hidden sm:block">Librarian v3.2: Myra React Bridge</p>
              </div>
            </div>
            <div className="px-4">
              <SavedWorkspaces />
            </div>
          </header>

          {/* Path-Proof Streamlit UI Error Simulators */}
          {disconnectedDBs.map(([dbName, status]) => (
            <div key={dbName} className="bg-red-950/40 border border-red-500/50 p-4 rounded-lg flex items-start gap-3 shrink-0">
              <AlertCircle className="text-red-400 flex-shrink-0 mt-0.5" size={18} />
              <div>
                <h3 className="text-red-400 text-sm font-semibold mb-1">Error: Database Missing ({dbName})</h3>
                <p className="text-[#ccc] text-xs font-mono">{status.error || 'Unknown Error'}</p>
              </div>
            </div>
          ))}

          <div className="flex-1">
            <div className="max-w-6xl">
              <Routes>
                <Route path="/mission-control" element={<MissionControlView lib={librarian} navigateTo={(tab) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                }} />} />
                <Route path="/leaderboard" element={<LeaderboardView lib={librarian} />} />
                <Route path="/fvg-scanner" element={<FVGScannerView lib={librarian} />} />
                <Route path="/historical-search" element={<HistoricalSearchView lib={librarian} />} />
                <Route path="/chart" element={<AdvancedChartView lib={librarian} activeSymbol={globalSelectedTicker} />} />
                <Route path="/sector-flow" element={<SectorFlowView lib={librarian} />} />
                <Route path="/reversion-engine" element={<ReversionEngineView lib={librarian} onNavigate={(tab, symbol) => { 
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                  if (symbol) setGlobalSelectedTicker(symbol); 
                }} />} />
                <Route path="/ghost-simulator" element={<GhostSimulatorView lib={librarian} />} />
                <Route path="/multibagger-matrix" element={<MultibaggerMatrixView lib={librarian} />} />
                <Route path="/inst-dom" element={<InstDOMView lib={librarian} />} />
                <Route path="/parquet-lake" element={<DataLakeView lib={librarian} />} />
                <Route path="/settings" element={<SettingsView />} />
                <Route path="/tools" element={<ToolsView lib={librarian} />} />
                
                {/* Fallback */}
                <Route path="*" element={<Navigate to="/mission-control" replace />} />
              </Routes>
            </div>
          </div>
          
          {/* Terminal Logs */}
          {isDesktopMenuExpanded && (
            <div className={`shrink-0 h-24 ${bgSidebar} border-t border-[#ffffff1a] p-2 flex flex-col font-mono text-[10px] text-[#aaa] overflow-y-auto`}>
              {logs.map((log, idx) => (
                <div key={idx} className="truncate">
                  {log}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* MYRA CLI Footer Replication */}
        <footer className={`h-10 ${bgFooter} border-t border-blue-500/30 shrink-0 flex items-center justify-between px-4 font-mono text-[11px] shadow-[0_-2px_10px_rgba(59,130,246,0.05)] transition-colors`}>
          <div className="flex items-center gap-4">
            <div>
              <span className={`${accent.text400} font-bold`}>DB: </span>
              {isConnected ? 
                <span className="text-white">Connected (Core)</span> : 
                <span className="text-red-400">Degraded (Demo)</span>
              }
              <span className="text-[#888]"> ({dbSize})</span>
            </div>
            <div className="text-[#555]">|</div>
            <div className="flex items-center gap-2">
              <span className="text-white font-bold">Sync Defaults: </span>
              <span className="text-cyan-400">Fund: {pipelineStatus ? formatDate(pipelineStatus.fundamentals) : 'N/A'}</span>
              <span className="text-white font-bold ml-1">ETF: </span>
              <span className="text-cyan-400">{pipelineStatus ? formatDate(pipelineStatus.etf) : 'N/A'}</span>
              <span className="text-white font-bold ml-1">IDX: </span>
              <span className="text-cyan-400">{pipelineStatus ? formatDate(pipelineStatus.index) : 'N/A'}</span>
            </div>
          </div>
          
          <div className="flex items-center gap-3">
            <span className="text-white font-bold">BCOPY:</span>
            <span className="text-cyan-400 font-bold">
              {pipelineStatus ? formatDate(pipelineStatus.ingest) : 'N/A'}
            </span>
            <div className={`w-1.5 h-1.5 rounded-full ${pipelineStatus && isToday(pipelineStatus.ingest) ? 'bg-green-400' : 'bg-yellow-400'} ml-0.5 ${settings.animations ? 'animate-pulse' : ''}`}></div>
          </div>
        </footer>
      </main>
    </div>
  );
}
