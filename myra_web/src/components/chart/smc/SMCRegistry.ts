/**
 * SMC Registry
 *
 * Central registry for Smart Money Concepts indicators.
 * Merges Order Blocks, Swing Points, FVGs, Divergence, and delivery-backed indicators
 * (DWAP, Delivery Thrust, DA-AD) into unified interface.
 */

import type { Candle } from '../../../core/technical-analysis/types';
import { detectOrderBlocksFromData, buildOrderBlockShapes, buildOrderBlockAnnotations, type OrderBlock } from './OrderBlocks';
import { detectSwingPointsFromData, buildSwingPointMarkers, type SwingPoint } from './SwingPoints';
import { detectFVGsFromData, buildFVGExtendedShapes, buildFVGExtendedAnnotations, type FairValueGap } from './FairValueGapsExtended';
import { detectDivergencesFromData, buildDivergenceOscillatorTrace, buildDivergenceMarkers, type DivergenceSignal } from './DeliveryDivergence';
import { detectLiquidityVoids, buildLiquidityVoidShapes, buildLiquidityVoidAnnotations, type LiquidityVoid, type LiquidityVoidsConfig, DEFAULT_CONFIG as DEFAULT_LIQUIDITY_VOIDS_CONFIG } from './LiquidityVoids';
import { calculateDWAP, renderDWAP, renderDWAPBands, type DWAPConfig, DEFAULT_DWAP_CONFIG } from '../indicators/DWAPOverlay';
import { identifyThrustCandles, renderDeliveryThrust, getThrustStatistics, type DeliveryThrustConfig, DEFAULT_DELIVERY_THRUST_CONFIG } from '../indicators/DeliveryThrustCandles';
import { calculateDAAD, renderDAAD, detectDivergences as detectDAADDivergences, renderDivergences as renderDAADDivergences, type DAADConfig, DEFAULT_DAAD_CONFIG } from '../indicators/DeliveryAdjustedAD';
import { calculateDeliveryProfile, buildDeliveryProfileTraces, type DeliveryProfileConfig, DEFAULT_CONFIG as DEFAULT_DELIVERY_PROFILE_CONFIG } from './DeliveryProfile';
import { detectDeliveryZones, buildDeliveryZoneShapes, buildDeliveryZoneAnnotations, type DeliveryZonesConfig, DEFAULT_CONFIG as DEFAULT_DELIVERY_ZONES_CONFIG } from './DeliverySupplyDemandZones';
import { detectAccumulationStreaks, buildAccumulationShapes, buildAccumulationAnnotations, type AccumulationStreakConfig, DEFAULT_CONFIG as DEFAULT_ACCUMULATION_CONFIG } from './SilentAccumulationStreak';

export interface SMCConfig {
  id: string;
  visible: boolean;
  showConviction: boolean; // For Order Blocks
  showMitigation: boolean; // For FVGs
  showDivergence: boolean; // For Divergence Oscillator
  swingLookback: number;
  obLookback: number;
  fvgMinPercent: number;
  // Liquidity Voids
  showLiquidityVoids: boolean;
  liquidityVoidsBucket: string;
  liquidityVoidsConfig?: Partial<LiquidityVoidsConfig>;
  // Delivery-backed indicators
  showDWAP: boolean;
  showDWAPBands: boolean;
  showDeliveryThrust: boolean;
  showDAAD: boolean;
  showDAADHistogram: boolean;
  dwapConfig?: Partial<DWAPConfig>;
  thrustConfig?: Partial<DeliveryThrustConfig>;
  daadConfig?: Partial<DAADConfig>;
  // Experimental indicators (UNVALIDATED — require calibration)
  showDeliveryProfile: boolean;
  deliveryProfileConfig?: Partial<DeliveryProfileConfig>;
  showSupplyDemandZones: boolean;
  supplyDemandZonesConfig?: Partial<DeliveryZonesConfig>;
  showSilentAccumulation: boolean;
  silentAccumulationConfig?: Partial<AccumulationStreakConfig>;
}

