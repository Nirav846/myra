/**
 * Chart Settings Panel
 *
 * Collapsible settings panel for chart configuration:
 * - Theme selection (dark/light/custom)
 * - Candle colors (up/down)
 * - Volume pane visibility
 * - Grid visibility
 * - Crosshair style
 *
 * Lightweight - no complex indicator settings or plugin framework.
 */

import { useState, memo, useCallback } from 'react';

export interface ChartTheme {
  id: string;
  name: string;
  backgroundColor: string;
  gridColor: string;
  textColor: string;
  candleUpColor: string;
  candleDownColor: string;
  volumeUpColor: string;
  volumeDownColor: string;
}

export const DEFAULT_THEMES: ChartTheme[] = [
  {
    id: 'dark',
    name: 'Dark',
    backgroundColor: '#1a1c24',
    gridColor: '#2a2c34',
    textColor: '#888899',
    candleUpColor: '#26a69a',
    candleDownColor: '#ef5350',
    volumeUpColor: 'rgba(38, 166, 154, 0.6)',
    volumeDownColor: 'rgba(239, 83, 80, 0.6)',
  },
  {
    id: 'light',
    name: 'Light',
    backgroundColor: '#ffffff',
    gridColor: '#e0e0e0',
    textColor: '#666666',
    candleUpColor: '#26a69a',
    candleDownColor: '#ef5350',
    volumeUpColor: 'rgba(38, 166, 154, 0.5)',
    volumeDownColor: 'rgba(239, 83, 80, 0.5)',
  },
  {
    id: 'midnight',
    name: 'Midnight',
    backgroundColor: '#0d1117',
    gridColor: '#1c2128',
    textColor: '#7d8590',
    candleUpColor: '#00e676',
    candleDownColor: '#ff5252',
    volumeUpColor: 'rgba(0, 230, 118, 0.5)',
    volumeDownColor: 'rgba(255, 82, 82, 0.5)',
  },
];

export interface ChartSettings {
  theme: ChartTheme;
  showVolume: boolean;
  showGrid: boolean;
  showCrosshair: boolean;
  crosshairStyle: 'solid' | 'dashed' | 'dotted';
  candleWidth: number;
  showWicks: boolean;
}

interface ChartSettingsPanelProps {
  /** Current settings */
  settings: ChartSettings;
  /** Callback when settings change */
  onChange: (settings: ChartSettings) => void;
  /** Panel collapsed state */
  collapsed: boolean;
  /** Callback to toggle collapse */
  onToggleCollapse: () => void;
}

