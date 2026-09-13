import { useState, useEffect, lazy, Suspense } from 'react';
import { PageLoader } from './components/ui/PageLoader';

// Lazy load heavy views for performance optimization (Phase 5.1)
const LeaderboardView = lazy(() => import('./views/Leaderboard'));
const FVGScannerView = lazy(() => import('./views/FVGScanner'));
const DataLakeView = lazy(() => import('./views/DataLake'));

import MissionControlView from './views/MissionControl';
import SettingsView from './views/Settings';
const HistoricalSearchView = lazy(() => import('./views/HistoricalSearch'));
const SectorFlowView = lazy(() => import('./views/SectorFlow'));
const GhostSimulatorView = lazy(() => import('./views/GhostSimulator'));
const MultibaggerMatrixView = lazy(() => import('./views/MultibaggerMatrix'));
const InstDOMView = lazy(() => import('./views/InstDOM'));
const FiiDiiScannerView = lazy(() => import('./views/FiiDiiScanner'));
const PriceDeliveryDivergenceScannerView = lazy(() => import('./views/PriceDeliveryDivergenceScanner'));
import AdvancedChartView from './views/AdvancedChart';
const ReversionEngineView = lazy(() => import('./views/ReversionEngine'));
const ValueRankerView = lazy(() => import('./views/ValueRanker'));
const InvisibleHandScannerView = lazy(() => import('./views/InvisibleHandScanner'));
const TriggerScannerView = lazy(() => import('./views/TriggerScanner'));
import { getLibrarian } from './lib/Librarian';
import { API_ROOT } from './config';
import { useSettings } from './lib/SettingsContext';
import { useHealthStatus } from './hooks/useHealthStatus';
import { AlertManager } from './lib/AlertManager';
import { DebugPanel } from './components/DebugPanel';
import HealthStatusBar from './components/HealthStatusBar';
import { SavedWorkspaces } from './components/SavedWorkspaces';
import ScannerPresetsPanel from './components/ScannerPresetsPanel';
import Navbar from './components/Navbar';
import { LiveRegion } from './components/LiveRegion';
const MLLabView = lazy(() => import('./views/MLLabView'));
const LaunchpadScannerView = lazy(() => import('./views/LaunchpadScanner'));
const MultibaggerProScannerView = lazy(() => import('./views/MultibaggerProScanner'));
const DarvasBoxProScannerView = lazy(() => import('./views/DarvasBoxProScanner'));
const LiquidityFlipDetectorView = lazy(() => import('./views/LiquidityFlipDetector'));
const OperatorFingerprintScannerView = lazy(() => import('./views/OperatorFingerprintScanner'));
const FloatExhaustionScannerView = lazy(() => import('./views/FloatExhaustionScanner'));
const SeasonalDeliveryHarvesterView = lazy(() => import('./views/SeasonalDeliveryHarvester'));
const WyckoffAutomatonView = lazy(() => import('./views/WyckoffAutomaton'));
import PortfolioView from './views/PortfolioView';
const DataSyncView = lazy(() => import('./views/DataSync'));
const DeliveryAnomalyScannerView = lazy(() => import('./views/DeliveryAnomalyScanner'));
const BottomHunterView = lazy(() => import('./views/BottomHunter'));
const RecoveryLadderView = lazy(() => import('./views/RecoveryLadder'));
const SuperBreakoutView = lazy(() => import('./views/SuperBreakoutView'));
const ClimaxAccumulationView = lazy(() => import('./views/ClimaxAccumulation'));
const ConfluenceView = lazy(() => import('./views/ConfluenceView'));
const DCBBargainView = lazy(() => import('./views/DCBBargain'));
const SmartMoneyBargainView = lazy(() => import('./views/SmartMoneyBargain'));
const RRGView = lazy(() => import('./views/RRGView'));
const FundTractionReportView = lazy(() => import('./views/FundTractionReport'));
const FundTractionScannerView = lazy(() => import('./views/FundTractionScanner'));
const CrossBuyScannerView = lazy(() => import('./views/CrossBuyScanner'));
const NewsSentimentView = lazy(() => import('./views/NewsSentiment'));
const FundamentalsView = lazy(() => import('./views/FundamentalsView'));
const FullFundamentalsView = lazy(() => import('./views/FullFundamentalsView'));
import { AlertCircle, Settings as SettingsIcon, SlidersHorizontal, BrainCircuit, Rocket, Database, RotateCw, Eye, Zap } from 'lucide-react';
import { Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom';

const TABS = [
  { id: 'Mission Control', path: '/mission-control', icon: '🎛️', category: 'dashboard' },
  { id: 'Portfolio', path: '/portfolio', icon: '💰', category: 'dashboard' },
  { id: 'Bottom Hunter', path: '/bottom-hunter', icon: '🎯', category: 'scanners', group: 'Price Action' },
  { id: 'Recovery Ladder', path: '/recovery-ladder', icon: '🪜', category: 'scanners', group: 'Price Action' },
  { id: 'Super Breakout(MSK)', path: '/super-breakout', icon: '🚀', category: 'scanners', group: 'Price Action' },
  { id: 'DCB Bargain', path: '/dcb-bargain', icon: '🏷️', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Smart Money Bargain', path: '/smart-money-bargain', icon: '🏦', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Consensus', path: '/confluence', icon: '🔗', category: 'scanners', group: 'Overview' },
  { id: 'Invisible Hand', path: '/invisible-hand', icon: <Eye size={16} />, category: 'scanners', group: 'Institutional / Flow' },
  { id: 'The Trigger', path: '/trigger', icon: <Zap size={16} />, category: 'scanners', group: 'Price Action' },
  { id: 'Climax Accumulation', path: '/climax-accumulation', icon: '📊', category: 'scanners', group: 'Price Action' },
  { id: 'Wyckoff Auto', path: '/wyckoff', icon: '🤖', category: 'scanners', group: 'Price Action' },
  { id: 'Float Exhaustion', path: '/float-exhaustion', icon: '🪫', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Liquidity Flip', path: '/liquidity-flip', icon: '🔄', category: 'scanners', group: 'Price Action' },
  { id: 'Operator Fingerprint', path: '/operator-fingerprint', icon: '🔍', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Darvas Box Pro', path: '/darvas-box-pro', icon: '📦', category: 'scanners', group: 'Price Action' },
  { id: 'Multibagger Pro', path: '/multibagger-pro-scanner', icon: <Rocket size={18} />, category: 'scanners', group: 'ML / Momentum' },
  { id: 'Price-Delivery Divergence', path: '/price-delivery-divergence', icon: '📉', category: 'scanners', group: 'Delivery / Volume' },
  { id: 'Delivery Anomaly', path: '/delivery-anomaly', icon: '📦', category: 'scanners', group: 'Delivery / Volume' },
  { id: 'Seasonal Delivery', path: '/seasonal-delivery', icon: '📅', category: 'scanners', group: 'Delivery / Volume' },
  { id: 'Launchpad Scanner', path: '/launchpad-scanner', icon: <Rocket size={18} />, category: 'scanners', group: 'ML / Momentum' },
  { id: 'FII/DII Scanner', path: '/fii-dii-scanner', icon: '🏢', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Fund Traction', path: '/fund-traction', icon: '💰', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Cross-Buy', path: '/cross-buy', icon: '🤝', category: 'scanners', group: 'Institutional / Flow' },
  { id: 'Leaderboard', path: '/leaderboard', icon: '📊', category: 'scanners', group: 'Overview' },
  { id: 'FVG Scanner', path: '/fvg-scanner', icon: '📡', category: 'scanners', group: 'Price Action' },
  { id: 'Technical Chart', path: '/chart', icon: '📈', category: 'analysis' },
  { id: 'Fundamentals', path: '/fundamentals', icon: '📋', category: 'analysis' },
  { id: 'Deep Fundamentals', path: '/deep-fundamentals', icon: '🏛️', category: 'analysis' },
  { id: 'Delivery Volume Profile', path: '/inst-dom', icon: '🧱', category: 'analysis' },
  { id: 'Multibagger Matrix', path: '/multibagger-matrix', icon: '🚀', category: 'analysis' },
  { id: 'Value Ranker', path: '/value-ranker', icon: '🎯', category: 'analysis' },
  { id: 'News Sentiment', path: '/news-sentiment', icon: '📰', category: 'analysis' },
  { id: 'RRG', path: '/rrg', icon: '🎯', category: 'analysis' },
  { id: 'Historical Search', path: '/historical-search', icon: '🔍', category: 'analysis' },
  { id: 'Data Sync', path: '/data-sync', icon: <Database size={18} />, category: 'data' },
  { id: 'Parquet Lake', path: '/parquet-lake', icon: '🌊', category: 'data' },
  { id: 'Sector Flow', path: '/sector-flow', icon: '🚥', category: 'data' },
  { id: 'ML Lab', path: '/ml-lab', icon: <BrainCircuit size={18} />, category: 'data' },
  { id: 'Reversion Engine', path: '/reversion-engine', icon: '🌀', category: 'experimental' },
  { id: 'Ghost Simulator', path: '/ghost-simulator', icon: '👻', category: 'experimental' },
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
  
  const [toolsStatus, setToolsStatus] = useState<any>(null);
  const [dbSize, setDbSize] = useState<string>("N/A");
  const [logs, setLogs] = useState<string[]>(["[SYSTEM] Offline mode – no logs"]);

  const fetchLiveData = async () => {
    if (!isConnected) {
      setLogs(["[SYSTEM] Offline mode – no logs"]);
      return;
    }
    try {
      const [statusRes, sizeRes, logsRes] = await Promise.all([
        fetch(`${API_ROOT}/api/tools/status`),
        fetch(`${API_ROOT}/api/db-size`),
        fetch(`${API_ROOT}/api/logs/recent`)
      ]);

      if (statusRes.ok) {
        const data = await statusRes.json();
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
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <LiveRegion message="" priority="polite" />
      <HealthStatusBar />
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
      <main id="main-content" className="flex-1 flex flex-col h-screen overflow-hidden pt-9" role="main">
        <div className={`flex-1 overflow-x-hidden overflow-y-auto flex flex-col ${densityClass}`}>
          <header className="flex justify-between items-center w-full gap-2 shrink-0 h-8 mb-1">
            <div className="flex items-center gap-2 h-full">
              <div className="flex items-baseline gap-2">
                <h2 className="text-base font-semibold leading-none">Quantitative Engine Dashboard</h2>
                <p className="text-[12px] text-[#888] hidden sm:block">Librarian v3.2: Myra React Bridge</p>
              </div>
            </div>
            <div className="px-4 flex items-center gap-2">
              <button
                onClick={() => navigate('/settings')}
                className={`flex items-center gap-2 px-3 py-1.5 text-[12px] rounded font-mono transition-colors ${
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
                className="flex items-center gap-2 px-3 py-1.5 text-[12px] bg-[#ffffff0a] border border-[#ffffff1a] rounded font-mono text-[#888] hover:text-white transition-colors"
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
          <div className="flex-1 min-h-0">
            <Routes>
                <Route path="/mission-control" element={<MissionControlView lib={librarian} navigateTo={(tab) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(target.path);
                }} />} />
                <Route path="/portfolio" element={<PortfolioView />} />
                <Route path="/ml-lab" element={<LazyLoadView><MLLabView lib={librarian} /></LazyLoadView>} />
                <Route path="/launchpad-scanner" element={<LazyLoadView><LaunchpadScannerView lib={librarian} onNavigate={(tab, symbol) => {
                  const target = TABS.find(t => t.id === tab);
                  if (target) navigate(`${target.path}?symbol=${symbol}`);
                }} /></LazyLoadView>} />
                <Route path="/leaderboard" element={<LazyLoadView><LeaderboardView lib={librarian} /></LazyLoadView>} />
                <Route path="/price-delivery-divergence" element={<LazyLoadView><PriceDeliveryDivergenceScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/fvg-scanner" element={<LazyLoadView><FVGScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/historical-search" element={<LazyLoadView><HistoricalSearchView lib={librarian} /></LazyLoadView>} />
                <Route path="/chart" element={<AdvancedChartView lib={librarian} activeSymbol={globalSelectedTicker} />} />
                <Route path="/fundamentals" element={<LazyLoadView><FundamentalsView lib={librarian} /></LazyLoadView>} />
                <Route path="/deep-fundamentals" element={<LazyLoadView><FullFundamentalsView lib={librarian} /></LazyLoadView>} />
                <Route path="/sector-flow" element={<LazyLoadView><SectorFlowView lib={librarian} /></LazyLoadView>} />
                <Route path="/reversion-engine" element={<LazyLoadView><ReversionEngineView lib={librarian} /></LazyLoadView>} />
                <Route path="/ghost-simulator" element={<LazyLoadView><GhostSimulatorView lib={librarian} /></LazyLoadView>} />
                <Route path="/multibagger-matrix" element={<LazyLoadView><MultibaggerMatrixView lib={librarian} /></LazyLoadView>} />
                <Route path="/value-ranker" element={<LazyLoadView><ValueRankerView lib={librarian} /></LazyLoadView>} />
                <Route path="/darvas-box-pro" element={<LazyLoadView><DarvasBoxProScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/liquidity-flip" element={<LazyLoadView><LiquidityFlipDetectorView lib={librarian} /></LazyLoadView>} />
                <Route path="/operator-fingerprint" element={<LazyLoadView><OperatorFingerprintScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/float-exhaustion" element={<LazyLoadView><FloatExhaustionScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/seasonal-delivery" element={<LazyLoadView><SeasonalDeliveryHarvesterView lib={librarian} /></LazyLoadView>} />
                <Route path="/wyckoff" element={<LazyLoadView><WyckoffAutomatonView lib={librarian} /></LazyLoadView>} />
                <Route path="/inst-dom" element={<LazyLoadView><InstDOMView lib={librarian} /></LazyLoadView>} />
                <Route path="/fii-dii-scanner" element={<LazyLoadView><FiiDiiScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/fund-traction" element={<LazyLoadView><FundTractionScannerView /></LazyLoadView>} />
                <Route path="/cross-buy" element={<LazyLoadView><CrossBuyScannerView /></LazyLoadView>} />
                <Route path="/parquet-lake" element={<LazyLoadView><DataLakeView lib={librarian} /></LazyLoadView>} />
                <Route path="/invisible-hand" element={<LazyLoadView><InvisibleHandScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/trigger" element={<LazyLoadView><TriggerScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/settings" element={<SettingsView />} />
                <Route path="/data-sync" element={<LazyLoadView><DataSyncView /></LazyLoadView>} />
                <Route path="/delivery-anomaly" element={<LazyLoadView><DeliveryAnomalyScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/multibagger-pro-scanner" element={<LazyLoadView><MultibaggerProScannerView lib={librarian} /></LazyLoadView>} />
                <Route path="/bottom-hunter" element={<LazyLoadView><BottomHunterView lib={librarian} /></LazyLoadView>} />
                <Route path="/recovery-ladder" element={<LazyLoadView><RecoveryLadderView /></LazyLoadView>} />
                <Route path="/super-breakout" element={<LazyLoadView><SuperBreakoutView /></LazyLoadView>} />
                <Route path="/climax-accumulation" element={<LazyLoadView><ClimaxAccumulationView lib={librarian} /></LazyLoadView>} />
                <Route path="/dcb-bargain" element={<LazyLoadView><DCBBargainView lib={librarian} /></LazyLoadView>} />
                <Route path="/smart-money-bargain" element={<LazyLoadView><SmartMoneyBargainView /></LazyLoadView>} />
                <Route path="/news-sentiment" element={<LazyLoadView><NewsSentimentView /></LazyLoadView>} />
                <Route path="/confluence" element={<LazyLoadView><ConfluenceView /></LazyLoadView>} />
                <Route path="/rrg" element={<LazyLoadView><RRGView /></LazyLoadView>} />
                <Route path="/fund-traction-report" element={<LazyLoadView><FundTractionReportView /></LazyLoadView>} />

                {/* Fallback */}
                <Route path="*" element={<Navigate to="/mission-control" replace />} />
              </Routes>
          </div>
        </div>

        {/* MYRA CLI Footer Replication */}
        <footer className={`h-10 ${bgFooter} border-t border-blue-500/30 shrink-0 flex items-center justify-between px-4 font-mono text-[12px] shadow-[0_-2px_10px_rgba(59,130,246,0.05)] transition-colors`}>
          <div className="flex items-center gap-4">
            <div>
              <span className={`${accent.text400} font-bold`}>DB: </span>
              {isConnected ? 
                <span className="text-white">Connected (Core)</span> : 
                <span className="text-red-400">Degraded (Demo)</span>
              }
              <span className="text-[#888]"> ({dbSize})</span>
            </div>
            <div className="text-[#888]">|</div>
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
