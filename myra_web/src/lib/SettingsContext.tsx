import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

export type Theme = 'myra-dark' | 'pitch-black';
export type Density = 'comfortable' | 'compact';
export type AccentColor = 'indigo' | 'cyan' | 'fuchsia' | 'green';
export type ChartRange = '1M' | '3M' | '6M' | '1Y' | 'All';
export type CandlestickStyle = 'Hollow' | 'Filled' | 'Heikin-Ashi';
export type ChartHeight = 'Compact' | 'Standard' | 'Expansive';
export type AutoRefreshInterval = 'Off' | '10s' | '30s' | '1min' | '5min';
export type FontSize = 'Small' | 'Medium' | 'Large';
export type FontFamily = 'Monospace' | 'Sans-serif' | 'System Default';
export type SidebarPosition = 'Left' | 'Right';

export interface SettingsState {
  theme: Theme;
  density: Density;
  animations: boolean;
  accentColor: AccentColor;
  apiEndpoint: string;

  // Chart & Technical Analysis
  defaultChartRange: ChartRange;
  candlestickStyle: CandlestickStyle;
  showGridLines: boolean;
  chartHeight: ChartHeight;

  // Data & Performance
  autoRefreshInterval: AutoRefreshInterval;
  maxCandlesPerRequest: number;
  mockDataMode: boolean;

  // Appearance & Accessibility
  fontSize: FontSize;
  fontFamily: FontFamily;
  highContrastMode: boolean;
  sidebarPosition: SidebarPosition;

  // Notifications & Alerts
  enableSoundAlerts: boolean;
  browserNotifications: boolean;
  alertVolume: number;

  // Developer
  debugMode: boolean;
  frvpBinCount: number;
  rsiPeriod: number;
}

interface SettingsContextType {
  settings: SettingsState;
  updateSettings: (newSettings: Partial<SettingsState>) => void;
}

const defaultSettings: SettingsState = {
  theme: 'myra-dark',
  density: 'comfortable',
  animations: true,
  accentColor: 'indigo',
  apiEndpoint: 'http://localhost:8000/api',
  defaultChartRange: '1M',
  candlestickStyle: 'Filled',
  showGridLines: true,
  chartHeight: 'Standard',
  autoRefreshInterval: 'Off',
  maxCandlesPerRequest: 10000,
  mockDataMode: false,
  fontSize: 'Medium',
  fontFamily: 'System Default',
  highContrastMode: false,
  sidebarPosition: 'Left',
  enableSoundAlerts: true,
  browserNotifications: false,
  alertVolume: 0.5,
  debugMode: false,
  frvpBinCount: 50,
  rsiPeriod: 14,
};

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<SettingsState>(() => {
    try {
      const saved = localStorage.getItem('myra-settings');
      return saved ? { ...defaultSettings, ...JSON.parse(saved) } : defaultSettings;
    } catch {
      return defaultSettings;
    }
  });

  useEffect(() => {
    localStorage.setItem('myra-settings', JSON.stringify(settings));
    
    if (settings.theme === 'pitch-black') {
      document.body.style.backgroundColor = '#000000';
    } else {
      document.body.style.backgroundColor = '#0e1117';
    }
  }, [settings]);

  const updateSettings = (newSettings: Partial<SettingsState>) => {
    setSettings(prev => ({ ...prev, ...newSettings }));
  };

  return (
    <SettingsContext.Provider value={{ settings, updateSettings }}>
      {children}
    </SettingsContext.Provider>
  );
}

export const useSettings = () => {
  const context = useContext(SettingsContext);
  if (context === undefined) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
};
