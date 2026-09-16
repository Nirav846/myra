/**
 * Advanced Chart V2 - Phase 4 Implementation
 *
 * New chart component built from the ground up with modular architecture.
 * Phase 4: SMC Indicators integration (Order Blocks, Swing Points, FVGs, Divergence).
 *
 * @module AdvancedChartV2
 */

import { useState, useMemo, memo, useCallback, useEffect } from 'react';
import Plot from 'react-plotly.js';
import type { PlotData, Layout } from 'plotly.js-dist-min';
import { buildBaseLayout, DEFAULT_PLOTLY_CONFIG } from '../components/chart/ChartLayout';
import { buildCandlestickTrace, extractDates, getPriceRange } from '../components/chart/CandlestickRenderer';
import { buildVolumeDeliveryPane, buildVolumePaneLayout, getVolumeStats } from '../components/chart/panes/VolumeDeliveryPane';
import { defaultFixture, getAllFixtures, FixtureScenario } from '../components/chart/fixtures/chartFixtures';
import { IndicatorRegistry, type IndicatorConfig } from '../components/chart/registry/IndicatorRegistry';
import { SMAIndicator, type SMAConfig } from '../components/chart/overlays/SMAOverlay';
import { VWAPIndicator, type VWAPConfig } from '../components/chart/overlays/VWAPOverlay';
import { FVGIndicator, type FVGConfig } from '../components/chart/overlays/FVGOverlay';
import { renderSMCIndicators, type SMCConfig } from '../components/chart/smc/SMCRegistry';
import CrosshairOverlay from '../components/chart/CrosshairOverlay';
import { AnnotationLayer, type Annotation } from '../components/chart/AnnotationLayer';
import { ChartSettingsPanel, type ChartSettings, DEFAULT_THEMES } from '../components/chart/ChartSettingsPanel';
import Crosshair from '../components/chart/Crosshair';

/**
 * Indicator groupings for dropdown organization
 */
const INDICATOR_GROUPS = {
  'Moving Averages': [
    { id: 'sma_20', label: 'SMA (20)', type: 'sma', period: 20, color: '#2962ff' },
    { id: 'sma_50', label: 'SMA (50)', type: 'sma', period: 50, color: '#ff6d00' },
    { id: 'sma_200', label: 'SMA (200)', type: 'sma', period: 200, color: '#9c27b0' },
  ],
  'Volume': [
    { id: 'vwap', label: 'VWAP', type: 'vwap', color: '#ff6d00' },
  ],
  'SMC': [
    { id: 'fvg', label: 'Fair Value Gaps', type: 'fvg', color: '#ffd700' },
    { id: 'orderblocks', label: 'Order Blocks', type: 'smc_ob', color: '#22c55e' },
    { id: 'swings', label: 'Swing Points', type: 'smc_swings', color: '#a855f7' },
    { id: 'divergence', label: 'Price-Delivery Div', type: 'smc_div', color: '#f59e0b' },
  ],
};

/**
 * Props for AdvancedChartV2 component
 */
interface AdvancedChartV2Props {
  /** Optional fixture scenario to render (uses default if not provided) */
  fixture?: FixtureScenario;
  /** Enable debug mode with extra logging */
  debug?: boolean;
  /** Callback when chart is ready (for testing) */
  onReady?: () => void;
}

/**
 * Main chart component - Phase 3: Overlay Indicators with Registry
 *
 * Features:
 * - Renders candlestick chart from static fixture data
 * - Adds combined volume + delivery pane below price chart
 * - Registry-driven overlay indicators (SMA, VWAP, FVG)
 * - Grouped dropdown UI for indicator selection
 * - Config-based indicator customization
 * - Uses modular renderer components
 */
