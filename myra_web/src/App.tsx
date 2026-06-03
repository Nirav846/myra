import { useState, useEffect } from 'react';
import LeaderboardView from './views/Leaderboard';
import FVGScannerView from './views/FVGScanner';
import AIAnalysisView from './views/AIAnalysis';
import DataLakeView from './views/DataLake';

import MissionControlView from './views/MissionControl';
import SettingsView from './views/Settings';
import HistoricalSearchView from './views/HistoricalSearch';
import SectorFlowView from './views/SectorFlow';
import GhostSimulatorView from './views/GhostSimulator';
import MultibaggerMatrixView from './views/MultibaggerMatrix';
import InstDOMView from './views/InstDOM';
import FiiDiiScannerView from './views/FiiDiiScanner';
import PriceDeliveryDivergenceScannerView from './views/PriceDeliveryDivergenceScanner';
import AdvancedChartView from './views/AdvancedChart';
import ReversionEngineView from './views/ReversionEngine';
import ValueRankerView from './views/ValueRanker';
import { getLibrarian } from './lib/Librarian';
import { API_ROOT, API_BASE } from '../config';
import { useSettings } from './lib/SettingsContext';
import { useHealthStatus } from './hooks/useHealthStatus';
import { AlertManager } from './lib/AlertManager';
import { DebugPanel } from './components/DebugPanel';
import { SavedWorkspaces } from './components/SavedWorkspaces';
import ScannerPresetsPanel from './components/ScannerPresetsPanel';
import Navbar from './components/Navbar';
import MLLabView from './views/MLLabView';
import LaunchpadScannerView from './views/LaunchpadScanner';
import DataSyncView from './views/DataSync';
import DeliveryAnomalyScannerView from './views/DeliveryAnomalyScanner';
import { AlertCircle, Settings as SettingsIcon, SlidersHorizontal, BrainCircuit, Rocket, Database, RotateCw } from 'lucide-react';
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom';

