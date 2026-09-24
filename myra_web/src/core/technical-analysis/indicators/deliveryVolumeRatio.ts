import type { Candle } from '../types';

export interface DeliveryData {
  date: string;
  deliveryPercent: number;
  volume?: number;
}

export interface DeliveryVolumeRatioResult {
  ratios: number[];
  signals: DVRSignal[];
  metadata: {
    averageRatio: number;
    currentRatio: number;
    signalCount: number;
    accumulationDays: number;
    distributionDays: number;
  };
}

export interface DVRSignal {
  id: string;
  index: number;
  type: 'accumulation' | 'distribution' | 'extreme_accumulation' | 'extreme_distribution';
  ratio: number;
  deliveryPercent: number;
  volume: number;
  avgDeliveryVolume: number;
  confidence: number;
}

/**
 * Calculates Delivery Volume Ratio (DVR)
 * Compares current delivery volume to 20-day average
 * >1.5x = Accumulation (bullish)
 * >2.0x = Extreme Accumulation (very bullish)
 * <0.8x = Distribution (bearish)
 * <0.5x = Extreme Distribution (very bearish)
 */
export function calculateDeliveryVolumeRatio(
  candles: Candle[],
  deliveryData: DeliveryData[]
): DeliveryVolumeRatioResult {
  if (candles.length < 20 || !deliveryData || deliveryData.length === 0) {
    return {
      ratios: [],
      signals: [],
      metadata: {
        averageRatio: 0,
        currentRatio: 0,
        signalCount: 0,
        accumulationDays: 0,
        distributionDays: 0,
      },
    };
  }

  const ratios: number[] = [];
  const signals: DVRSignal[] = [];
  let signalId = 0;
  
  const lookbackPeriod = 20;
  let accumulationDays = 0;
  let distributionDays = 0;

  for (let i = lookbackPeriod - 1; i < candles.length; i++) {
    const candle = candles[i];
    const delivery = deliveryData.find(d => d.date === candle.t);
    
    if (!delivery) {
      ratios.push(0);
      continue;
    }

    // Calculate current delivery volume
    const currentDeliveryVolume = candle.v * (delivery.deliveryPercent / 100);
    
    // Calculate 20-day average delivery volume
    let totalDeliveryVolume = 0;
    let count = 0;
    
    for (let j = i - lookbackPeriod + 1; j <= i; j++) {
      const pastCandle = candles[j];
      const pastDelivery = deliveryData.find(d => d.date === pastCandle.t);
      
      if (pastDelivery) {
        totalDeliveryVolume += pastCandle.v * (pastDelivery.deliveryPercent / 100);
        count++;
      }
    }
    
    const avgDeliveryVolume = count > 0 ? totalDeliveryVolume / count : 0;
    const ratio = avgDeliveryVolume > 0 ? currentDeliveryVolume / avgDeliveryVolume : 0;
    
    ratios.push(ratio);
    
    // Identify signals
    if (ratio > 2.0) {
      accumulationDays++;
      signals.push({
        id: `dvr_extreme_acc_${signalId++}`,
        index: i,
        type: 'extreme_accumulation',
        ratio,
        deliveryPercent: delivery.deliveryPercent,
        volume: candle.v,
        avgDeliveryVolume,
        confidence: Math.min(90 + (ratio - 2) * 10, 100),
      });
    } else if (ratio > 1.5) {
      accumulationDays++;
      signals.push({
        id: `dvr_acc_${signalId++}`,
        index: i,
        type: 'accumulation',
        ratio,
        deliveryPercent: delivery.deliveryPercent,
        volume: candle.v,
        avgDeliveryVolume,
        confidence: 70 + (ratio - 1.5) * 20,
      });
    } else if (ratio < 0.5) {
      distributionDays++;
      signals.push({
        id: `dvr_extreme_dist_${signalId++}`,
        index: i,
        type: 'extreme_distribution',
        ratio,
        deliveryPercent: delivery.deliveryPercent,
        volume: candle.v,
        avgDeliveryVolume,
        confidence: Math.min(90 + (0.5 - ratio) * 20, 100),
      });
    } else if (ratio < 0.8) {
      distributionDays++;
      signals.push({
        id: `dvr_dist_${signalId++}`,
        index: i,
        type: 'distribution',
        ratio,
        deliveryPercent: delivery.deliveryPercent,
        volume: candle.v,
        avgDeliveryVolume,
        confidence: 70 + (0.8 - ratio) * 30,
      });
    }
  }

  const validRatios = ratios.filter(r => r > 0);
  const averageRatio = validRatios.length > 0 
    ? validRatios.reduce((sum, r) => sum + r, 0) / validRatios.length 
    : 0;
  const currentRatio = ratios.length > 0 ? ratios[ratios.length - 1] : 0;

  return {
    ratios,
    signals,
    metadata: {
      averageRatio,
      currentRatio,
      signalCount: signals.length,
      accumulationDays,
      distributionDays,
    },
  };
}
