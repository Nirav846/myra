import { TraceBuilder, TraceBuilderContext } from './types';
import { DeliveryTrendResult } from '../../technical-analysis/indicators/deliveryTrend';

export const deliveryTrendTraceBuilder: TraceBuilder<DeliveryTrendResult, any> = {
  id: 'deliveryTrend',
  buildTraces: (result: DeliveryTrendResult, context: TraceBuilderContext) => {
    if (!result || result.ema20.length === 0) return [];
    const { candleIndexes } = context;

    const traces: any[] = [];

    // Delivery % line (raw)
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: candleIndexes,
      y: result.deliveryPercentages,
      name: 'Del %',
      yaxis: 'y5',
      line: { color: 'rgba(136, 136, 136, 0.3)', width: 1 },
      hoverinfo: 'skip',
      showlegend: false,
    });

    // EMA20 line
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: candleIndexes,
      y: result.ema20,
      name: 'Del EMA20',
      yaxis: 'y5',
      line: { color: '#f59e0b', width: 2 },
      connectgaps: false,
      hoverinfo: 'skip',
      showlegend: false,
    });

    // Signal line
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: candleIndexes,
      y: result.signalLine,
      name: 'Del Signal',
      yaxis: 'y5',
      line: { color: '#8b5cf6', width: 1.5, dash: 'dot' },
      connectgaps: false,
      hoverinfo: 'skip',
      showlegend: false,
    });

    return traces;
  },
  buildShapes: () => [],
};