const TABS = [
  { id: 'Mission Control', path: '/mission-control', icon: '🎛️'},
  { id: 'ML Lab', path: '/ml-lab', icon: <BrainCircuit size={18} />},
  { id: 'Launchpad Scanner', path: '/launchpad-scanner', icon: <Rocket size={18} />},
  { id: 'Leaderboard', path: '/leaderboard', icon: '📊'},
  { id: 'Price-Delivery Divergence', path: '/price-delivery-divergence', icon: '📉'},
  { id: 'FVG Scanner', path: '/fvg-scanner', icon: '📡'},
  { id: 'Historical Search', path: '/historical-search', icon: '🔍'},
  { id: 'Technical Chart', path: '/chart', icon: '📈'},
  { id: 'Sector Flow', path: '/sector-flow', icon: '🚥'},
  { id: 'Reversion Engine', path: '/reversion-engine', icon: '🌀'},
  { id: 'Ghost Simulator', path: '/ghost-simulator', icon: '👻'},
  { id: 'Multibagger Matrix', path: '/multibagger-matrix', icon: '🚀'},
  { id: 'Value Ranker', path: '/value-ranker', icon: '🎯'},
  { id: 'Delivery Volume Profile', path: '/inst-dom', icon: '🧱'},
  { id: 'FII/DII Scanner', path: '/fii-dii-scanner', icon: '🏢'},
  { id: 'Parquet Lake', path: '/parquet-lake', icon: '🌊'},
  { id: 'Data Sync', path: '/data-sync', icon: <Database size={18} />},
  { id: 'Delivery Anomaly', path: '/delivery-anomaly', icon: '📦'},
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



export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const activeTab = TABS.find(t => location.pathname.startsWith(t.path))?.id || 
                    (location.pathname === '/settings' ? 'Settings' : 'Mission Control');

  const [globalSelectedTicker, setGlobalSelectedTicker] = useState<string | undefined>();
  const [showPresetsPanel, setShowPresetsPanel] = useState(false);
  
  const { settings } = useSettings();
  const { health, coverage, isConnected } = useHealthStatus();
  
  const [pipelineStatus, setPipelineStatus] = useState<any>(null);
  const [toolsStatus, setToolsStatus] = useState<any>(null);
  const [dbSize, setDbSize] = useState<string>("N/A");
  const [logs, setLogs] = useState<string[]>(["[SYSTEM] Offline mode – no logs"]);

  const fetchLiveData = async () => {
    if (!isConnected) {
      setLogs(["[SYSTEM] Offline mode – no logs"]);
      return;
    }
    try {
      const [statusRes, pipelineRes, sizeRes, logsRes] = await Promise.all([
        fetch(`${API_ROOT}/api/tools/status`),
        fetch(`${API_ROOT}/api/tools/status`),
        fetch(`${API_ROOT}/api/db-size`),
        fetch(`${API_ROOT}/api/logs/recent`)
      ]);

      if (statusRes.ok) {
        const data = await statusRes.json();
        setPipelineStatus(data);
      }
      if (pipelineRes.ok) {
        const data = await pipelineRes.json();
        setToolsStatus(data);
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

  // Fetch on startup and on reconnect
  useEffect(() => {
    fetchLiveData();
  }, [isConnected]);

  // Compute disconnected DBs
  const disconnectedDBs = Object.entries(health as Record<string, HealthStatus>).filter(([_, status]) => !status.connected);

  // Dynamic Theme mappings based on SettingsContext
  const bgMain = settings.theme === 'pitch-black' ? 'bg-[#000000]' : 'bg-[#0e1117]';
  const bgFooter = settings.theme === 'pitch-black' ? 'bg-[#050505]' : 'bg-[#0e1117]';
  const densityClass = settings.density === 'compact' ? 'p-3 gap-3 text-sm' : 'p-6 gap-6 text-base';
  
  const accent = ACCENT_MAP[settings.accentColor] || ACCENT_MAP['indigo'];

  return (
    <div className={`flex h-screen w-full ${bgMain} text-[#fafafa] font-sans overflow-hidden transition-colors relative`}>
      <AlertManager />
      <DebugPanel />
      {showPresetsPanel && (
        <ScannerPresetsPanel
          onClose={() => setShowPresetsPanel(false)}
          onLoad={(preset) => {
            let path = '/';
            if (preset.module === 'ReversionEngine') path = '/reversion-engine';
            else if (preset.module === 'MultibaggerMatrix') path = '/multibagger-matrix';
            else if (preset.module === 'PriceDeliveryDivergence') path = '/price-delivery-divergence';
            else if (preset.module === 'ValueRanker') path = '/value-ranker';
            navigate(path);
            setShowPresetsPanel(false);
          }}
        />
      )}
      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className={`flex-1 overflow-x-hidden overflow-y-auto flex flex-col ${densityClass}`}>
          <header className="flex justify-between items-center w-full gap-2 shrink-0 h-8 mb-1">
            <div className="flex items-center gap-2 h-full">
              <div className="flex items-baseline gap-2">
                <h2 className="text-base font-semibold leading-none">Quantitative Engine Dashboard</h2>
                <p className="text-[10px] text-[#888] hidden sm:block">Librarian v3.2: Myra React Bridge</p>
              </div>
            </div>
            <div className="px-4 flex items-center gap-2">
              <button
                onClick={() => navigate('/settings')}
                className={`flex items-center gap-2 px-3 py-1.5 text-[10px] rounded font-mono transition-colors ${
                  activeTab === 'Settings'
                    ? 'bg-[#ffffff1a] text-white border border-[#ffffff3a]'
                    : 'bg-[#ffffff0a] border border-[#ffffff1a] text-[#888] hover:text-white'
                }`}
                title="Settings"
              >
                <SettingsIcon size={14} />
                <span className="hidden sm:inline">Settings</span>
              </button>
              <button
                onClick={() => setShowPresetsPanel(true)}
                className="flex items-center gap-2 px-3 py-1.5 text-[10px] bg-[#ffffff0a] border border-[#ffffff1a] rounded font-mono text-[#888] hover:text-white transition-colors"
                title="Scanner Presets"
              >
                <SlidersHorizontal size={14} />
                <span className="hidden sm:inline">Presets</span>
              </button>
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

          <Navbar tabs={TABS} />
          <div className="flex-1">
            <Routes>
                <Route path="/mission-control" element={<MissionControlView lib={librarian} navigateTo={(tab) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                }} />} />
                <Route path="/ml-lab" element={<MLLabView lib={librarian} />} />
                <Route path="/launchpad-scanner" element={<LaunchpadScannerView lib={librarian} onNavigate={(tab, symbol) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(`${target.path}?symbol=${symbol}`);
                }} />} />
                <Route path="/leaderboard" element={<LeaderboardView lib={librarian} />} />
                <Route path="/price-delivery-divergence" element={<PriceDeliveryDivergenceScannerView lib={librarian} onNavigate={(tab, symbol) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                  if (symbol) setGlobalSelectedTicker(symbol);
                }} />} />
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
                <Route path="/value-ranker" element={<ValueRankerView lib={librarian} onNavigate={(tab, symbol) => { 
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                  if (symbol) setGlobalSelectedTicker(symbol); 
                }} />} />
                <Route path="/inst-dom" element={<InstDOMView lib={librarian} />} />
                <Route path="/fii-dii-scanner" element={<FiiDiiScannerView lib={librarian} />} />
                <Route path="/parquet-lake" element={<DataLakeView lib={librarian} />} />
                <Route path="/settings" element={<SettingsView />} />
                <Route path="/data-sync" element={<DataSyncView />} />
                <Route path="/delivery-anomaly" element={<DeliveryAnomalyScannerView lib={librarian} onNavigate={(tab, symbol) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                  if (symbol) setGlobalSelectedTicker(symbol);
                }} />} />

                {/* Fallback */}
                <Route path="*" element={<Navigate to="/mission-control" replace />} />
              </Routes>
          </div>
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
              <span className="text-white font-bold">Data last synced: </span>
              <span className="text-cyan-400">{(() => {
                if (!toolsStatus) return 'N/A';
                const dates = Object.values(toolsStatus as Record<string, string>)
                  .filter((v) => typeof v === 'string' && v !== 'Never' && v.length > 4);
                if (dates.length === 0) return 'Never';
                const newest = dates.sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
                const diffMs = Date.now() - new Date(newest).getTime();
                const hours = Math.floor(diffMs / 3600000);
                return hours < 1 ? '<1h ago' : `${hours}h ago`;
              })()}</span>
              <button onClick={fetchLiveData} className="p-1 text-[#888] hover:text-white transition-colors" title="Refresh">
                <RotateCw size={12} />
              </button>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}
