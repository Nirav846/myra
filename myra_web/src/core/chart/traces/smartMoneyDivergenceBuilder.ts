import { Candle } from '../../technical-analysis/types';

export interface DivergencePoint {
  date: string;
  priceValue: number;
  indicatorValue: number;
  type: 'bullish' | 'bearish';
  confidence: number;
}

/**
 * Smart Money Divergence Detector
 * Identifies bullish/bearish divergences between price action and institutional delivery flow
 */
export function buildSmartMoneyDivergence(
  data: Candle[],
  lookback: number = 10
): any[] {
  if (data.length < lookback * 2) return [];

  const divergencePoints: Array<{
    date: string;
    price: number;
    type: 'bullish' | 'bearish';
    confidence: number;
    priceStart: number;
    priceEnd: number;
    delivStart: number;
    delivEnd: number;
  }> = [];

  // Detect divergences using swing points
  for (let i = lookback; i < data.length - lookback; i++) {
    const leftSlice = data.slice(i - lookback, i);
    const rightSlice = data.slice(i, i + lookback);
    
    // Find price extremes
    const leftPriceLow = Math.min(...leftSlice.map(c => c.low));
    const leftPriceHigh = Math.max(...leftSlice.map(c => c.high));
    const rightPriceLow = Math.min(...rightSlice.map(c => c.low));
    const rightPriceHigh = Math.max(...rightSlice.map(c => c.high));
    
    // Find delivery percentage extremes
    const leftDelivAvg = leftSlice.reduce((sum, c) => sum + (c.deliveryPercentage || 0), 0) / lookback;
    const rightDelivAvg = rightSlice.reduce((sum, c) => sum + (c.deliveryPercentage || 0), 0) / lookback;
    
    // Bullish Divergence: Price makes lower low, Delivery makes higher low
    const isBullish = rightPriceLow < leftPriceLow && rightDelivAvg > leftDelivAvg;
    
    // Bearish Divergence: Price makes higher high, Delivery makes lower high
    const isBearish = rightPriceHigh > leftPriceHigh && rightDelivAvg < leftDelivAvg;
    
    if (isBullish || isBearish) {
      const confidence = Math.abs(rightDelivAvg - leftDelivAvg) / 50; // Normalize confidence
      
      divergencePoints.push({
        date: data[i].date,
        price: isBullish ? rightPriceLow : rightPriceHigh,
        type: isBullish ? 'bullish' : 'bearish',
        confidence: Math.min(confidence, 1),
        priceStart: isBullish ? leftPriceLow : leftPriceHigh,
        priceEnd: isBullish ? rightPriceLow : rightPriceHigh,
        delivStart: leftDelivAvg,
        delivEnd: rightDelivAvg
      });
    }
  }

  if (divergencePoints.length === 0) return [];

  // Create labels for divergences
  const labelDates = divergencePoints.map(d => d.date);
  const labelPrices = divergencePoints.map(d => d.price);
  const labelTexts = divergencePoints.map(d => 
    `${d.type === 'bullish' ? '🟢' : '🔴'} ${d.confidence > 0.7 ? 'Strong' : ''} Div`
  );
  const labelColors = divergencePoints.map(d => 
    d.type === 'bullish' ? '#10b981' : '#ef4444'
  );

  return [
    {
      type: 'scatter',
      x: labelDates,
      y: labelPrices,
      name: 'SM Divergence',
      mode: 'markers+text',
      marker: {
        size: 14,
        color: labelColors,
        symbol: 'diamond',
        line: {
          color: 'white',
          width: 2
        },
        opacity: 0.9
      },
      text: labelTexts,
      textposition: 'top center',
      textfont: {
        size: 11,
        weight: 'bold',
        color: labelColors
      },
      hovertemplate:
        '<b>%{text}</b><br>' +
        'Date: %{x}<br>' +
        'Price: %{y}<br>' +
        'Confidence: %{customdata[0]:.0%}<br>' +
        'Delivery Change: %{customdata[1]:.1f}%<br>' +
        '<extra></extra>',
      customdata: divergencePoints.map(d => [
        d.confidence,
        d.delivEnd - d.delivStart
      ])
    }
  ];
}

export default buildSmartMoneyDivergence;
