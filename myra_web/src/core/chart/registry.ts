import { IndicatorModule } from '../technical-analysis/types';
import { TraceBuilder } from './traces/types';
import { LayoutBuilder } from './layout/types';

import { smaIndicator } from '../technical-analysis/indicators/sma';
import { rsiIndicator } from '../technical-analysis/indicators/rsi';
import { atrIndicator } from '../technical-analysis/indicators/atr';
import { fvgIndicator } from '../technical-analysis/indicators/fvg';
import { swingsIndicator } from '../technical-analysis/indicators/swings';
import { volumeProfileIndicator } from '../technical-analysis/indicators/volumeProfile';

import { smaTraceBuilder } from './traces/smaBuilder';
import { rsiTraceBuilder } from './traces/rsiBuilder';
import { fvgTraceBuilder } from './traces/fvgBuilder';
import { volumeProfileTraceBuilder } from './traces/volumeProfileBuilder';
import { swingsTraceBuilder } from './traces/swingsBuilder';
import { vwapTraceBuilder } from './traces/vwapBuilder';
import { volumeTraceBuilder } from './traces/volumeBuilder';
import { deliveryTraceBuilder } from './traces/deliveryBuilder';
import { niftyOutTraceBuilder } from './traces/niftyOutBuilder';
import { delVwapBandsIndicator } from '../technical-analysis/indicators/delVwapBands';
import { delVwapBandsTraceBuilder } from './traces/delVwapBandsBuilder';
import { instBlocksIndicator } from '../technical-analysis/indicators/instBlocks';
import { instBlocksTraceBuilder } from './traces/instBlocksBuilder';
import { delAdIndicator } from '../technical-analysis/indicators/delAd';
import { delAdTraceBuilder } from './traces/delAdBuilder';
import { smartMoneyPrintsIndicator } from '../technical-analysis/indicators/smartMoneyPrints';
import { smartMoneyPrintsTraceBuilder } from './traces/smartMoneyPrintsBuilder';
import { delIntensityCoreIndicator } from '../technical-analysis/indicators/delIntensityCore';
import { delIntensityCoreTraceBuilder } from './traces/delIntensityCoreBuilder';

import { fibonacciLayoutBuilder } from './layout/fibonacciBuilder';
import { liqVoidsLayoutBuilder } from './layout/liqVoidsBuilder';
import { liqVoidsIndicator } from '../technical-analysis/indicators/liqVoids';

class ChartRegistry {
    private indicators = new Map<string, IndicatorModule<any, any>>();
    private traceBuilders = new Map<string, TraceBuilder<any, any>>();
    private layoutBuilders = new Map<string, LayoutBuilder<any>>();

    registerIndicator(indicator: IndicatorModule<any, any>) {
        this.indicators.set(indicator.id, indicator);
    }

    getIndicator(id: string) {
        return this.indicators.get(id);
    }

    registerTraceBuilder(builder: TraceBuilder<any, any>) {
        this.traceBuilders.set(builder.id, builder);
    }

    getTraceBuilder(id: string) {
        return this.traceBuilders.get(id);
    }

    registerLayoutBuilder(builder: LayoutBuilder<any>) {
        this.layoutBuilders.set(builder.id, builder);
    }

    getLayoutBuilder(id: string) {
        return this.layoutBuilders.get(id);
    }
}

export const chartRegistry = new ChartRegistry();

// Initialize registry with core indicators and builders
chartRegistry.registerIndicator(smaIndicator);
chartRegistry.registerIndicator(rsiIndicator);
chartRegistry.registerIndicator(atrIndicator);
chartRegistry.registerIndicator(fvgIndicator);
chartRegistry.registerIndicator(swingsIndicator);
chartRegistry.registerIndicator(volumeProfileIndicator);
chartRegistry.registerIndicator(delVwapBandsIndicator);
chartRegistry.registerIndicator(instBlocksIndicator);
chartRegistry.registerIndicator(delAdIndicator);
chartRegistry.registerIndicator(smartMoneyPrintsIndicator);
chartRegistry.registerIndicator(delIntensityCoreIndicator);
chartRegistry.registerIndicator(liqVoidsIndicator);

chartRegistry.registerTraceBuilder(smaTraceBuilder);
chartRegistry.registerTraceBuilder(rsiTraceBuilder);
chartRegistry.registerTraceBuilder(fvgTraceBuilder);
chartRegistry.registerTraceBuilder(volumeProfileTraceBuilder);
chartRegistry.registerTraceBuilder(swingsTraceBuilder);
chartRegistry.registerTraceBuilder(vwapTraceBuilder);
chartRegistry.registerTraceBuilder(volumeTraceBuilder);
chartRegistry.registerTraceBuilder(deliveryTraceBuilder);
chartRegistry.registerTraceBuilder(niftyOutTraceBuilder);
chartRegistry.registerTraceBuilder(delVwapBandsTraceBuilder);
chartRegistry.registerTraceBuilder(instBlocksTraceBuilder);
chartRegistry.registerTraceBuilder(delAdTraceBuilder);
chartRegistry.registerTraceBuilder(smartMoneyPrintsTraceBuilder);
chartRegistry.registerTraceBuilder(delIntensityCoreTraceBuilder);

chartRegistry.registerLayoutBuilder(fibonacciLayoutBuilder);
chartRegistry.registerLayoutBuilder(liqVoidsLayoutBuilder);
