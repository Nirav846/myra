export type ScannerModule = 'ReversionEngine' | 'MultibaggerMatrix' | 'PriceDeliveryDivergence' | 'ValueRanker';

export interface ReversionConfig {
    setup: 'Exhaustion' | 'Divergence' | 'SpringCoil';
    filterSector: string;
    filterMcap: string;
}

export interface MultibaggerConfig {
    excludeCyclical: boolean;
    requireAccumulation: boolean;
    minRoe: number;
    minEps: number;
    days: number;
    filterMcap: string;
}

export interface DivergenceConfig {
    lookbackBars: number;
    priceMetric: 'Close' | 'VWAP' | 'Typical';
    deliveryMetric: 'Pct' | 'Qty';
    priceDirection: 'Falling' | 'Rising';
    minPriceChange: number;
    minDeliveryChange: number;
    minRelativeVolume: number;
    minScore: number;
    scoreWeighting: 'Balanced' | 'Price' | 'Delivery';
    filterSector: string;
    filterMcap: string;
}

export interface ValueRankerConfig {
    weights: {
        graham: number;
        earningsYield: number;
        roe: number;
        debtEquity: number;
        dividendYield: number;
        netMargin: number;
    };
    minScore: number;
    maxPE: number;
    filterSector: string;
    filterMcap: string;
}

export type BaseConfig = ReversionConfig | MultibaggerConfig | DivergenceConfig | ValueRankerConfig;

export interface ScannerPreset {
    id: string;
    name: string;
    module: ScannerModule;
    description?: string;
    config: any;
    isDefault?: boolean;
}

export const DEFAULT_PRESETS: ScannerPreset[] = [
    {
        id: 'rev-default-1',
        name: 'Large Cap Exhaustion',
        module: 'ReversionEngine',
        description: 'Exhausted large caps ready for a bounce.',
        isDefault: true,
        config: { setup: 'Exhaustion', filterSector: 'All', filterMcap: 'Large Cap (N100)' }
    },
    {
        id: 'rev-default-2',
        name: 'Broad Market Divergence',
        module: 'ReversionEngine',
        description: 'Divergence plays across N500.',
        isDefault: true,
        config: { setup: 'Divergence', filterSector: 'All', filterMcap: 'Broader Market (N500)' }
    },
    {
        id: 'rev-default-3',
        name: 'Small Cap Coils',
        module: 'ReversionEngine',
        description: 'Spring coil setups in small caps.',
        isDefault: true,
        config: { setup: 'SpringCoil', filterSector: 'All', filterMcap: 'Nifty Small Cap 250' }
    },
    {
        id: 'mb-default-1',
        name: '🛡️ Conservative',
        module: 'MultibaggerMatrix',
        description: 'Large caps, high ROE, strong accumulation.',
        isDefault: true,
        config: { minRoe: 12, minEps: 5, days: 365, excludeCyclical: true, requireAccumulation: true, filterMcap: 'Large Cap (N100)' }
    },
    {
        id: 'mb-default-2',
        name: '⚖️ Balanced (Recommended)',
        module: 'MultibaggerMatrix',
        description: 'N500 universe, moderate thresholds.',
        isDefault: true,
        config: { minRoe: 8, minEps: 3, days: 180, excludeCyclical: true, requireAccumulation: true, filterMcap: 'Broader Market (N500)' }
    },
    {
        id: 'mb-default-3',
        name: '🚀 Aggressive',
        module: 'MultibaggerMatrix',
        description: 'All caps, low thresholds, short lookback.',
        isDefault: true,
        config: { minRoe: 10, minEps: 5, days: 90, excludeCyclical: false, requireAccumulation: false, filterMcap: 'All' }
    },
    {
        id: 'pdd-default-1',
        name: 'Classic Bearish Divergence',
        module: 'PriceDeliveryDivergence',
        description: 'Price is falling, but delivery % is spiking. Classic divergence sign.',
        isDefault: true,
        config: {
            lookbackBars: 10, priceMetric: 'Close', deliveryMetric: 'Pct', priceDirection: 'Falling',
            minPriceChange: -2, minDeliveryChange: 5, minRelativeVolume: 1.2, minScore: 50, scoreWeighting: 'Balanced',
            filterSector: 'All', filterMcap: 'All'
        }
    },
    {
        id: 'pdd-default-2',
        name: 'Volume Spike Accumulation',
        module: 'PriceDeliveryDivergence',
        description: 'Strong delivery quantity spike with flat/down price.',
        isDefault: true,
        config: {
            lookbackBars: 5, priceMetric: 'VWAP', deliveryMetric: 'Qty', priceDirection: 'Falling',
            minPriceChange: -1, minDeliveryChange: 50, minRelativeVolume: 2.0, minScore: 60, scoreWeighting: 'Delivery',
            filterSector: 'All', filterMcap: 'Broader Market (N500)'
        }
    },
    {
        id: 'pdd-default-3',
        name: 'Quiet Absorption',
        module: 'PriceDeliveryDivergence',
        description: 'Steady rising delivery inside a tight price range.',
        isDefault: true,
        config: {
            lookbackBars: 21, priceMetric: 'Typical', deliveryMetric: 'Pct', priceDirection: 'Rising',
            minPriceChange: 0, minDeliveryChange: 2, minRelativeVolume: 1.0, minScore: 40, scoreWeighting: 'Balanced',
            filterSector: 'All', filterMcap: 'All'
        }
    },
    {
        id: 'vr-default-1',
        name: 'Classic Graham',
        module: 'ValueRanker',
        description: 'Focuses 100% on the Graham Number margin, ignoring other factors.',
        isDefault: true,
        config: {
            weights: { graham: 100, earningsYield: 0, roe: 0, debtEquity: 0, dividendYield: 0, netMargin: 0 },
            minScore: 50, maxPE: 25, filterSector: 'All', filterMcap: 'All'
        }
    },
    {
        id: 'vr-default-2',
        name: 'Quality Value',
        module: 'ValueRanker',
        description: 'Balanced approach blending Graham margin with quality metrics like ROE and Net Margin.',
        isDefault: true,
        config: {
            weights: { graham: 25, earningsYield: 25, roe: 20, debtEquity: 15, dividendYield: 10, netMargin: 5 },
            minScore: 50, maxPE: 30, filterSector: 'All', filterMcap: 'Broader Market (N500)'
        }
    },
    {
        id: 'vr-default-3',
        name: 'Deep Value',
        module: 'ValueRanker',
        description: 'Heavier on Earnings Yield and Debt/Equity for distressed deep value.',
        isDefault: true,
        config: {
            weights: { graham: 20, earningsYield: 40, roe: 0, debtEquity: 30, dividendYield: 0, netMargin: 10 },
            minScore: 40, maxPE: 15, filterSector: 'All', filterMcap: 'All'
        }
    },
    {
        id: 'vr-default-4',
        name: 'Dividend Value',
        module: 'ValueRanker',
        description: 'Heavier on Dividend Yield mixed with baseline valuation.',
        isDefault: true,
        config: {
            weights: { graham: 20, earningsYield: 20, roe: 10, debtEquity: 10, dividendYield: 40, netMargin: 0 },
            minScore: 50, maxPE: 30, filterSector: 'All', filterMcap: 'All'
        }
    }
];