const DEFAULT_SMC_CONFIG: SMCConfig = {
  id: 'smc',
  visible: true,
  showConviction: true,
  showMitigation: true,
  showDivergence: false,
  swingLookback: 5,
  obLookback: 5,
  fvgMinPercent: 0.001,
  // Liquidity Voids defaults
  showLiquidityVoids: false,
  liquidityVoidsBucket: 'Broader Market (N500)',
  liquidityVoidsConfig: {},
  // Delivery-backed indicators defaults
  showDWAP: false,
  showDWAPBands: false,
  showDeliveryThrust: false,
  showDAAD: false,
  showDAADHistogram: false,
  // Experimental indicator defaults (UNVALIDATED)
  showDeliveryProfile: false,
  deliveryProfileConfig: {},
  showSupplyDemandZones: false,
  supplyDemandZonesConfig: {},
  showSilentAccumulation: false,
  silentAccumulationConfig: {},
};

export interface SMCRenderResult {
  shapes: any[];
  annotations: any[];
  traces: any[];
  orderBlocks: OrderBlock[];
  swingPoints: SwingPoint[];
  fvgZones: FairValueGap[];
  divergences: DivergenceSignal[];
  liquidityVoids: LiquidityVoid[];
  // Delivery-backed indicator data
  dwapEnabled: boolean;
  thrustCandlesCount: number;
  daadEnabled: boolean;
  liquidityVoidsEnabled: boolean;
  // Experimental indicator data
  deliveryProfileEnabled: boolean;
  supplyDemandZonesEnabled: boolean;
  silentAccumulationEnabled: boolean;
}

/**
 * Render all SMC indicators
 */
