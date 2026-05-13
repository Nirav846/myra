import { useSettings, Theme, AccentColor, Density, ChartRange, CandlestickStyle, ChartHeight, AutoRefreshInterval, FontSize, FontFamily, SidebarPosition } from '../lib/SettingsContext';
import { Settings, Palette, Moon, Monitor, LayoutGrid, CheckCircle2, Server, BarChart2, Bell, Bug, Zap } from 'lucide-react';

export default function SettingsView() {
  const { settings, updateSettings } = useSettings();

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded-lg flex flex-col shadow-xl overflow-y-auto max-w-6xl mx-auto h-full">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24] sticky top-0 z-10">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Settings size={20} className="text-[#888]" />
          Dashboard Configuration
        </h3>
        <span className="text-xs text-[#888] font-mono">~/.config/myra/ui.json</span>
      </div>

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-8 text-sm pb-16">
        
        {/* Visual Theme Section */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Palette size={16} /> Visual Aesthetics
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Environment Theme</label>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'myra-dark' as Theme, label: 'Myra Default', icon: <Monitor size={16}/> },
                { id: 'pitch-black' as Theme, label: 'OLED Black', icon: <Moon size={16}/> }
              ].map(theme => (
                <button
                  key={theme.id}
                  onClick={() => updateSettings({ theme: theme.id })}
                  className={`flex flex-col items-center justify-center p-4 rounded border transition-colors relative ${settings.theme === theme.id ? 'bg-[#ffffff10] border-blue-500' : 'bg-[#0e1117] border-[#ffffff1a] hover:border-[#ffffff3a]'}`}
                >
                  {settings.theme === theme.id && <CheckCircle2 size={14} className="absolute top-2 right-2 text-blue-400" />}
                  {theme.icon}
                  <span className="mt-2 font-medium">{theme.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 pt-2">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Accent Color</label>
            <div className="flex gap-3">
              {[
                { id: 'indigo' as AccentColor, color: 'bg-indigo-500' },
                { id: 'cyan' as AccentColor, color: 'bg-cyan-500' },
                { id: 'fuchsia' as AccentColor, color: 'bg-fuchsia-500' },
                { id: 'green' as AccentColor, color: 'bg-green-500' },
              ].map(accent => (
                <button
                  key={accent.id}
                  onClick={() => updateSettings({ accentColor: accent.id })}
                  className={`w-10 h-10 rounded-full flex items-center justify-center border-2 transition-transform ${settings.accentColor === accent.id ? `border-white scale-110 shadow-[0_0_10px_rgba(255,255,255,0.2)]` : 'border-transparent hover:scale-105'} ${accent.color}`}
                >
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* Layout & Behavior Section */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <LayoutGrid size={16} /> Layout & Typography
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Data Density</label>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'comfortable' as Density, label: 'Comfortable (Default)' },
                { id: 'compact' as Density, label: 'Compact (High Density)' }
              ].map(density => (
                <button
                  key={density.id}
                  onClick={() => updateSettings({ density: density.id })}
                  className={`px-4 py-3 rounded border text-left transition-colors relative ${settings.density === density.id ? 'bg-[#ffffff10] border-blue-500' : 'bg-[#0e1117] border-[#ffffff1a] hover:border-[#ffffff3a]'}`}
                >
                  {settings.density === density.id && <CheckCircle2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-blue-400" />}
                  <span className="font-medium block">{density.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 pt-2">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Sidebar Position</label>
            <div className="flex gap-2 flex-wrap">
              {(['Left', 'Right'] as SidebarPosition[]).map(pos => (
                <button
                  key={pos}
                  onClick={() => updateSettings({ sidebarPosition: pos })}
                  className={`px-3 py-1.5 rounded border text-sm font-mono transition-colors ${
                    settings.sidebarPosition === pos ? 'border-blue-500 bg-blue-500/10 text-blue-400' : 'border-[#ffffff1a] bg-[#0e1117] hover:border-[#ffffff3a]'
                  }`}
                >
                  {pos}
                </button>
              ))}
            </div>
          </div>

        </section>

        {/* Chart Preferences */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <BarChart2 size={16} /> Charting
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Default Chart Range</label>
            <div className="flex gap-2 flex-wrap">
              {(['1M', '3M', '6M', '1Y', 'All'] as ChartRange[]).map(r => (
                <button
                  key={r}
                  onClick={() => updateSettings({ defaultChartRange: r })}
                  className={`px-3 py-1.5 rounded border text-sm font-mono transition-colors ${
                    settings.defaultChartRange === r ? 'border-blue-500 bg-blue-500/10 text-blue-400' : 'border-[#ffffff1a] bg-[#0e1117] hover:border-[#ffffff3a]'
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3">
             <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Candlestick Style</label>
             <div className="flex gap-2 flex-wrap">
              {(['Hollow', 'Filled', 'Heikin-Ashi'] as CandlestickStyle[]).map(r => (
                <button
                  key={r}
                  onClick={() => updateSettings({ candlestickStyle: r })}
                  className={`px-3 py-1.5 rounded border text-sm font-mono transition-colors ${
                    settings.candlestickStyle === r ? 'border-blue-500 bg-blue-500/10 text-blue-400' : 'border-[#ffffff1a] bg-[#0e1117] hover:border-[#ffffff3a]'
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3">
            <label htmlFor="showGridLines" className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors mt-2">
              <input 
                id="showGridLines"
                type="checkbox" 
                checked={settings.showGridLines} 
                onChange={(e) => updateSettings({ showGridLines: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#1a1c24] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Show Grid Lines</span>
                <span className="text-xs text-[#888]">Display horizontal and vertical grid markers on main charts</span>
              </div>
            </label>
          </div>

        </section>

        {/* Data & Performance */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Zap size={16} /> Data & Performance
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Auto-Refresh Interval</label>
            <div className="flex gap-2 flex-wrap">
              {(['Off', '10s', '30s', '1min', '5min'] as AutoRefreshInterval[]).map(r => (
                <button
                  key={r}
                  onClick={() => updateSettings({ autoRefreshInterval: r })}
                  className={`px-3 py-1.5 rounded border text-sm font-mono transition-colors ${
                    settings.autoRefreshInterval === r ? 'border-blue-500 bg-blue-500/10 text-blue-400' : 'border-[#ffffff1a] bg-[#0e1117] hover:border-[#ffffff3a]'
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            <p className="text-xs text-[#888]">Background polling for live quotes/status. Paused when tab is hidden.</p>
          </div>

          <div className="space-y-3">
            <label htmlFor="animations" className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors mt-2">
              <input 
                id="animations"
                type="checkbox" 
                checked={settings.animations} 
                onChange={(e) => updateSettings({ animations: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#1a1c24] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Hardware Acceleration & Animations</span>
                <span className="text-xs text-[#888]">Enable layout transitions, pulsing dots, and charting animations.</span>
              </div>
            </label>
          </div>
        </section>

        {/* Notifications & Developer */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Bell size={16} /> Alerts
          </h4>
          <div className="space-y-3">
            <label htmlFor="enableSoundAlerts" className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors">
              <input 
                id="enableSoundAlerts"
                type="checkbox" 
                checked={settings.enableSoundAlerts} 
                onChange={(e) => updateSettings({ enableSoundAlerts: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#1a1c24] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Enable Sound Alerts</span>
                <span className="text-xs text-[#888]">Play a chime when scanners detect a new FVG or pattern</span>
              </div>
            </label>
          </div>
        </section>

        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Bug size={16} /> Developer Options
          </h4>
          <div className="space-y-3">
            <label htmlFor="debugMode" className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors">
              <input 
                id="debugMode"
                type="checkbox" 
                checked={settings.debugMode} 
                onChange={(e) => updateSettings({ debugMode: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#0e1117] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Debug Mode</span>
                <span className="text-xs text-[#888]">Show log panel, verbose errors, and query times</span>
              </div>
            </label>
            <label htmlFor="mockDataMode" className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors">
              <input 
                id="mockDataMode"
                type="checkbox" 
                checked={settings.mockDataMode} 
                onChange={(e) => updateSettings({ mockDataMode: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#0e1117] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Mock Data Generator</span>
                <span className="text-xs text-[#888]">Globally force mock data structures (UI layout testing)</span>
              </div>
            </label>
          </div>
        </section>

        {/* Backend Configuration */}
        <section className="space-y-4 md:col-span-2 mt-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Server size={16} /> Connection & Integration
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">FastAPI Bridge Endpoint</label>
            <div className="flex gap-3">
              <input 
                type="text" 
                value={settings.apiEndpoint}
                onChange={(e) => updateSettings({ apiEndpoint: e.target.value })}
                className="flex-1 bg-[#0e1117] border border-[#ffffff1a] hover:border-[#ffffff3a] rounded px-3 py-2 text-sm text-[#eee] focus:outline-none focus:border-blue-500 focus:bg-[#151821] font-mono transition-colors"
                placeholder="http://localhost:8000/api"
              />
              <button 
                onClick={() => alert('Connection test feature coming soon!')}
                className="bg-[#2a2d36] hover:bg-[#333640] border border-[#ffffff1a] px-4 rounded text-sm transition-colors"
              >
                Test Connection
              </button>
            </div>
            <p className="text-xs text-[#888]">Defines the URL where the React frontend will attempt to connect to your local Python/FastAPI backend (Librarian Bridge).</p>
          </div>
        </section>

      </div>
    </div>
  );
}
