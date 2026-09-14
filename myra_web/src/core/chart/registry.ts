import { IndicatorModule } from '../technical-analysis/types';
import { TraceBuilder } from './traces/types';
import { LayoutBuilder } from './layout/types';

// Lazy-load indicator modules dynamically
const indicatorLoaders: Record<string, () => Promise<IndicatorModule<any, any>>> = {
    'sma': () => import('../technical-analysis/indicators/sma').then(m => m.smaIndicator),
    'rsi': () => import('../technical-analysis/indicators/rsi').then(m => m.rsiIndicator),
    'atr': () => import('../technical-analysis/indicators/atr').then(m => m.atrIndicator),
    'fvg': () => import('../technical-analysis/indicators/fvg').then(m => m.fvgIndicator),
    'swings': () => import('../technical-analysis/indicators/swings').then(m => m.swingsIndicator),
    'volumeProfile': () => import('../technical-analysis/indicators/volumeProfile').then(m => m.volumeProfileIndicator),
    'delVwapBands': () => import('../technical-analysis/indicators/delVwapBands').then(m => m.delVwapBandsIndicator),
    'instBlocks': () => import('../technical-analysis/indicators/instBlocks').then(m => m.instBlocksIndicator),
    'delAd': () => import('../technical-analysis/indicators/delAd').then(m => m.delAdIndicator),
    'smartMoneyPrints': () => import('../technical-analysis/indicators/smartMoneyPrints').then(m => m.smartMoneyPrintsIndicator),
    'delIntensityCore': () => import('../technical-analysis/indicators/delIntensityCore').then(m => m.delIntensityCoreIndicator),
    'liqVoids': () => import('../technical-analysis/indicators/liqVoids').then(m => m.liqVoidsIndicator),
    'orderBlocks': () => import('../technical-analysis/indicators/orderBlocks').then(m => ({ id: 'orderBlocks', defaults: {}, calculate: m.detectOrderBlocks }) as IndicatorModule<any, any>),
    'equalHighsLows': () => import('../technical-analysis/indicators/equalHighsLows').then(m => ({ id: 'equalHighsLows', defaults: { tolerancePercent: 0.5, minTouches: 2 }, calculate: m.detectEqualHighsLows }) as IndicatorModule<any, any>),
    'premiumDiscount': () => import('../technical-analysis/indicators/premiumDiscount').then(m => ({ id: 'premiumDiscount', defaults: { lookback: 50 }, calculate: m.calculatePremiumDiscount }) as IndicatorModule<any, any>),
    'deliveryTrend': () => import('../technical-analysis/indicators/deliveryTrend').then(m => ({ id: 'deliveryTrend', defaults: {}, calculate: m.calculateDeliveryTrend }) as IndicatorModule<any, any>),
    'breakerBlocks': () => import('../technical-analysis/indicators/breakerBlocks').then(m => ({ id: 'breakerBlocks', defaults: {}, calculate: m.detectBreakerBlocks }) as IndicatorModule<any, any>),
    'marketStructureShift': () => import('../technical-analysis/indicators/marketStructureShift').then(m => ({ id: 'marketStructureShift', defaults: {}, calculate: m.detectMarketStructureShifts }) as IndicatorModule<any, any>),
    'changeOfCharacter': () => import('../technical-analysis/indicators/changeOfCharacter').then(m => ({ id: 'changeOfCharacter', defaults: {}, calculate: m.detectChangeOfCharacter }) as IndicatorModule<any, any>),
    'deliveryVolumeRatio': () => import('../technical-analysis/indicators/deliveryVolumeRatio').then(m => ({ id: 'deliveryVolumeRatio', defaults: { lookback: 20 }, calculate: m.calculateDeliveryVolumeRatio }) as IndicatorModule<any, any>),
};

// Lazy-load trace builders dynamically
const traceBuilderLoaders: Record<string, () => Promise<TraceBuilder<any, any>>> = {
    'sma': () => import('./traces/smaBuilder').then(m => m.smaTraceBuilder),
    'rsi': () => import('./traces/rsiBuilder').then(m => m.rsiTraceBuilder),
    'fvg': () => import('./traces/fvgBuilder').then(m => m.fvgTraceBuilder),
    'volumeProfile': () => import('./traces/volumeProfileBuilder').then(m => m.volumeProfileTraceBuilder),
    'swings': () => import('./traces/swingsBuilder').then(m => m.swingsTraceBuilder),
    'vwap': () => import('./traces/vwapBuilder').then(m => m.vwapTraceBuilder),
    'volume': () => import('./traces/volumeBuilder').then(m => m.volumeTraceBuilder),
    'delivery': () => import('./traces/deliveryBuilder').then(m => m.deliveryTraceBuilder),
    'niftyOut': () => import('./traces/niftyOutBuilder').then(m => m.niftyOutTraceBuilder),
    'delVwapBands': () => import('./traces/delVwapBandsBuilder').then(m => m.delVwapBandsTraceBuilder),
    'instBlocks': () => import('./traces/instBlocksBuilder').then(m => m.instBlocksTraceBuilder),
    'delAd': () => import('./traces/delAdBuilder').then(m => m.delAdTraceBuilder),
    'smartMoneyPrints': () => import('./traces/smartMoneyPrintsBuilder').then(m => m.smartMoneyPrintsTraceBuilder),
    'delIntensityCore': () => import('./traces/delIntensityCoreBuilder').then(m => m.delIntensityCoreTraceBuilder),
    'orderBlocks': () => import('./traces/orderBlocksBuilder').then(m => m.orderBlocksTraceBuilder),
    'equalHighsLows': () => import('./traces/equalHighsLowsBuilder').then(m => m.equalHighsLowsTraceBuilder),
    'premiumDiscount': () => import('./traces/premiumDiscountBuilder').then(m => m.premiumDiscountTraceBuilder),
};

