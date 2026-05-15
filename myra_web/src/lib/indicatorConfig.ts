export interface LiqVoidSettings {
  minAtrMultiplier: number;
  maxVolumeMultiplier: number;
  minStrengthScore: number;
  hideFilledVoids: boolean;
  volatilityScaling: boolean;
}

export interface SmpSettings {
  minVolumeSpike: number;
  minDeliveryPct: number;
  accumulationCloseZone: number;
  distributionCloseZone: number;
  hideIndecision: boolean;
  showAccumulation: boolean;
  showDistribution: boolean;
  volatilityScaling: boolean;
}

export const DEFAULT_LIQ_VOID_SETTINGS: LiqVoidSettings = {
  minAtrMultiplier: 1.5,
  maxVolumeMultiplier: 0.8,
  minStrengthScore: 50,
  hideFilledVoids: false,
  volatilityScaling: true
};

export const DEFAULT_SMP_SETTINGS: SmpSettings = {
  minVolumeSpike: 1.5,
  minDeliveryPct: 60,
  accumulationCloseZone: 0.7,
  distributionCloseZone: 0.3,
  hideIndecision: true,
  showAccumulation: true,
  showDistribution: true,
  volatilityScaling: true
};

export const VOID_THRESHOLDS: Record<string, { minAtrMultiplier: number, maxVolumeMultiplier: number }> = {
    'Large Cap (N50)': { minAtrMultiplier: 1.2, maxVolumeMultiplier: 0.9 },
    'Large Cap (N100)': { minAtrMultiplier: 1.3, maxVolumeMultiplier: 0.85 },
    'Broader Market (N500)': { minAtrMultiplier: 1.5, maxVolumeMultiplier: 0.8 },
    'Nifty Small Cap 250': { minAtrMultiplier: 1.8, maxVolumeMultiplier: 0.7 },
    'Deep Frontier': { minAtrMultiplier: 2.0, maxVolumeMultiplier: 0.6 }
};

export const SMP_THRESHOLDS: Record<string, { minVolumeSpike: number, minDeliveryPct: number }> = {
    'Large Cap (N50)': { minVolumeSpike: 1.3, minDeliveryPct: 55 },
    'Large Cap (N100)': { minVolumeSpike: 1.4, minDeliveryPct: 58 },
    'Broader Market (N500)': { minVolumeSpike: 1.6, minDeliveryPct: 62 },
    'Nifty Small Cap 250': { minVolumeSpike: 1.8, minDeliveryPct: 65 },
    'Deep Frontier': { minVolumeSpike: 2.0, minDeliveryPct: 70 }
};

export type VolatilityRegime = 'low' | 'normal' | 'high';

export function getVolatilityRegime(currentAtr: number, historicalAtr: number): VolatilityRegime {
  if (currentAtr < historicalAtr * 0.8) return 'low';
  if (currentAtr > historicalAtr * 1.5) return 'high';
  return 'normal';
}

export function applyVolatilityScalar(value: number, regime: VolatilityRegime, scalingEnabled: boolean): number {
  if (!scalingEnabled) return value;
  if (regime === 'low') return value * 0.8;
  if (regime === 'high') return value * 1.2;
  return value;
}
