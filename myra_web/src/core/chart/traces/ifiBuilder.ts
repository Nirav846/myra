import { Candle } from '../../technical-analysis/types';

export interface IFIDataPoint {
  date: string;
  ifiValue: number;
  deliveryComponent: number;
  volumeComponent: number;
  momentumComponent: number;
}

/**
 * Institutional Flow Index (IFI)
 * Combines delivery percentage, volume ratio, and price momentum
 * Range: -100 to +100
 * >50: Strong accumulation (Green zone)
 * <-50: Strong distribution (Red zone)
 */
export function buildInstitutionalFlowIndex(
  data: Candle[],
  period: number = 20
): any[] {
  if (data.length < period) return [];

  const ifiValues: number[] = [];
  const dates: string[] = [];
  const deliveryComp: number[] = [];
  const volumeComp: number[] = [];
  const momentumComp: number[] = [];

  for (let i = period - 1; i < data.length; i++) {
    const slice = data.slice(i - period + 1, i + 1);
    
    // Delivery Component (-100 to +100)
    const avgDeliveryPct = slice.reduce((sum, c) => sum + (c.deliveryPercentage || 0), 0) / period;
    const deliveryScore = (avgDeliveryPct - 50) * 2; // Normalize to -100 to +100
    
    // Volume Component (-100 to +100)
    const avgVolume = slice.reduce((sum, c) => sum + (c.volume ?? 0), 0) / period;
    const currentVolume = data[i].volume ?? 0;
    const volumeRatio = currentVolume / avgVolume;
    const volumeScore = Math.min(Math.max((volumeRatio - 1) * 100, -100), 100);
    
    // Momentum Component (-100 to +100)
    const priceChange = (data[i].close - data[i - period + 1].close) / data[i - period + 1].close * 100;
    const momentumScore = Math.min(Math.max(priceChange * 10, -100), 100);
    
    // Combined IFI (weighted average)
    const ifiValue = (deliveryScore * 0.4 + volumeScore * 0.3 + momentumScore * 0.3);
    const clampedIFI = Math.min(Math.max(ifiValue, -100), 100);
    
    ifiValues.push(clampedIFI);
    dates.push(data[i].date);
    deliveryComp.push(deliveryScore);
    volumeComp.push(volumeScore);
    momentumComp.push(momentumScore);
  }

  // Create gradient colors based on IFI value
  const colors = ifiValues.map(val => {
    if (val > 50) return 'rgba(16, 185, 129, 0.8)'; // Strong accumulation (emerald)
    if (val > 0) return 'rgba(59, 130, 246, 0.6)'; // Moderate accumulation (blue)
    if (val > -50) return 'rgba(245, 158, 11, 0.6)'; // Moderate distribution (amber)
    return 'rgba(239, 68, 68, 0.8)'; // Strong distribution (red)
  });

  return [
    {
      type: 'bar',
      x: dates,
      y: ifiValues,
      name: 'IFI',
      marker: {
        color: colors,
        line: {
          color: 'rgba(255, 255, 255, 0.3)',
          width: 1
        }
      },
      hovertemplate: 
        '<b>IFI: %{y:.2f}</b><br>' +
        'Date: %{x}<br>' +
        'Delivery Score: %{customdata[0]:.2f}<br>' +
        'Volume Score: %{customdata[1]:.2f}<br>' +
        'Momentum Score: %{customdata[2]:.2f}<br>' +
        '<extra></extra>',
      customdata: dates.map((_, idx) => [
        deliveryComp[idx],
        volumeComp[idx],
        momentumComp[idx]
      ]),
      yaxis: 'y2',
      opacity: 0.9
    },
    {
      type: 'scatter',
      x: dates,
      y: ifiValues,
      name: 'IFI Signal',
      mode: 'lines',
      line: {
        color: 'rgba(255, 255, 255, 0.8)',
        width: 2,
        shape: 'spline',
        smoothing: 0.4
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    // Reference lines
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [50, 50],
      name: 'Accumulation Zone',
      line: {
        color: 'rgba(16, 185, 129, 0.5)',
        width: 2,
        dash: 'dot'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [-50, -50],
      name: 'Distribution Zone',
      line: {
        color: 'rgba(239, 68, 68, 0.5)',
        width: 2,
        dash: 'dot'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [0, 0],
      name: 'Neutral Line',
      line: {
        color: 'rgba(255, 255, 255, 0.3)',
        width: 1,
        dash: 'solid'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    }
  ];
}

export default buildInstitutionalFlowIndex;