export const ChartSettingsPanel = memo(({
  settings,
  onChange,
  collapsed,
  onToggleCollapse,
}: ChartSettingsPanelProps) => {
  const [activeTab, setActiveTab] = useState<'theme' | 'display'>('theme');

  /**
   * Update theme
   */
  const handleThemeChange = useCallback((themeId: string) => {
    const theme = DEFAULT_THEMES.find(t => t.id === themeId);
    if (theme) {
      onChange({ ...settings, theme });
    }
  }, [settings, onChange]);

  /**
   * Toggle boolean setting
   */
  const toggleSetting = useCallback(<K extends keyof ChartSettings>(
    key: K,
    value?: ChartSettings[K]
  ) => {
    if (value !== undefined) {
      onChange({ ...settings, [key]: value });
    } else {
      onChange({ ...settings, [key]: !settings[key] } as ChartSettings);
    }
  }, [settings, onChange]);

  if (collapsed) {
    return (
      <button
        onClick={onToggleCollapse}
        className="absolute top-2 right-2 z-40 bg-[#2a2c34] hover:bg-[#3a3c44] text-gray-300 rounded p-2 transition-colors"
        title="Open settings"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>
    );
  }

  return (
    <div className="absolute top-2 right-2 z-40 w-72 bg-[#1e2028] border border-[#ffffff1a] rounded-lg shadow-xl overflow-hidden">
      {/* Header */}
      <div className="h-10 bg-[#2a2c34]/50 border-b border-[#ffffff1a] flex items-center justify-between px-4">
        <span className="text-sm font-semibold text-white">Chart Settings</span>
        <button
          onClick={onToggleCollapse}
          className="text-gray-400 hover:text-white transition-colors"
          title="Close settings"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#ffffff1a]">
        <button
          onClick={() => setActiveTab('theme')}
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            activeTab === 'theme'
              ? 'bg-blue-600 text-white'
              : 'bg-[#1e2028] text-gray-400 hover:bg-[#2a2c34]'
          }`}
        >
          Theme
        </button>
        <button
          onClick={() => setActiveTab('display')}
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            activeTab === 'display'
              ? 'bg-blue-600 text-white'
              : 'bg-[#1e2028] text-gray-400 hover:bg-[#2a2c34]'
          }`}
        >
          Display
        </button>
      </div>

      {/* Content */}
      <div className="p-4 max-h-80 overflow-y-auto">
        {activeTab === 'theme' && (
          <div className="space-y-3">
            <label className="text-xs text-gray-400 block mb-2">Color Scheme</label>
            {DEFAULT_THEMES.map(theme => (
              <button
                key={theme.id}
                onClick={() => handleThemeChange(theme.id)}
                className={`w-full px-3 py-2 rounded text-left text-xs flex items-center gap-3 transition-colors ${
                  settings.theme.id === theme.id
                    ? 'bg-blue-600/20 border border-blue-500'
                    : 'bg-[#2a2c34] border border-transparent hover:bg-[#3a3c44]'
                }`}
              >
                <div
                  className="w-8 h-8 rounded border border-[#ffffff1a]"
                  style={{ backgroundColor: theme.backgroundColor }}
                />
                <div>
                  <div className="text-white font-medium">{theme.name}</div>
                  <div className="text-gray-500 text-[10px]">
                    BG: {theme.backgroundColor}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}

        {activeTab === 'display' && (
          <div className="space-y-4">
            {/* Show Volume */}
            <div className="flex items-center justify-between">
              <label className="text-xs text-gray-300">Volume Pane</label>
              <button
                onClick={() => toggleSetting('showVolume')}
                className={`w-10 h-5 rounded-full transition-colors ${
                  settings.showVolume ? 'bg-blue-600' : 'bg-[#2a2c34]'
                }`}
              >
                <div
                  className={`w-4 h-4 rounded-full bg-white transform transition-transform ${
                    settings.showVolume ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                  style={{ marginTop: '2px' }}
                />
              </button>
            </div>

            {/* Show Grid */}
            <div className="flex items-center justify-between">
              <label className="text-xs text-gray-300">Grid Lines</label>
              <button
                onClick={() => toggleSetting('showGrid')}
                className={`w-10 h-5 rounded-full transition-colors ${
                  settings.showGrid ? 'bg-blue-600' : 'bg-[#2a2c34]'
                }`}
              >
                <div
                  className={`w-4 h-4 rounded-full bg-white transform transition-transform ${
                    settings.showGrid ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                  style={{ marginTop: '2px' }}
                />
              </button>
            </div>

            {/* Show Crosshair */}
            <div className="flex items-center justify-between">
              <label className="text-xs text-gray-300">Crosshair</label>
              <button
                onClick={() => toggleSetting('showCrosshair')}
                className={`w-10 h-5 rounded-full transition-colors ${
                  settings.showCrosshair ? 'bg-blue-600' : 'bg-[#2a2c34]'
                }`}
              >
                <div
                  className={`w-4 h-4 rounded-full bg-white transform transition-transform ${
                    settings.showCrosshair ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                  style={{ marginTop: '2px' }}
                />
              </button>
            </div>

            {/* Crosshair Style */}
            {settings.showCrosshair && (
              <div className="space-y-2">
                <label className="text-xs text-gray-400 block">Crosshair Style</label>
                <div className="flex gap-2">
                  {(['solid', 'dashed', 'dotted'] as const).map(style => (
                    <button
                      key={style}
                      onClick={() => toggleSetting('crosshairStyle', style)}
                      className={`flex-1 px-2 py-1.5 rounded text-xs transition-colors ${
                        settings.crosshairStyle === style
                          ? 'bg-blue-600 text-white'
                          : 'bg-[#2a2c34] text-gray-400 hover:bg-[#3a3c44]'
                      }`}
                    >
                      {style.charAt(0).toUpperCase() + style.slice(1)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Candle Width */}
            <div className="space-y-2">
              <label className="text-xs text-gray-400 block">
                Candle Width: {settings.candleWidth.toFixed(1)}
              </label>
              <input
                type="range"
                min="0.3"
                max="1.0"
                step="0.1"
                value={settings.candleWidth}
                onChange={(e) => toggleSetting('candleWidth', parseFloat(e.target.value))}
                className="w-full accent-blue-600"
              />
            </div>

            {/* Show Wicks */}
            <div className="flex items-center justify-between">
              <label className="text-xs text-gray-300">Show Wicks</label>
              <button
                onClick={() => toggleSetting('showWicks')}
                className={`w-10 h-5 rounded-full transition-colors ${
                  settings.showWicks ? 'bg-blue-600' : 'bg-[#2a2c34]'
                }`}
              >
                <div
                  className={`w-4 h-4 rounded-full bg-white transform transition-transform ${
                    settings.showWicks ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                  style={{ marginTop: '2px' }}
                />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

ChartSettingsPanel.displayName = 'ChartSettingsPanel';

export default ChartSettingsPanel;
