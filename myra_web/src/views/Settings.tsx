import { useSettings } from '../lib/SettingsContext';
import { Settings, Palette, Eye, Moon, Monitor, LayoutGrid, CheckCircle2, Server } from 'lucide-react';

export default function SettingsView() {
  const { settings, updateSettings } = useSettings();

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded-lg flex flex-col shadow-xl overflow-hidden max-w-4xl">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Settings size={20} className="text-[#888]" />
          Dashboard Configuration
        </h3>
        <span className="text-xs text-[#888] font-mono">~/.config/myra/ui.json</span>
      </div>

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-8 text-sm">
        
        {/* Visual Theme Section */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Palette size={16} /> Visual Aesthetics
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Environment Theme</label>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'myra-dark', label: 'Myra Default', icon: <Monitor size={16}/> },
                { id: 'pitch-black', label: 'OLED Black', icon: <Moon size={16}/> }
              ].map(theme => (
                <button
                  key={theme.id}
                  onClick={() => updateSettings({ theme: theme.id as any })}
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
                { id: 'indigo', color: 'bg-indigo-500' },
                { id: 'cyan', color: 'bg-cyan-500' },
                { id: 'fuchsia', color: 'bg-fuchsia-500' },
                { id: 'green', color: 'bg-green-500' },
              ].map(accent => (
                <button
                  key={accent.id}
                  onClick={() => updateSettings({ accentColor: accent.id as any })}
                  className={`w-10 h-10 rounded-full flex items-center justify-center border-2 transition-transform ${settings.accentColor === accent.id ? `border-white scale-110` : 'border-transparent hover:scale-105'} ${accent.color}`}
                >
                  {settings.accentColor === accent.id && <CheckCircle2 size={16} className="text-white mix-blend-difference" />}
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* Layout & Behavior Section */}
        <section className="space-y-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <LayoutGrid size={16} /> Layout & Behavior
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Data Density</label>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'comfortable', label: 'Comfortable (Default)' },
                { id: 'compact', label: 'Compact (High Density)' }
              ].map(density => (
                <button
                  key={density.id}
                  onClick={() => updateSettings({ density: density.id as any })}
                  className={`px-4 py-3 rounded border text-left transition-colors relative ${settings.density === density.id ? 'bg-[#ffffff10] border-blue-500' : 'bg-[#0e1117] border-[#ffffff1a] hover:border-[#ffffff3a]'}`}
                >
                  {settings.density === density.id && <CheckCircle2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-blue-400" />}
                  <span className="font-medium block">{density.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 pt-2">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">Performance Options</label>
            <label className="flex items-center gap-3 p-3 bg-[#0e1117] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff05] transition-colors">
              <input 
                type="checkbox" 
                checked={settings.animations} 
                onChange={(e) => updateSettings({ animations: e.target.checked })}
                className="w-4 h-4 rounded border-[#333] bg-[#1a1c24] text-blue-500 focus:ring-blue-500/20"
              />
              <div className="flex flex-col">
                <span className="font-medium text-[#eee]">Hardware Acceleration & Animations</span>
                <span className="text-xs text-[#888]">Enable layout transitions, pulsing dots, and charting animations. Turn off for lower CPU usage.</span>
              </div>
            </label>
          </div>
        </section>

        {/* Backend Configuration */}
        <section className="space-y-4 md:col-span-2 mt-4">
          <h4 className="flex items-center gap-2 font-semibold text-[#ccc] border-b border-[#ffffff1a] pb-2">
            <Server size={16} /> Backend Architecture
          </h4>

          <div className="space-y-3">
            <label className="block text-[#888] text-xs uppercase tracking-wider font-mono">FastAPI Bridge Endpoint</label>
            <div className="flex gap-3">
              <input 
                type="text" 
                value={settings.apiEndpoint}
                onChange={(e) => updateSettings({ apiEndpoint: e.target.value })}
                className="flex-1 bg-[#0e1117] border border-[#ffffff1a] rounded px-3 py-2 text-sm text-[#eee] focus:outline-none focus:border-blue-500 font-mono"
                placeholder="http://localhost:8000/api"
              />
            </div>
            <p className="text-xs text-[#888]">Defines the URL where the React frontend will attempt to connect to your local Python/FastAPI backend (Librarian Bridge). Change this if your internal port routing differs.</p>
          </div>
        </section>

      </div>
    </div>
  );
}
