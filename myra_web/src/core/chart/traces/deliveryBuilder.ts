import { TraceBuilder } from './types';

export interface DeliveryConfig {
    showMA: boolean;
    maData?: number[];
}

export const deliveryTraceBuilder: TraceBuilder<number[], DeliveryConfig> = {
    id: 'delivery',
    buildTraces: (result, context, config) => {
        const dates = context.data.map(d => d.date);
        const deliveryColorsInverse = context.data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e');
        
        const traces: any[] = [{
            type: 'bar',
            x: dates,
            y: result,
            name: 'Del Qty',
            yaxis: 'y5',
            marker: { color: deliveryColorsInverse, opacity: 0.8 },
            hovertemplate: '%{y:.2s}<extra></extra>'
        }];

        if (config?.showMA && config.maData) {
            traces.push({
                type: 'scattergl',
                mode: 'lines',
                x: dates,
                y: config.maData,
                name: 'Del MA (20)',
                yaxis: 'y5',
                line: { color: '#f59e0b', width: 2 },
                hovertemplate: 'MA: %{y:.2s}<extra></extra>'
            });
        }
        
        return traces;
    }
};
