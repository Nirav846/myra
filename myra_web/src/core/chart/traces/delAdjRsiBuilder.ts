import { CandleData } from '../../types';
import { PlotlyTrace } from '../types';

/**
 * Delivery-Adjusted RSI
 * Enhanced RSI that weights volume by delivery percentage for accurate signals
 * during institutional activity
 */
export function buildDeliveryAdjustedRSI(
  data: CandleData[],
  period: number = 14,
  deliveryWeight: number = 0.5 // Weight given to delivery-adjusted calculation
): PlotlyTrace[] {
  if (data.length < period + 1) return [];

  const rsiValues: number[] = [];
  const delivRsiValues: number[] = [];
  const dates: string[] = [];

  for (let i = period; i < data.length; i++) {
    const slice = data.slice(i - period, i + 1);
    
    // Standard RSI Calculation
    let gains = 0;
    let losses = 0;
    
    // Delivery-Adjusted RSI Calculation
    let delivGains = 0;
    let delivLosses = 0;
    
    for (let j = 1; j < slice.length; j++) {
      const change = slice[j].close - slice[j - 1].close;
      const delivPct = slice[j].deliveryPercentage || 50;
      const weight = 0.5 + (delivPct / 100) * deliveryWeight; // Weight: 0.5 to 1.0
      
      if (change > 0) {
        gains += change;
        delivGains += change * weight;
      } else {
        losses += Math.abs(change);
        delivLosses += Math.abs(change) * weight;
      }
    }
    
    const avgGain = gains / period;
    const avgLoss = losses / period;
    const rs = avgGain / (avgLoss || 1);
    const rsi = 100 - (100 / (1 + rs));
    
    const avgDelivGain = delivGains / period;
    const avgDelivLoss = delivLosses / period;
    const delivRs = avgDelivGain / (avgDelivLoss || 1);
    const delivRsi = 100 - (100 / (1 + delivRs));
    
    rsiValues.push(rsi);
    delivRsiValues.push(delivRsi);
    dates.push(slice[period].date);
  }

  // Create colors based on overbought/oversold zones
  const rsiColors = rsiValues.map(val => {
    if (val >= 70) return 'rgba(239, 68, 68, 0.8)'; // Overbought (red)
    if (val <= 30) return 'rgba(16, 185, 129, 0.8)'; // Oversold (emerald)
    return 'rgba(59, 130, 246, 0.6)'; // Neutral (blue)
  });

  const delivRsiColors = delivRsiValues.map(val => {
    if (val >= 70) return 'rgba(239, 68, 68, 0.9)'; // Overbought (red, more opaque)
    if (val <= 30) return 'rgba(16, 185, 129, 0.9)'; // Oversold (emerald, more opaque)
    return 'rgba(245, 158, 11, 0.7)'; // Neutral (amber, more opaque)
  });

  return [
    // Standard RSI
    {
      type: 'scatter',
      x: dates,
      y: rsiValues,
      name: 'RSI',
      mode: 'lines',
      line: {
        color: 'rgba(59, 130, 246, 0.5)',
        width: 2,
        shape: 'spline',
        smoothing: 0.4
      },
      hovertemplate:
        '<b>Standard RSI: %{y:.2f}</b><br>' +
        'Date: %{x}<br>' +
        '<extra></extra>',
      yaxis: 'y2',
      showlegend: true
    },
    // Delivery-Adjusted RSI
    {
      type: 'scatter',
      x: dates,
      y: delivRsiValues,
      name: 'Del-Adj RSI',
      mode: 'lines+markers',
      line: {
        color: 'rgba(245, 158, 11, 0.9)',
        width: 3,
        shape: 'spline',
        smoothing: 0.4
      },
      marker: {
        size: 6,
        color: delivRsiColors,
        opacity: 0.8
      },
      hovertemplate:
        '<b>Delivery-Adj RSI: %{y:.2f}</b><br>' +
        'Date: %{x}<br>' +
        'Signal: %{customdata[0]}<br>' +
        '<extra></extra>',
      customdata: delivRsiValues.map(val => {
        if (val >= 70) return 'Overbought ⚠️';
        if (val <= 30) return 'Oversold ✅';
        return 'Neutral';
      }),
      yaxis: 'y2',
      showlegend: true
    },
    // Overbought Zone (70-100)
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [70, 70],
      name: 'Overbought (70)',
      line: {
        color: 'rgba(239, 68, 68, 0.5)',
        width: 2,
        dash: 'dot'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    // Oversold Zone (0-30)
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [30, 30],
      name: 'Oversold (30)',
      line: {
        color: 'rgba(16, 185, 129, 0.5)',
        width: 2,
        dash: 'dot'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    // Centerline (50)
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [50, 50],
      name: 'Centerline (50)',
      line: {
        color: 'rgba(255, 255, 255, 0.3)',
        width: 1,
        dash: 'solid'
      },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    // Shaded zones
    {
      type: 'scatter',
      x: [...dates, ...dates.reverse()],
      y: [...Array(dates.length).fill(70), ...Array(dates.length).fill(100)],
      name: 'Overbought Zone',
      fill: 'toself',
      fillcolor: 'rgba(239, 68, 68, 0.1)',
      line: { width: 0 },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    {
      type: 'scatter',
      x: [...dates, ...dates.reverse()],
      y: [...Array(dates.length).fill(0), ...Array(dates.length).fill(30)],
      name: 'Oversold Zone',
      fill: 'toself',
      fillcolor: 'rgba(16, 185, 129, 0.1)',
      line: { width: 0 },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    }
  ];
}

export default buildDeliveryAdjustedRSI;