export const AdvancedChartV2 = memo(({
  fixture = defaultFixture,
  debug = false,
  onReady
}: AdvancedChartV2Props) => {
  // Internal state
  const [isReady, setIsReady] = useState(false);
  const [activeIndicators, setActiveIndicators] = useState<Set<string>>(() => {
    // Default: SMA 20 enabled
    return new Set(['sma_20']);
  });
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set(['Moving Averages']));

  // Phase 6: Feature parity state
  const [showCrosshair, setShowCrosshair] = useState(true);
  const [crosshairPos, setCrosshairPos] = useState<{ x: number; y: number } | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [annotationMode, setAnnotationMode] = useState(false);
  const [annotationType, setAnnotationType] = useState<Annotation['type']>('hline');
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [chartSettings, setChartSettings] = useState<ChartSettings>({
    theme: DEFAULT_THEMES[0],
    showVolume: true,
    showGrid: true,
    showCrosshair: true,
    crosshairStyle: 'solid',
    candleWidth: 0.8,
    showWicks: true,
  });

  // Register indicators on mount
  useEffect(() => {
    // Clear any existing registrations
    IndicatorRegistry.clear();

    // Register SMA indicator (will be used for all SMA periods)
    IndicatorRegistry.register(SMAIndicator);

    // Register VWAP indicator
    IndicatorRegistry.register(VWAPIndicator);

    // Register FVG indicator
    IndicatorRegistry.register(FVGIndicator);

    return () => {
      IndicatorRegistry.clear();
    };
  }, []);

  // Build chart data using pure renderer functions
  const { traces, dates, layout, volumeStats, shapes } = useMemo(() => {
    if (debug) {
      console.log('[ChartV2] Building chart for fixture:', fixture.name);
    }

    // Build candlestick traces
    const candleTraces = buildCandlestickTrace(fixture.candles, {
      candleColorUp: '#26a69a',
      candleColorDown: '#ef5350',
      wickColorUp: '#26a69a',
      wickColorDown: '#ef5350',
      candleWidth: 0.8,
      showWicks: true,
    });

    // Build volume + delivery pane traces
    const volumeTraces = buildVolumeDeliveryPane(fixture.candles, {
      showDeliverySeparate: false,
      deliveryIntensity: 1.0,
      showDeliverySMA: true,
      volumeOpacity: 0.6,
      colorScheme: 'standard',
    });

    // Build overlay indicator traces from registry
    const indicatorTraces: any[] = [];
    const indicatorShapes: any[] = [];
    const indicatorAnnotations: any[] = [];

    // Track which SMC indicators are active
    const smcConfig: Partial<SMCConfig> = {
      visible: false,
      showConviction: true,
      showMitigation: true,
      showDivergence: activeIndicators.has('divergence'),
    };
    let hasSMCIndicators = false;

    activeIndicators.forEach(indicatorId => {
      // Find indicator config from groups
      let indicatorConfig: any = null;

      Object.values(INDICATOR_GROUPS).forEach(group => {
        const found = group.find((i: any) => i.id === indicatorId);
        if (found) {
          indicatorConfig = found;
        }
      });

      if (!indicatorConfig) return;

      // Handle SMC indicators specially (they use a combined renderer)
      if (['orderblocks', 'swings', 'divergence'].includes(indicatorId)) {
        hasSMCIndicators = true;
        smcConfig.visible = true;
        return;
      }

      const indicatorModule = IndicatorRegistry.get(indicatorConfig.type);
      if (!indicatorModule) return;

      // Build config for this indicator instance
      const config: any = {
        id: indicatorConfig.id,
        type: indicatorModule.config.type,
        visible: true,
        color: indicatorConfig.color,
        zIndex: 10,
        settings: {},
      };

      // Add type-specific config
      if (indicatorConfig.type === 'sma') {
        (config as SMAConfig).period = indicatorConfig.period;
      } else if (indicatorConfig.type === 'vwap') {
        (config as VWAPConfig).showDeviationBands = false;
      } else if (indicatorConfig.type === 'fvg') {
        (config as FVGConfig).showBullish = true;
        (config as FVGConfig).showBearish = true;
        (config as FVGConfig).minGapSize = 0.001;
      }

      // Render indicator
      const results = indicatorModule.render(fixture.candles, config);

      results.forEach(result => {
        if (result.type === 'shape_collection') {
          indicatorShapes.push(...result.shapes);
          indicatorAnnotations.push(...result.annotations);
        } else {
          indicatorTraces.push(result);
        }
      });
    });

    // Render SMC indicators if any are active
    if (hasSMCIndicators) {
      const smcResult = renderSMCIndicators(fixture.candles, smcConfig);
      indicatorShapes.push(...smcResult.shapes);
      indicatorAnnotations.push(...smcResult.annotations);
      indicatorTraces.push(...smcResult.traces);
    }

    // Combine all traces
    const allTraces = [...candleTraces, ...volumeTraces, ...indicatorTraces];

    // Extract dates for x-axis
    const dateArray = extractDates(fixture.candles);

    // Get price range for y-axis
    const priceRange = getPriceRange(fixture.candles);

    // Get volume stats for display
    const volStats = getVolumeStats(fixture.candles);

    // Build multi-pane layout
    const chartLayout = buildBaseLayout({
      showGrid: true,
      gridColor: '#2a2c34',
      backgroundColor: '#1a1c24',
      textColor: '#888899',
    }) as any;

    // Update x-axis categories
    chartLayout.xaxis.categories = dateArray;

    // Configure y-axis for price with padding
    chartLayout.yaxis.range = [priceRange.min, priceRange.max];

    // Add secondary y-axis for volume pane
    chartLayout.yaxis2 = {
      ...buildVolumePaneLayout(),
      anchor: 'free',
      overlaying: 'y',
      side: 'right',
      position: 0.95,
    };

    // Add shapes and annotations from indicators
    if (indicatorShapes.length > 0) {
      chartLayout.shapes = indicatorShapes;
    }
    if (indicatorAnnotations.length > 0) {
      chartLayout.annotations = indicatorAnnotations;
    }

    return {
      traces: allTraces,
      dates: dateArray,
      layout: chartLayout,
      volumeStats: volStats,
      shapes: indicatorShapes,
      annotations: indicatorAnnotations,
    };
  }, [fixture, debug, activeIndicators]);

  // Handle initial render complete
  const handleInitialized = useCallback(() => {
    if (!isReady) {
      setIsReady(true);
      if (onReady) {
        onReady();
      }
      if (debug) {
        console.log('[ChartV2] Chart initialized with', fixture.candles.length, 'candles');
      }
    }
  }, [isReady, onReady, debug, fixture.candles.length]);

  // Handle relayout (zoom/pan)
  const handleRelayout = useCallback((event: any) => {
    if (debug) {
      console.log('[ChartV2] Relayout event:', event);
    }
    // Phase 1: Just log, actual viewport handling in Phase 5
  }, [debug]);

  // Toggle indicator visibility
  const toggleIndicator = useCallback((indicatorId: string) => {
    setActiveIndicators(prev => {
      const next = new Set(prev);
      if (next.has(indicatorId)) {
        next.delete(indicatorId);
      } else {
        next.add(indicatorId);
      }
      return next;
    });
  }, []);

  // Toggle group expansion
  const toggleGroup = useCallback((groupName: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  }, []);

  // Phase 6: Annotation mode toggle
  const toggleAnnotationMode = useCallback((type?: Annotation['type']) => {
    if (type) {
      setAnnotationType(type);
    }
    setAnnotationMode(prev => !prev);
  }, []);

  // Handle annotation changes
  const handleAnnotationsChange = useCallback((newAnnotations: Annotation[]) => {
    setAnnotations(newAnnotations);
  }, []);

  // Get price range for annotation layer
  const priceRange = useMemo(() => {
    const range = getPriceRange(fixture.candles);
    return [range.min, range.max] as [number, number];
  }, [fixture.candles]);

  if (!fixture.candles || fixture.candles.length === 0) {
    return (
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex items-center justify-center h-[500px]">
        <div className="text-gray-400">No data available</div>
      </div>
    );
  }

  return (
    <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden chart-v2-container relative">
      {/* Header */}
      <div className="h-10 bg-[#2a2c34]/50 border-b border-[#ffffff1a] flex items-center px-4 justify-between">
        <div className="flex gap-2 items-center">
          <span className="font-semibold text-white">{fixture.name}</span>
          <span className="text-xs text-gray-400">{fixture.candles.length} candles</span>
        </div>
        <div className="text-xs text-gray-500">
          {fixture.candles[0]?.date} → {fixture.candles[fixture.candles.length - 1]?.date}
        </div>
        {/* Quick annotation tools */}
        <div className="flex gap-1">
          <button
            onClick={() => toggleAnnotationMode('hline')}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              annotationMode && annotationType === 'hline'
                ? 'bg-blue-600 text-white'
                : 'bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44]'
            }`}
            title="Horizontal Line"
          >
            ─
          </button>
          <button
            onClick={() => toggleAnnotationMode('vline')}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              annotationMode && annotationType === 'vline'
                ? 'bg-blue-600 text-white'
                : 'bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44]'
            }`}
            title="Vertical Line"
          >
            │
          </button>
          <button
            onClick={() => toggleAnnotationMode('text')}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              annotationMode && annotationType === 'text'
                ? 'bg-blue-600 text-white'
                : 'bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44]'
            }`}
            title="Text Label"
          >
            T
          </button>
          <button
            onClick={() => toggleAnnotationMode('trendline')}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              annotationMode && annotationType === 'trendline'
                ? 'bg-blue-600 text-white'
                : 'bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44]'
            }`}
            title="Trend Line"
          >
            ╱
          </button>
          <button
            onClick={() => setSettingsPanelOpen(prev => !prev)}
            className="px-2 py-1 text-xs rounded bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44] transition-colors"
            title="Settings"
          >
            ⚙
          </button>
        </div>
      </div>

      {/* Indicator Controls */}
      <div className="h-auto min-h-[40px] bg-[#1e2028] border-b border-[#ffffff1a] px-4 py-2">
        <div className="flex flex-wrap gap-2">
          {Object.entries(INDICATOR_GROUPS).map(([groupName, indicators]) => (
            <div key={groupName} className="relative">
              <button
                onClick={() => toggleGroup(groupName)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  expandedGroups.has(groupName)
                    ? 'bg-blue-600 text-white'
                    : 'bg-[#2a2c34] text-gray-300 hover:bg-[#3a3c44]'
                }`}
              >
                {groupName} ▾
              </button>

              {expandedGroups.has(groupName) && (
                <div className="absolute top-full left-0 mt-1 bg-[#2a2c34] border border-[#ffffff1a] rounded shadow-lg z-50 min-w-[180px]">
                  {indicators.map((indicator: any) => {
                    const isActive = activeIndicators.has(indicator.id);
                    return (
                      <button
                        key={indicator.id}
                        onClick={() => toggleIndicator(indicator.id)}
                        className={`w-full px-4 py-2 text-left text-xs flex items-center justify-between hover:bg-[#3a3c44] transition-colors ${
                          isActive ? 'text-blue-400' : 'text-gray-300'
                        }`}
                      >
                        <span>{indicator.label}</span>
                        <div
                          className="w-3 h-3 rounded-sm"
                          style={{
                            backgroundColor: isActive ? indicator.color : 'transparent',
                            border: `1px solid ${indicator.color}`,
                          }}
                        />
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative" style={{ height: '500px' }}>
        <Plot
          data={traces as PlotData[]}
          layout={layout as Layout}
          config={DEFAULT_PLOTLY_CONFIG}
          style={{ width: '100%', height: '100%' }}
          useResizeHandler={true}
          onInitialized={handleInitialized}
          onRelayout={handleRelayout}
        />

        {/* Crosshair Overlay */}
        {showCrosshair && crosshairPos && (
          <Crosshair
            x={crosshairPos.x}
            y={crosshairPos.y}
            dates={dates}
            style={chartSettings.crosshairStyle}
          />
        )}

        {/* Annotation Layer */}
        <AnnotationLayer
          annotations={annotations}
          onChange={handleAnnotationsChange}
          chartWidth={800} // Will be updated with actual width in Phase 7
          chartHeight={500}
          annotationMode={annotationMode}
          annotationType={annotationType}
          xCategories={dates}
          yRange={priceRange}
        />

        {/* Settings Panel */}
        <ChartSettingsPanel
          settings={chartSettings}
          onChange={setChartSettings}
          collapsed={!settingsPanelOpen}
          onToggleCollapse={() => setSettingsPanelOpen(prev => !prev)}
        />

        {/* Debug overlay (optional) */}
        {debug && (
          <div className="absolute top-2 left-2 bg-black/70 text-green-400 text-xs p-2 rounded font-mono">
            <div>V2 | {fixture.name}</div>
            <div>Candles: {fixture.candles.length}</div>
            <div>Traces: {traces.length}</div>
            <div>Indicators: {activeIndicators.size}</div>
            <div>Shapes: {shapes.length}</div>
            <div>Avg Vol: {(volumeStats.avgVolume / 1000).toFixed(1)}K</div>
            <div>Avg Del: {volumeStats.avgDeliveryPct}%</div>
            <div>Ready: {isReady ? '✓' : '...'}</div>
            <div>Annot: {annotations.length}</div>
          </div>
        )}
      </div>
    </div>
  );
});

AdvancedChartV2.displayName = 'AdvancedChartV2';

export default AdvancedChartV2;
