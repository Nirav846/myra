import { Candle } from '../../technical-analysis/types';

/**
 * Delivery-Adjusted RSI
 * Enhanced RSI that weights volume by delivery percentage for accurate signals
 * during institutional activity
 */
export function buildDeliveryAdjustedRSI(
  data: Candle[],
  period: number = 14,
  deliveryWeight: number = 0.5 // Weight given to delivery-adjusted calculation
): any[] {
  if (data.length < period + 1) return [];

  const getDeliveryPct = (c: Candle): number => {
    if (typeof c.delivery_pct === 'number' && !isNaN(c.delivery_pct)) return c.delivery_pct;
    if (typeof c.deliveryPercentage === 'number' && !isNaN(c.deliveryPercentage)) return c.deliveryPercentage;
    return 50;
  };

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
      const delivPct = getDeliveryPct(slice[j]);
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

  // Marker per point only at overbought/oversold extremes to keep the pane readable
  const delivSignals: Array<number | null> = delivRsiValues.map(val => {
    if (val >= 70) return 70;
    if (val <= 30) return 30;
    return null;
  });
  const signalIndices = delivSignals
    .map((v, i) => (v !== null ? i : -1))
    .filter(i => i !== -1);
  const signalX = signalIndices.map(i => dates[i]);
  const signalY = signalIndices.map(i => delivRsiValues[i]);
  const signalLabels = signalIndices.map(i =>
    delivRsiValues[i] >= 70 ? 'Overbought' : 'Oversold'
  );
  const signalColors = signalIndices.map(i =>
    delivRsiValues[i] >= 70 ? '#ef4444' : '#22c55e'
  );

  return [
    // Standard RSI (thin reference)
    {
      type: 'scatter',
      x: dates,
      y: rsiValues,
      name: 'RSI (std)',
      mode: 'lines',
      line: { color: 'rgba(148, 163, 184, 0.45)', width: 1, shape: 'spline', smoothing: 0.4 },
      hovertemplate: '<b>Standard RSI: %{y:.2f}</b><br>Date: %{x}<br><extra></extra>',
      yaxis: 'y2',
      showlegend: true
    },
    // Delivery-Adjusted RSI (clean line, signal markers only at extremes)
    {
      type: 'scatter',
      x: dates,
      y: delivRsiValues,
      name: 'Del-Adj RSI',
      mode: 'lines',
      line: { color: '#f59e0b', width: 2, shape: 'spline', smoothing: 0.4 },
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
    // Extreme signal markers
    {
      type: 'scatter',
      x: signalX,
      y: signalY,
      name: 'Extremes',
      mode: 'markers',
      marker: { size: 7, color: signalColors, symbol: 'circle', line: { color: '#fff', width: 1 }, opacity: 0.9 },
      text: signalLabels,
      textposition: 'top center',
      textfont: { size: 10, color: signalColors, weight: 'bold' },
      hovertemplate: '<b>%{text}</b><br>Del-Adj RSI: %{y:.2f}<br>Date: %{x}<br><extra></extra>',
      yaxis: 'y2',
      showlegend: false
    },
    // Overbought Zone (70-100)
    {
      type: 'line',
      x: [dates[0], dates[dates.length - 1]],
      y: [70, 70],
      name: 'Overbought (70)',
      line: { color: 'rgba(239, 68, 68, 0.5)', width: 2, dash: 'dot' },
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
      line: { color: 'rgba(16, 185, 129, 0.5)', width: 2, dash: 'dot' },
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
      line: { color: 'rgba(255, 255, 255, 0.25)', width: 1, dash: 'solid' },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    // Shaded zones
    {
      type: 'scatter',
      x: [...dates, ...[...dates].reverse()],
      y: [...Array(dates.length).fill(70), ...Array(dates.length).fill(100)],
      name: 'Overbought Zone',
      fill: 'toself',
      fillcolor: 'rgba(239, 68, 68, 0.08)',
      line: { width: 0 },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    },
    {
      type: 'scatter',
      x: [...dates, ...[...dates].reverse()],
      y: [...Array(dates.length).fill(0), ...Array(dates.length).fill(30)],
      name: 'Oversold Zone',
      fill: 'toself',
      fillcolor: 'rgba(16, 185, 129, 0.08)',
      line: { width: 0 },
      hoverinfo: 'skip',
      yaxis: 'y2',
      showlegend: false
    }
  ];
}

export default buildDeliveryAdjustedRSI;