// Lazy-load layout builders dynamically
const layoutBuilderLoaders: Record<string, () => Promise<LayoutBuilder<any>>> = {
    'fibonacci': () => import('./layout/fibonacciBuilder').then(m => m.fibonacciLayoutBuilder),
    'liqVoids': () => import('./layout/liqVoidsBuilder').then(m => m.liqVoidsLayoutBuilder),
};

class ChartRegistry {
    private indicators = new Map<string, IndicatorModule<any, any>>();
    private traceBuilders = new Map<string, TraceBuilder<any, any>>();
    private layoutBuilders = new Map<string, LayoutBuilder<any>>();
    private pendingIndicators = new Map<string, Promise<void>>();
    private pendingTraceBuilders = new Map<string, Promise<void>>();
    private pendingLayoutBuilders = new Map<string, Promise<void>>();

    private async ensureIndicator(id: string): Promise<void> {
        if (this.indicators.has(id) || this.pendingIndicators.has(id)) {
            return this.pendingIndicators.get(id);
        }
        const loader = indicatorLoaders[id];
        if (!loader) return;
        
        const loadPromise = loader().then(module => {
            this.indicators.set(id, module);
            this.pendingIndicators.delete(id);
        }).catch(err => {
            console.error(`Failed to lazy-load indicator "${id}":`, err);
            this.pendingIndicators.delete(id);
        });
        
        this.pendingIndicators.set(id, loadPromise);
        return loadPromise;
    }

    private async ensureTraceBuilder(id: string): Promise<void> {
        if (this.traceBuilders.has(id) || this.pendingTraceBuilders.has(id)) {
            return this.pendingTraceBuilders.get(id);
        }
        const loader = traceBuilderLoaders[id];
        if (!loader) return;
        
        const loadPromise = loader().then(module => {
            this.traceBuilders.set(id, module);
            this.pendingTraceBuilders.delete(id);
        }).catch(err => {
            console.error(`Failed to lazy-load trace builder "${id}":`, err);
            this.pendingTraceBuilders.delete(id);
        });
        
        this.pendingTraceBuilders.set(id, loadPromise);
        return loadPromise;
    }

    private async ensureLayoutBuilder(id: string): Promise<void> {
        if (this.layoutBuilders.has(id) || this.pendingLayoutBuilders.has(id)) {
            return this.pendingLayoutBuilders.get(id);
        }
        const loader = layoutBuilderLoaders[id];
        if (!loader) return;
        
        const loadPromise = loader().then(module => {
            this.layoutBuilders.set(id, module);
            this.pendingLayoutBuilders.delete(id);
        }).catch(err => {
            console.error(`Failed to lazy-load layout builder "${id}":`, err);
            this.pendingLayoutBuilders.delete(id);
        });
        
        this.pendingLayoutBuilders.set(id, loadPromise);
        return loadPromise;
    }

    async getIndicator(id: string): Promise<IndicatorModule<any, any> | undefined> {
        await this.ensureIndicator(id);
        return this.indicators.get(id);
    }

    async getTraceBuilder(id: string): Promise<TraceBuilder<any, any> | undefined> {
        await this.ensureTraceBuilder(id);
        return this.traceBuilders.get(id);
    }

    async getLayoutBuilder(id: string): Promise<LayoutBuilder<any> | undefined> {
        await this.ensureLayoutBuilder(id);
        return this.layoutBuilders.get(id);
    }
    
    // Synchronous versions for backward compatibility (returns already-loaded modules)
    getIndicatorSync(id: string): IndicatorModule<any, any> | undefined {
        return this.indicators.get(id);
    }
    
    getTraceBuilderSync(id: string): TraceBuilder<any, any> | undefined {
        return this.traceBuilders.get(id);
    }
    
    getLayoutBuilderSync(id: string): LayoutBuilder<any> | undefined {
        return this.layoutBuilders.get(id);
    }
}

export const chartRegistry = new ChartRegistry();
