import { TraceBuilder, TraceBuilderContext } from './types';

export interface DVRBuildData {
  ratios: number[];
  signals: Array<{
    index: number;
    type: string;
    ratio: number;
  }>;
}

export const deliveryVolumeRatioTraceBuilder: TraceBuilder<DVRBuildData, any> = {
  id: 'deliveryVolumeRatio',
  buildTraces: (result: DVRBuildData, context: TraceBuilderContext) => {
    if (!result || result.ratios.length === 0) return [];
    const { candleIndexes } = context;

    const traces: any[] = [];

    // Ratio bars (colored by zone)
    const colors = result.ratios.map(r => {
      if (r > 2.0) return 'rgba(34, 197, 94, 0.8)';
      if (r > 1.5) return 'rgba(34, 197, 94, 0.5)';
      if (r < 0.5) return 'rgba(239, 68, 68, 0.8)';
      if (r < 0.8) return 'rgba(239, 68, 68, 0.5)';
      return 'rgba(136, 136, 136, 0.3)';
    });

    traces.push({
      type: 'bar',
      x: candleIndexes,
      y: result.ratios,
      name: 'DVR',
      yaxis: 'y5',
      marker: { color: colors, line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false,
      opacity: 0.7,
    });

    // Accumulation threshold line (1.5)
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: [candleIndexes[0], candleIndexes[candleIndexes.length - 1]],
      y: [1.5, 1.5],
      yaxis: 'y5',
      line: { color: 'rgba(34, 197, 94, 0.4)', width: 1, dash: 'dash' },
      hoverinfo: 'skip',
      showlegend: false,
    });

    // Distribution threshold line (0.8)
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: [candleIndexes[0], candleIndexes[candleIndexes.length - 1]],
      y: [0.8, 0.8],
      yaxis: 'y5',
      line: { color: 'rgba(239, 68, 68, 0.4)', width: 1, dash: 'dash' },
      hoverinfo: 'skip',
      showlegend: false,
    });

    return traces;
  },
  buildShapes: () => [],
};