export function renderSMCIndicators(
  candles: Candle[],
  config: Partial<SMCConfig> = {}
): SMCRenderResult {
  const fullConfig = { ...DEFAULT_SMC_CONFIG, ...config };

  const shapes: any[] = [];
  const annotations: any[] = [];
  const traces: any[] = [];
  let orderBlocks: OrderBlock[] = [];
  let swingPoints: SwingPoint[] = [];
  let fvgZones: FairValueGap[] = [];
  let divergences: DivergenceSignal[] = [];
  let liquidityVoids: LiquidityVoid[] = [];

  // Delivery-backed indicator state
  let dwapEnabled = false;
  let thrustCandlesCount = 0;
  let daadEnabled = false;
  let liquidityVoidsEnabled = false;

  // Experimental indicator state
  let deliveryProfileEnabled = false;
  let supplyDemandZonesEnabled = false;
  let silentAccumulationEnabled = false;

  if (!fullConfig.visible) {
    return {
      shapes,
      annotations,
      traces,
      orderBlocks,
      swingPoints,
      fvgZones,
      divergences,
      liquidityVoids,
      dwapEnabled,
      thrustCandlesCount,
      daadEnabled,
      liquidityVoidsEnabled,
      deliveryProfileEnabled,
      supplyDemandZonesEnabled,
      silentAccumulationEnabled,
    };
  }

  // Detect Order Blocks
  orderBlocks = detectOrderBlocksFromData(candles, fullConfig.obLookback);
  const obShapes = buildOrderBlockShapes(orderBlocks);
  const obAnnotations = buildOrderBlockAnnotations(orderBlocks);
  shapes.push(...obShapes);
  annotations.push(...obAnnotations);

  // Detect Swing Points
  swingPoints = detectSwingPointsFromData(candles, fullConfig.swingLookback);
  const swingTraces = buildSwingPointMarkers(swingPoints);
  traces.push(...swingTraces);

  // Detect FVGs
  fvgZones = detectFVGsFromData(candles, fullConfig.fvgMinPercent);
  const fvgShapes = buildFVGExtendedShapes(fvgZones);
  const fvgAnnotations = buildFVGExtendedAnnotations(fvgZones);
  shapes.push(...fvgShapes);
  annotations.push(...fvgAnnotations);

  // Detect Liquidity Voids
  if (fullConfig.showLiquidityVoids) {
    liquidityVoidsEnabled = true;
    const liqVoidsConfig = { ...DEFAULT_LIQUIDITY_VOIDS_CONFIG, ...fullConfig.liquidityVoidsConfig };
    liquidityVoids = detectLiquidityVoids(candles, liqVoidsConfig, fullConfig.liquidityVoidsBucket);
    const liqVoidShapes = buildLiquidityVoidShapes(liquidityVoids, candles.map(c => c.date), liqVoidsConfig);
    const liqVoidAnnotations = buildLiquidityVoidAnnotations(liquidityVoids, candles.map(c => c.date), liqVoidsConfig);
    shapes.push(...liqVoidShapes);
    annotations.push(...liqVoidAnnotations);
  }

  // Detect Divergences (only if enabled)
  if (fullConfig.showDivergence) {
    divergences = detectDivergencesFromData(candles, 10);
    const divTraces = buildDivergenceOscillatorTrace(candles, 10);
    const divMarkers = buildDivergenceMarkers(divergences);
    traces.push(...divTraces, ...divMarkers);
  }

  // DWAP (Delivery-Weighted Average Price)
  if (fullConfig.showDWAP) {
    dwapEnabled = true;
    const dwapConfig = { ...DEFAULT_DWAP_CONFIG, ...fullConfig.dwapConfig };
    const dwapTrace = renderDWAP(candles, dwapConfig);
    traces.push(dwapTrace);

    if (fullConfig.showDWAPBands) {
      const bandTraces = renderDWAPBands(candles);
      traces.push(...bandTraces);
    }
  }

  // Delivery Thrust Candles
  if (fullConfig.showDeliveryThrust) {
    const thrustConfig = { ...DEFAULT_DELIVERY_THRUST_CONFIG, ...fullConfig.thrustConfig };
    const thrustTraces = renderDeliveryThrust(candles, thrustConfig);
    traces.push(...thrustTraces);
    thrustCandlesCount = identifyThrustCandles(candles, thrustConfig).length;
  }

  // DA-AD (Delivery-Adjusted Accumulation/Distribution)
  if (fullConfig.showDAAD) {
    daadEnabled = true;
    const daadConfig = { ...DEFAULT_DAAD_CONFIG, ...fullConfig.daadConfig, showHistogram: fullConfig.showDAADHistogram };
    const daadTraces = renderDAAD(candles, daadConfig);
    traces.push(...daadTraces);

    // Optional DA-AD divergence markers
    if (fullConfig.showDivergence) {
      const daadDivMarkers = renderDAADDivergences(candles);
      traces.push(...daadDivMarkers);
    }
  }

  // EXPERIMENTAL: Delivery Profile
  if (fullConfig.showDeliveryProfile) {
    deliveryProfileEnabled = true;
    const dpConfig = { ...DEFAULT_DELIVERY_PROFILE_CONFIG, ...fullConfig.deliveryProfileConfig };
    const dpTraces = buildDeliveryProfileTraces(candles, dpConfig);
    traces.push(...dpTraces);
  }

  // EXPERIMENTAL: Supply/Demand Zones
  if (fullConfig.showSupplyDemandZones) {
    supplyDemandZonesEnabled = true;
    const sdConfig = { ...DEFAULT_DELIVERY_ZONES_CONFIG, ...fullConfig.supplyDemandZonesConfig };
    const zones = detectDeliveryZones(candles, sdConfig);
    const dates = candles.map(c => c.date);
    const zoneShapes = buildDeliveryZoneShapes(zones, dates);
    const zoneAnnotations = buildDeliveryZoneAnnotations(zones, dates);
    shapes.push(...zoneShapes);
    annotations.push(...zoneAnnotations);
  }

  // EXPERIMENTAL: Silent Accumulation Streak
  if (fullConfig.showSilentAccumulation) {
    silentAccumulationEnabled = true;
    const saConfig = { ...DEFAULT_ACCUMULATION_CONFIG, ...fullConfig.silentAccumulationConfig };
    const streaks = detectAccumulationStreaks(candles, saConfig);
    const dates = candles.map(c => c.date);
    const priceRange = {
      min: Math.min(...candles.map(c => c.low)),
      max: Math.max(...candles.map(c => c.high)),
    };
    const saShapes = buildAccumulationShapes(streaks, dates, priceRange);
    const saAnnotations = buildAccumulationAnnotations(streaks, dates);
    shapes.push(...saShapes);
    annotations.push(...saAnnotations);
  }

  return {
    shapes,
    annotations,
    traces,
    orderBlocks,
    swingPoints,
    fvgZones,
    divergences,
    liquidityVoids,
    dwapEnabled,
    thrustCandlesCount,
    daadEnabled,
    liquidityVoidsEnabled,
    deliveryProfileEnabled,
    supplyDemandZonesEnabled,
    silentAccumulationEnabled,
  };
}

export default renderSMCIndicators;
