import { IndicatorModule } from '../types';
import { computeFrvp, FrvpResult, FrvpConfig } from '../../../lib/frvpUtils';

export const volumeProfileIndicator: IndicatorModule<FrvpResult | null, Partial<FrvpConfig>> = {
    id: 'volumeProfile',
    
    calculate: (data, options) => {
        if (!data || data.length === 0) return null;

        const highs = data.map(d => d.high);
        const lows = data.map(d => d.low);
        const priceRange = Math.max(...highs) - Math.min(...lows);
        
        let validDrops = 0;
        const atrSum = data.slice(1).reduce((sum, d, i) => {
            const close = data[i].close;
            if (close && close > 0) {
               validDrops++;
               return sum + ((d.high - d.low) / close);
            }
            return sum;
        }, 0);
        const avgVolatility = validDrops > 0 ? atrSum / validDrops : 0.015;

        const config: FrvpConfig = {
            bucket: options?.bucket ?? null,
            resolution: options?.resolution ?? 'auto',
            visibleBars: data.length,
            priceRange,
            avgVolatility
        };

        return computeFrvp(data, config);
    }
};
