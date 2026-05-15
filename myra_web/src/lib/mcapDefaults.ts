export type McapBucket = 'Large Cap (N50)' | 'Large Cap (N100)' | 'Broader Market (N500)' | 'Nifty Small Cap 250' | 'Deep Frontier';

interface ThresholdSet {
  volumeSpike: number;        // multiplier over avg vol
  priceDeclinePct: number;    // min % decline for exhaustion
  deliveryFloor: number;      // min delivery % for institutional conviction
  deliverySpikePct: number;   // min delivery % for divergence
  rangeContraction: number;   // max range/ATR for spring coil
  volContraction: number;     // max vol/avgVol for spring coil
  nearLowPct: number;         // "near" multi‑month low
}

const DEFAULTS: Record<McapBucket, ThresholdSet> = {
  'Large Cap (N50)': {
    volumeSpike: 1.3, priceDeclinePct: 3, deliveryFloor: 55,
    deliverySpikePct: 75, rangeContraction: 0.50, volContraction: 0.6,
    nearLowPct: 2
  },
  'Large Cap (N100)': {
    volumeSpike: 1.4, priceDeclinePct: 4, deliveryFloor: 58,
    deliverySpikePct: 78, rangeContraction: 0.55, volContraction: 0.55,
    nearLowPct: 3
  },
  'Broader Market (N500)': {
    volumeSpike: 1.6, priceDeclinePct: 5, deliveryFloor: 62,
    deliverySpikePct: 82, rangeContraction: 0.60, volContraction: 0.5,
    nearLowPct: 3.5
  },
  'Nifty Small Cap 250': {
    volumeSpike: 1.8, priceDeclinePct: 6, deliveryFloor: 65,
    deliverySpikePct: 85, rangeContraction: 0.65, volContraction: 0.45,
    nearLowPct: 4
  },
  'Deep Frontier': {
    volumeSpike: 2.0, priceDeclinePct: 8, deliveryFloor: 70,
    deliverySpikePct: 90, rangeContraction: 0.70, volContraction: 0.4,
    nearLowPct: 5
  },
};

export function getThreshold(key: keyof ThresholdSet, bucket: McapBucket): number {
  return DEFAULTS[bucket]?.[key] ?? DEFAULTS['Broader Market (N500)'][key];
}