const STORAGE_KEY = 'myra_scanner_user_presets';

export function loadUserPresets(): ScannerPreset[] {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) return JSON.parse(raw);
    } catch (e) {
        console.error('Failed to load user presets', e);
    }
    return [];
}

export function saveUserPreset(preset: Omit<ScannerPreset, 'id' | 'isDefault'>): ScannerPreset {
    const newPreset: ScannerPreset = {
        ...preset,
        id: `user-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        isDefault: false
    };
    const presets = loadUserPresets();
    presets.push(newPreset);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(presets));
    return newPreset;
}

export function updateUserPreset(id: string, updates: Partial<ScannerPreset>) {
    const presets = loadUserPresets();
    const idx = presets.findIndex(p => p.id === id);
    if (idx !== -1) {
        presets[idx] = { ...presets[idx], ...updates };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(presets));
    }
}

export function deleteUserPreset(id: string) {
    const presets = loadUserPresets();
    const updated = presets.filter(p => p.id !== id);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
}

export function getAllPresets(): ScannerPreset[] {
    return [...DEFAULT_PRESETS, ...loadUserPresets()];
}

export function exportPresetsToJSON(): string {
  return JSON.stringify({ defaults: DEFAULT_PRESETS, user: loadUserPresets() }, null, 2);
}

export function importPresetsFromJSON(json: string): number {
  try {
    const parsed = JSON.parse(json);
    if (parsed.user && Array.isArray(parsed.user)) {
      const existing = loadUserPresets();
      const merged = [...existing, ...parsed.user.filter(
        (p: ScannerPreset) => !existing.some(e => e.id === p.id)
      )];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
      return merged.length - existing.length; // return count of newly imported
    }
    return 0;
  } catch {
    return -1;
  }
}
