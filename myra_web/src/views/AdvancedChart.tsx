import { useState, useEffect, useMemo, useRef, memo, useCallback, lazy, Suspense } from 'react';
import { Librarian } from '../lib/Librarian';
const PlotlyCanvas = lazy(() => import('../components/chart/PlotlyCanvas').then(m => ({ default: m.PlotlyCanvas })));
import { ChartHeader } from '../components/chart/ChartHeader';
import { ChartSidebar } from '../components/chart/ChartSidebar';
import CrosshairOverlay from '../components/chart/CrosshairOverlay';
import type { CrosshairOverlayHandle } from '../components/chart/CrosshairOverlay';
import { createPortal } from 'react-dom';
import { X, BarChart2, Settings2, Crosshair, ZoomIn, ZoomOut, RefreshCw, Layers } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { SymbolSearch } from '../components/SymbolSearch';
import { useSettings } from '../lib/SettingsContext';
import { useCrosshair } from '../hooks/useCrosshair';
import { Button, ButtonGroup, IconButton } from '../components/ui/Button';

// Removed static import of aggregateData, using worker instead
import { useChartStore } from '../store/chartStore';
import { chartRegistry } from '../core/chart/registry';
import { TraceBuilderContext } from '../core/chart/traces/types';
import { resolveBucket } from '../lib/bucketUtils';
import { computeNakedPocs, nakedPocsToShapes } from '../lib/nakedPocTracker';
import { LiqVoidSettings, SmpSettings, DEFAULT_LIQ_VOID_SETTINGS, DEFAULT_SMP_SETTINGS } from '../lib/indicatorConfig';
import { computeLiquidityVoids, liqVoidsToShapes } from '../lib/liquidityVoid';
import { computeSmartMoneyPrints, smpToTraces } from '../lib/smartMoneyPrints';
import IndicatorSettingsPanel from '../components/IndicatorSettingsPanel';
import { createCandleIndexes, buildDateToIndexMap } from '../utils/chartCoords';
import { IndicatorWorker } from '../workers/indicatorWorker';
import * as Comlink from 'comlink';
import { decimateOHLCVData } from '../utils/dataDecimator';
import { buildSmartMoneyDivergence } from '../core/chart/traces/smartMoneyDivergenceBuilder';
import { buildDeliveryClusters } from '../core/chart/traces/deliveryClustersBuilder';
import { buildDeliveryAdjustedRSI } from '../core/chart/traces/delAdjRsiBuilder';
import { buildInstitutionalFlowIndex } from '../core/chart/traces/ifiBuilder';

// Lazy-load indicator modules - preloaded on mount
const INDICATOR_KEYS = ['sma', 'rsi', 'fvg', 'swings', 'volumeProfile', 'delIntensityCore', 'instBlocks', 'delAd', 'liqVoids', 'orderBlocks', 'equalHighsLows', 'breakerBlocks', 'premiumDiscount', 'deliveryTrend', 'deliveryVolumeRatio'];
const TRACE_BUILDER_KEYS = ['swings', 'vwap', 'sma', 'rsi', 'fvg', 'volumeProfile', 'delIntensityCore', 'instBlocks', 'delVwapBands', 'delAd', 'niftyOut', 'volume', 'delivery', 'orderBlocks', 'equalHighsLows', 'breakerBlocks', 'premiumDiscount', 'deliveryTrend', 'deliveryVolumeRatio'];
const LAYOUT_BUILDER_KEYS = ['fibonacci', 'liqVoids'];

const usePersistedState = <T,>(key: string, initialValue: T): [T, React.Dispatch<React.SetStateAction<T>>] => {
  const [state, setState] = useState<T>(() => {
    const saved = localStorage.getItem(key);
    if (saved !== null) {
      try {
        return JSON.parse(saved);
      } catch (e) {
        return initialValue;
      }
    }
    return initialValue;
  });

  useEffect(() => {
    const t = setTimeout(() => {
      localStorage.setItem(key, JSON.stringify(state));
    }, 300);
    return () => clearTimeout(t);
  }, [key, state]);

  return [state, setState];
};


const ChartItem = memo(({ sym, data, overlayToggles, paneToggles, perfToggles, settings, bucket, liqVoidSettings, smpSettings, crosshairEnabled }: any) => {
    const [modulesReady, setModulesReady] = useState(false);

    // Pre-load lazy indicator/trace/layout modules BEFORE ChartItemInner mounts,
    // so its useMemos read a populated registry (sync getters never trigger loading).
    useEffect(() => {
        let mounted = true;
        const loadModules = async () => {
            try {
                await Promise.all([
                    ...INDICATOR_KEYS.map(k => chartRegistry.getIndicator(k)),
                    ...TRACE_BUILDER_KEYS.map(k => chartRegistry.getTraceBuilder(k)),
                    ...LAYOUT_BUILDER_KEYS.map(k => chartRegistry.getLayoutBuilder(k))
                ]);
                if (mounted) setModulesReady(true);
            } catch (err) {
                console.error('Failed to lazy-load chart modules:', err);
                if (mounted) setModulesReady(true); // Continue even on error
            }
        };
        loadModules();
        return () => { mounted = false; };
    }, []);

    if (!data || !modulesReady) return (
        <div key={sym} className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex flex-col h-[500px] chart-container relative overflow-hidden">
            <div className="h-10 bg-[#2a2c34]/50 animate-pulse border-b border-[#ffffff1a] flex items-center px-4 justify-between">
                <div className="flex gap-2 items-center">
                    <div className="w-16 h-4 bg-white/10 rounded"></div>
                    <div className="w-10 h-3 bg-white/5 rounded"></div>
                </div>
            </div>
            <div className="flex-1 flex items-end justify-between px-4 pb-4 gap-1">
                {Array.from({ length: 40 }).map((_, i) => {
                    const height = 20 + Math.sin(i * 0.3) * 15 + Math.random() * 10;
                    return (
                        <div 
                            key={i} 
                            className="bg-white/5 w-full rounded-t-sm animate-pulse" 
                            style={{ height: `${height}%`, animationDelay: `${i * 0.05}s` }}
                        ></div>
                    );
                })}
            </div>
        </div>
    );
    return <ChartItemInner sym={sym} data={data} overlayToggles={overlayToggles} paneToggles={paneToggles} perfToggles={perfToggles} settings={settings} bucket={bucket} liqVoidSettings={liqVoidSettings} smpSettings={smpSettings} crosshairEnabled={crosshairEnabled} />;
}, (prev, next) => {
    return prev.sym === next.sym && 
           prev.data === next.data && 
           prev.overlayToggles === next.overlayToggles &&
           prev.paneToggles === next.paneToggles &&
           prev.perfToggles === next.perfToggles &&
           prev.settings === next.settings &&
           prev.bucket === next.bucket &&
    prev.liqVoidSettings === next.liqVoidSettings &&
    prev.smpSettings === next.smpSettings &&
    prev.crosshairEnabled === next.crosshairEnabled;
});

const ChartItemInner = ({ sym, data, overlayToggles, paneToggles, perfToggles, settings, bucket, liqVoidSettings, smpSettings, crosshairEnabled }: any) => {
    // Granular Zustand selectors to prevent unnecessary re-renders
    const viewport = useChartStore(state => state.viewport);
    const hoveredIndex = useChartStore(state => state.hoveredIndex);
    const plotRef = useRef<any>(null);
    const overlayHandleRef = useRef<CrosshairOverlayHandle | null>(null);
    const [worker, setWorker] = useState<Comlink.Remote<IndicatorWorker> | null>(null);

    // Initialize Web Worker for heavy indicator calculations
    useEffect(() => {
        let mounted = true;
        const initWorker = async () => {
            try {
                const workerInstance = new Worker(
                    new URL('../workers/indicatorWorker.ts', import.meta.url),
                    { type: 'module' }
                );
                const wrappedWorker = Comlink.wrap<IndicatorWorker>(workerInstance);
                if (mounted) setWorker(wrappedWorker);
            } catch (err) {
                console.warn('Failed to initialize indicator worker, falling back to main thread:', err);
                if (mounted) setWorker(null);
            }
        };
        initWorker();
        return () => {
            mounted = false;
            // Worker will be terminated automatically when component unmounts
        };
    }, []);

    // Pre-load is handled in ChartItem before ChartItemInner mounts (registry must be
    // populated before any useMemo reads getIndicatorSync/getTraceBuilderSync).

    // Granular selector to avoid re-renders when other store properties change
    const chartStoreSelectors = useMemo(() => ({
        viewport,
        hoveredIndex
    }), [viewport, hoveredIndex]);

    const dates = useMemo(() => (data ? data.map((d: any) => d.date) : []) as string[], [data]);

    const formatCrosshairDate = useCallback((idx: number) => {
      const d = data?.[idx]?.date;
      if (!d) return '';
      if (typeof d === 'string' && d.match(/^\d{4}-\d{2}-\d{2}/)) {
        return new Date(d).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
      }
      return String(d);
    }, [data]);

    useCrosshair(plotRef, overlayHandleRef, {
      enabled: crosshairEnabled,
      dates,
      dataLength: data?.length || 0,
      formatDate: formatCrosshairDate,
    });
    
    // Merge toggles for backward compatibility in internal logic
    const toggles = useMemo(() => ({
        ...overlayToggles,
        ...paneToggles,
        ...perfToggles
    }), [overlayToggles, paneToggles, perfToggles]);

    const xTickInfo = useMemo(() => {
        if (dates.length === 0) return { tickvals: [] as number[], ticktext: [] as string[] };
        const vp = viewport;
        let startIdx = 0;
        let endIdx = dates.length - 1;
        if (vp && isFinite(vp.startIndex) && isFinite(vp.endIndex)) {
            startIdx = Math.max(0, Math.floor(vp.startIndex));
            endIdx = Math.min(dates.length - 1, Math.ceil(vp.endIndex));
        }
        const visibleCount = endIdx - startIdx + 1;
        const maxTicks = toggles.performanceMode ? 5 : Math.max(3, Math.min(12, Math.floor(visibleCount / 15)));
        const step = Math.max(1, Math.floor(visibleCount / maxTicks));
        
        const tickvals: number[] = [];
        const ticktext: string[] = [];
        for (let i = startIdx; i <= endIdx; i += step) {
            tickvals.push(i);
            const d = dates[i];
            if (typeof d === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d)) {
                const dt = new Date(d);
                ticktext.push(dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' }));
            } else {
                ticktext.push(String(d));
            }
        }
        return { tickvals, ticktext };
    }, [dates, viewport, toggles.performanceMode]);

    const candleIndexes = useMemo(() => createCandleIndexes(data.length), [data.length]);
    const dateToIndex = useMemo(() => buildDateToIndexMap(dates), [dates]);

    // Virtualize long data series - decimate if > 5000 candles
    const MAX_DATA_POINTS = 2000;
    const decimatedData = useMemo(() => {
        if (!data || data.length <= MAX_DATA_POINTS) return data;
        console.debug(`[DataDecimator] Downsampling ${data.length} candles to ${MAX_DATA_POINTS} for performance`);
        return decimateOHLCVData(data, MAX_DATA_POINTS);
    }, [data]);

    // Use decimated data for all subsequent calculations
    const renderData = decimatedData || data;
    const renderDataLength = renderData?.length || 0;

    // Extract base data arrays from potentially decimated data
    const baseData = useMemo(() => {
        if (!renderData || renderData.length === 0) {
            return {
                opens: [], highs: [], lows: [], closes: [], volumes: [],
                vwap: [], deliveryFinal: [], deliveryPct: [], deliveryRatio: [],
                stockReturn: [], volComp: [], relVol: [], divScores: [],
                niftyOut: [], trendAlignment: [], volumeColors: [], deliveryColorsInverse: []
            };
        }
        const opens = renderData.map(d => d.open);
        const highs = renderData.map(d => d.high);
        const lows = renderData.map(d => d.low);
        const closes = renderData.map(d => d.close);
        const volumes = renderData.map(d => {
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume);
            return isNaN(vol) ? 0 : vol;
        });
        const vwap = renderData.map(d => d.vwap);
        const deliveryFinal = renderData.map(d => {
            const delVal = d.delivery_final ? Number(d.delivery_final) : 0;
            return isNaN(delVal) ? 0 : delVal;
        });
        const deliveryPct = renderData.map((d, i) => {
            if (d.delivery_pct != null && !isNaN(Number(d.delivery_pct))) return Number(d.delivery_pct);
            const delVal = deliveryFinal[i];
            const vol = Math.max(1, Number(d.volume) || 0);
            return (delVal / vol) * 100;
        });
        const deliveryRatio = renderData.map(d => d.delivery_ratio);
        const stockReturn = renderData.map(d => d.stock_return);
        const volComp = renderData.map(d => d.volatility_compression_score);
        const relVol = renderData.map(d => d.relative_volume_score);
        const divScores = renderData.map(d => d.delivery_divergence_score);
        const niftyOut = renderData.map(d => d.nifty_outperformance_score);
        const trendAlignment = renderData.map(d => d.trend_alignment);
        const volumeColors = renderData.map(d => d.close >= d.open ? '#22c55e' : '#ef4444');
        const deliveryColorsInverse = renderData.map(d => d.close >= d.open ? '#ef4444' : '#22c55e');
        
        return {
            opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio,
            stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse
        };
    }, [renderData]);

    // Heavy indicator calculations via Web Worker (with main thread fallback)
    const [workerResults, setWorkerResults] = useState<{
        deliveryObv?: number[];
        atr?: number[];
        swingsObj?: { swingHighs: number[]; swingLows: number[] };
    }>({});

    // Use worker for heavy calculations when available
    useEffect(() => {
        if (!worker || !data || data.length === 0) return;
        
        let cancelled = false;
        const requestId = Date.now();
        
        const calculateWithWorker = async () => {
            try {
                const [deliveryObvResult, atrResult, swingsResult] = await Promise.all([
                    worker.calculateIndicators(requestId, 'deliveryObv', data),
                    worker.calculateIndicators(requestId, 'atr', data),
                    toggles.showSwings || toggles.showBreakerBlocks ? worker.calculateIndicators(requestId, 'swings', data) : Promise.resolve(null)
                ]);
                
                if (!cancelled) {
                    setWorkerResults({
                        deliveryObv: deliveryObvResult as number[],
                        atr: atrResult as number[],
                        swingsObj: swingsResult as { swingHighs: number[]; swingLows: number[] } | null
                    });
                }
            } catch (err) {
                console.warn('Worker calculation failed, falling back to main thread:', err);
                if (!cancelled) setWorkerResults({});
            }
        };
        
        calculateWithWorker();
        
        return () => {
            cancelled = true;
        };
    }, [worker, data, toggles.showSwings, toggles.showBreakerBlocks]);

    // Delivery-Weighted OBV (main thread fallback)
    const deliveryObv = useMemo(() => {
        if (workerResults.deliveryObv !== undefined) return workerResults.deliveryObv;
        // Fallback: calculate on main thread
        if (!data || data.length === 0) return [];
        const arr: number[] = [];
        let cum = 0;
        for (const d of data) {
            const del = Number(d.delivery_final ?? d.delivery ?? 0);
            if (d.close > d.open) cum += del;
            else if (d.close < d.open) cum -= del;
            arr.push(cum);
        }
        return arr;
    }, [workerResults.deliveryObv, data]);

    // 14-period ATR using Wilder's smoothing (main thread fallback)
    const atr = useMemo(() => {
        if (workerResults.atr !== undefined) return workerResults.atr;
        // Fallback: calculate on main thread
        if (!data || data.length < 2) return data ? data.map(() => 0) : [];
        const tr: number[] = [data[0].high - data[0].low];
        for (let i = 1; i < data.length; i++) {
            const hl = data[i].high - data[i].low;
            const hc = Math.abs(data[i].high - data[i - 1].close);
            const lc = Math.abs(data[i].low - data[i - 1].close);
            tr.push(Math.max(hl, hc, lc));
        }
        const arr: number[] = [];
        let atrVal = tr.slice(0, 14).reduce((a, b) => a + b, 0) / 14;
        for (let i = 0; i < 14 && i < data.length; i++) arr.push(atrVal);
        for (let i = 14; i < data.length; i++) {
            atrVal = (atrVal * 13 + tr[i]) / 14;
            arr.push(atrVal);
        }
        return arr;
    }, [workerResults.atr, data]);

    // Swings (main thread fallback) — also needed for breaker blocks
    const swingsObj = useMemo(() => {
        if ((!toggles.showSwings && !toggles.showBreakerBlocks) || !data) return null;
        if (workerResults.swingsObj !== undefined) return workerResults.swingsObj;
        // Fallback: calculate on main thread via registry
        return chartRegistry.getIndicatorSync('swings')?.calculate(data, {}) || null;
    }, [toggles.showSwings, toggles.showBreakerBlocks, data, workerResults.swingsObj]);

    const atrPct = useMemo(() => {
        if (!baseData.closes || !atr || atr.length === 0) return [];
        return baseData.closes.map((c, i) => c > 0 ? (atr[i] / c) * 100 : 0);
    }, [baseData.closes, atr]);

    // Delivery MA
    const delMaData = useMemo(() => {
        if (!toggles.showDelMA || !data) return [];
        const delData = data.map(d => ({...d, close: d.delivery_final != null ? Number(d.delivery_final) : Number(d.delivery_qty) || 0}));
        return chartRegistry.getIndicatorSync('sma')?.calculate(delData, { period: 20 }) || [];
    }, [toggles.showDelMA, data]);

    // VWAP (Anchored)
    const vwapObj = useMemo(() => {
        if (!toggles.showVwap || !data) return [];
        let cumPV = 0, cumVol = 0;
        let lastDate = '';
        const result: (number | null)[] = [];
        for (const d of data) {
            const typPrice = (d.high + d.low + d.close) / 3;
            const vol = Number(d.volume_final ?? d.volume ?? 0);
            
            const dateStr = String(d.date).split('T')[0].split(' ')[0];
            if (lastDate !== dateStr) {
                cumPV = 0;
                cumVol = 0;
                lastDate = dateStr;
            }

            cumPV += typPrice * vol;
            cumVol += vol;
            if (d.vwap != null && !isNaN(d.vwap)) {
                result.push(d.vwap);
            } else {
                result.push(cumVol > 0 ? cumPV / cumVol : null);
            }
        }
        return result;
    }, [toggles.showVwap, data]);

    // SMAs - Split into per-indicator memos
    const sma20Result = useMemo(() => {
        if (!toggles.showSma20 || !data) return null;
        return chartRegistry.getIndicatorSync('sma')?.calculate(data, { period: 20 }) || null;
    }, [toggles.showSma20, data]);

    const sma50Result = useMemo(() => {
        if (!toggles.showSma50 || !data) return null;
        return chartRegistry.getIndicatorSync('sma')?.calculate(data, { period: 50 }) || null;
    }, [toggles.showSma50, data]);

    const sma150Result = useMemo(() => {
        if (!toggles.showSma150 || !data) return null;
        return chartRegistry.getIndicatorSync('sma')?.calculate(data, { period: 150 }) || null;
    }, [toggles.showSma150, data]);

    const sma200Result = useMemo(() => {
        if (!toggles.showSma200 || !data) return null;
        return chartRegistry.getIndicatorSync('sma')?.calculate(data, { period: 200 }) || null;
    }, [toggles.showSma200, data]);

    const smaResults = useMemo(() => {
        const results: Record<number, number[]> = {};
        if (sma20Result) results[20] = sma20Result;
        if (sma50Result) results[50] = sma50Result;
        if (sma150Result) results[150] = sma150Result;
        if (sma200Result) results[200] = sma200Result;
        return results;
    }, [sma20Result, sma50Result, sma150Result, sma200Result]);

    // RSI
    const rsiResult = useMemo(() => {
        if (!toggles.showRsi || !data) return [];
        return chartRegistry.getIndicatorSync('rsi')?.calculate(data, { period: 14 }) || [];
    }, [toggles.showRsi, data]);

    // FVG
    const activeFVGs = useMemo(() => {
        if (!toggles.showFvg || !data) return [];
        return chartRegistry.getIndicatorSync('fvg')?.calculate(data, { showMitigated: true }) || [];
    }, [toggles.showFvg, data]);

    // Liquidity Voids
    const liqVoidsResult = useMemo(() => {
        if (!toggles.showLiqVoids || !data) return null;
        return computeLiquidityVoids(data, bucket, liqVoidSettings);
    }, [toggles.showLiqVoids, data, bucket, liqVoidSettings]);

    // Smart Money Prints
    const smObj = useMemo(() => {
        if (!toggles.showSmartMoney || !data) return null;
        return computeSmartMoneyPrints(data, bucket, smpSettings);
    }, [toggles.showSmartMoney, data, bucket, smpSettings]);

    // Delivery Intensity Core
    const diObj = useMemo(() => {
        if (!toggles.showDelDivergence || !data) return null;
        return chartRegistry.getIndicatorSync('delIntensityCore')?.calculate(data, {});
    }, [toggles.showDelDivergence, data]);

    // Institutional Blocks
    const ibObj = useMemo(() => {
        if (!toggles.showInstBlocks || !data) return null;
        return chartRegistry.getIndicatorSync('instBlocks')?.calculate(data, {});
    }, [toggles.showInstBlocks, data]);

    // Delivery VWAP Bands
    const dbObj = useMemo(() => {
        if (!toggles.showDelVwapBands || !data) return null;
        const obj: { mid: (number | null)[], upper: (number | null)[], lower: (number | null)[] } = { mid: [], upper: [], lower: [] };
        let cumDPV = 0, cumDel = 0, n = 0, mean = 0, m2 = 0;
        for (const d of data) {
            const typPrice = (d.high + d.low + d.close) / 3;
            const del = Number(d.delivery_final ?? d.delivery ?? 0);
            
            n++;
            cumDPV += typPrice * del;
            cumDel += del;
            const dwap = cumDel > 0 ? cumDPV / cumDel : null;
            obj.mid.push(dwap);
            
            if (dwap !== null && del > 0) {
                const delta = typPrice - mean;
                mean += (delta * del) / cumDel;
                const delta2 = typPrice - mean;
                m2 += del * delta * delta2;
                
                const variance = m2 / cumDel;
                const stdDev = Math.sqrt(variance);
                obj.upper.push(dwap + stdDev * 1.5);
                obj.lower.push(dwap - stdDev * 1.5);
            } else {
                obj.upper.push(null);
                obj.lower.push(null);
            }
        }
        return obj;
    }, [toggles.showDelVwapBands, data]);

    // Delivery AD
    const daObj = useMemo(() => {
        if (!toggles.showDelAD || !data) return null;
        return chartRegistry.getIndicatorSync('delAd')?.calculate(data, {});
    }, [toggles.showDelAD, data]);

    // Order Blocks
    const obObj = useMemo(() => {
        if (!toggles.showOrderBlocks || !data) return null;
        return chartRegistry.getIndicatorSync('orderBlocks')?.calculate(data, { threshold: 1.5 }) || null;
    }, [toggles.showOrderBlocks, data]);

    // Equal Highs/Lows
    const ehlObj = useMemo(() => {
        if (!toggles.showEqualHighsLows || !data) return null;
        return chartRegistry.getIndicatorSync('equalHighsLows')?.calculate(data, { tolerancePercent: 0.5, minTouches: 2 }) || null;
    }, [toggles.showEqualHighsLows, data]);

    // Premium / Discount
    const pdObj = useMemo(() => {
        if (!toggles.showPremiumDiscount || !data) return null;
        return chartRegistry.getIndicatorSync('premiumDiscount')?.calculate(data, { lookback: 50 }) || null;
    }, [toggles.showPremiumDiscount, data]);

    // Breaker Blocks (needs swings)
    const bbObj = useMemo(() => {
        if (!toggles.showBreakerBlocks || !data || !swingsObj) return null;
        return chartRegistry.getIndicatorSync('breakerBlocks')?.calculate(data, { swings: swingsObj }) || null;
    }, [toggles.showBreakerBlocks, data, swingsObj]);

    // Smart Money Divergence (date-based → will adapt in computed)
    const smDivData = useMemo(() => {
        if (!toggles.showSmartMoneyDiv || !data) return null;
        return buildSmartMoneyDivergence(data, 10);
    }, [toggles.showSmartMoneyDiv, data]);

    // Delivery Clusters (date-based → will adapt in computed)
    const delClustersData = useMemo(() => {
        if (!toggles.showDeliveryClusters || !data) return null;
        return buildDeliveryClusters(data, 3, 60);
    }, [toggles.showDeliveryClusters, data]);

    // Delivery-Adjusted RSI (date-based → will adapt in computed)
    const delAdjRsiData = useMemo(() => {
        if (!toggles.showDelAdjRsi || !data) return null;
        return buildDeliveryAdjustedRSI(data, 14, 0.5);
    }, [toggles.showDelAdjRsi, data]);

    // IFI (date-based → will adapt in computed)
    const ifiData = useMemo(() => {
        if (!toggles.showIfi || !data) return null;
        return buildInstitutionalFlowIndex(data, 20);
    }, [toggles.showIfi, data]);

    // Delivery Trend
    const deliveryTrendData = useMemo(() => {
        if (!toggles.showDeliveryTrend || !data) return null;
        return chartRegistry.getIndicatorSync('deliveryTrend')?.calculate(data, {}) || null;
    }, [toggles.showDeliveryTrend, data]);

    // Delivery Volume Ratio (simplified — derives ratio from candle data)
    const deliveryVolumeRatioData = useMemo(() => {
        if (!toggles.showDeliveryVolumeRatio || !data || data.length < 21) return null;
        const ratios: number[] = [];
        const lookback = 20;
        for (let i = 0; i < data.length; i++) {
            if (i < lookback) { ratios.push(0); continue; }
            const deliveryPct = data[i].delivery_pct ?? 50;
            let avgDelivery = 0;
            let count = 0;
            for (let j = i - lookback; j < i; j++) {
                const dp = data[j].delivery_pct ?? 50;
                avgDelivery += data[j].volume * (dp / 100);
                count++;
            }
            avgDelivery = count > 0 ? avgDelivery / count : 0;
            const currentDelivery = data[i].volume * (deliveryPct / 100);
            ratios.push(avgDelivery > 0 ? currentDelivery / avgDelivery : 0);
        }
        return { ratios, signals: [] };
    }, [toggles.showDeliveryVolumeRatio, data]);

// Pane layout calculations (no indicator dependencies — declare first)
const paneLayout = useMemo(() => {
    const gap = 0.04;
    const activePanes = [toggles.showRsi, toggles.showDelAD, toggles.showDelivery, toggles.showVolume, toggles.showDeliveryObv].filter(Boolean).length;
    const paneHeight = activePanes > 0 ? Math.min(0.16, Math.max(0.05, (0.6 - (activePanes * gap)) / activePanes)) : 0;

    let currentY = 0;
    const rsiDomain = toggles.showRsi ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showRsi) currentY += paneHeight + gap;

    const delAdDomain = toggles.showDelAD ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showDelAD) currentY += paneHeight + gap;

    const delDomain = toggles.showDelivery ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showDelivery) currentY += paneHeight + gap;

    const volDomain = toggles.showVolume ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showVolume) currentY += paneHeight + gap;

    const obvDomain = toggles.showDeliveryObv ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showDeliveryObv) currentY += paneHeight + gap;

    const totalPaneSpace = currentY;
    const safeCurrentY = Math.min(0.65, totalPaneSpace);
    const priceDomain = [safeCurrentY, 1.0];

    return { currentY, rsiDomain, delAdDomain, delDomain, volDomain, obvDomain, priceDomain };
}, [toggles.showRsi, toggles.showDelAD, toggles.showDelivery, toggles.showVolume, toggles.showDeliveryObv]);

// Aggregate all indicator data for use in computed useMemo and ChartItemInner
const allIndicatorData = useMemo(() => ({
    delMaData, swingsObj, vwapObj, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj,
    deliveryObv, atr, atrPct, obObj, ehlObj, pdObj, bbObj, smDivData, delClustersData, delAdjRsiData, ifiData,
    deliveryTrendData, deliveryVolumeRatioData
}), [delMaData, swingsObj, vwapObj, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj, deliveryObv, atr, atrPct, obObj, ehlObj, pdObj, bbObj, smDivData, delClustersData, delAdjRsiData, ifiData,
    deliveryTrendData, deliveryVolumeRatioData]);

const computed = useMemo(() => {
const {
    opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
    currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain, obvDomain
} = {
    ...baseData,
    ...paneLayout,
    obvDomain: paneLayout.obvDomain,
};

// Access indicator results from outer scope (they are already memoized)
const { delMaData, swingsObj, vwapObj, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj } = allIndicatorData;

    const traceCtx: TraceBuilderContext = {
      data,
      viewport,
      candleIndexes,
      dateToIndex,
    };

    let profileResult: any = null;
    let vpMaxVolume = 1;

    if (toggles.showDeliveryProfile || toggles.showDeliverySR || toggles.showDelDelta) {
        let profileData = data;
        if (viewport && viewport.startIndex !== undefined && viewport.endIndex !== undefined) {
            if (toggles.profileResolution !== 'cumulative') {
                profileData = data.slice(Math.floor(viewport.startIndex), Math.ceil(viewport.endIndex) + 1);
            }
        }
        
        profileResult = chartRegistry.getIndicatorSync('volumeProfile')?.calculate(profileData, { 
            resolution: toggles.profileResolution,
            bucket: bucket 
        });
        
        if (profileResult) {
            vpMaxVolume = profileResult.maxVolume || 1;
        }
    }

    let swingsTraces: any[] = [];
    if (toggles.showSwings && swingsObj) {
        swingsTraces.push(...(chartRegistry.getTraceBuilderSync('swings')?.buildTraces(swingsObj, traceCtx) || []));
    }
    
    let vwapTraces: any[] = [];
    if (toggles.showVwap && vwapObj.length > 0) {
        vwapTraces.push(...(chartRegistry.getTraceBuilderSync('vwap')?.buildTraces(vwapObj, traceCtx) || []));
    }

    let smasTraces: any[] = [];
    const smaStyleMap: Record<number, { color: string; width: number }> = {
      20:  { color: '#eab308', width: 1.5 },
      50:  { color: '#0ea5e9', width: 1.5 },
      150: { color: '#d946ef', width: 1.5 },
      200: { color: '#f97316', width: 1.5 },
    };
    Object.entries(smaResults).forEach(([periodStr, result]) => {
      const period = Number(periodStr);
      const style = smaStyleMap[period];
      if (result && style) {
        smasTraces.push(...(chartRegistry.getTraceBuilderSync('sma')?.buildTraces(result, traceCtx, { period, color: style.color, width: style.width, yaxis: 'y' }) || []));
      }
    });

    let rsiTraces: any[] = [];
    const shapes: any[] = [];

    if (toggles.showNakedPoc && data.length > 0) {
        // compute avg volatility
        const atrSum = data.slice(1).reduce((sum: number, d: any, i: number) => {
            const close = data[i].close;
            if (close && close > 0) {
               return sum + ((d.high - d.low) / close);
            }
            return sum;
        }, 0);
        const avgVol = data.length > 1 ? atrSum / (data.length - 1) : 0.015;
        
        const pocs = computeNakedPocs(data, avgVol);
        const latestIndex = data.length - 1;
        shapes.push(...nakedPocsToShapes(pocs, latestIndex, dateToIndex));
    }

    if (toggles.showRsi && rsiResult.length > 0) {
      const tb = chartRegistry.getTraceBuilderSync('rsi');
      if (tb) {
         rsiTraces.push(...tb.buildTraces(rsiResult, traceCtx, { period: 14, color: '#8b5cf6', width: 1.5, yaxis: 'y3' }));
         if (tb.buildShapes) shapes.push(...tb.buildShapes(rsiResult, traceCtx));
      }
    }

    if (toggles.showFvg && activeFVGs.length > 0) {
        const tb = chartRegistry.getTraceBuilderSync('fvg');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(activeFVGs, traceCtx, { showMitigated: true }));
        }
    }

    if (toggles.showFibonacci) {
        const lb = chartRegistry.getLayoutBuilderSync('fibonacci');
        if (lb && lb.buildShapes) {
            shapes.push(...lb.buildShapes(traceCtx));
        }
    }

    // Order Blocks
    if (toggles.showOrderBlocks && obObj) {
        const tb = chartRegistry.getTraceBuilderSync('orderBlocks');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(obObj, traceCtx));
        }
    }

    // Equal Highs/Lows
    if (toggles.showEqualHighsLows && ehlObj) {
        const tb = chartRegistry.getTraceBuilderSync('equalHighsLows');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(ehlObj, traceCtx));
        }
    }

    // Breaker Blocks
    if (toggles.showBreakerBlocks && bbObj) {
        const tb = chartRegistry.getTraceBuilderSync('breakerBlocks');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(bbObj, traceCtx));
        }
    }

    // Premium / Discount
    if (toggles.showPremiumDiscount && pdObj) {
        const tb = chartRegistry.getTraceBuilderSync('premiumDiscount');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(pdObj, traceCtx));
        }
    }

    if (toggles.showLiqVoids && liqVoidsResult && liqVoidsResult.length > 0) {
        shapes.push(...liqVoidsToShapes(liqVoidsResult, dates, liqVoidSettings, dateToIndex));
    }

    let smartMoneyPrintsTraces: any[] = [];
    if (toggles.showSmartMoney && smObj && smObj.length > 0) {
        smartMoneyPrintsTraces.push(...smpToTraces(smObj, dateToIndex));
    }

    let delIntensityCoreTraces: any[] = [];
    if (toggles.showDelDivergence && diObj) {
        delIntensityCoreTraces.push(...(chartRegistry.getTraceBuilderSync('delIntensityCore')?.buildTraces(diObj, traceCtx) || []));
    }

    let volProfileTraces: any[] = [];
    
    if (profileResult && (toggles.showDeliveryProfile || toggles.showDeliverySR || toggles.showDelDelta)) {
        const tb = chartRegistry.getTraceBuilderSync('volumeProfile');
        if (tb) {
            if (toggles.showDeliveryProfile || toggles.showDelDelta) {
                volProfileTraces.push(...tb.buildTraces(profileResult, traceCtx, { 
                    resolution: toggles.profileResolution, 
                    showDeliveryProfile: toggles.showDeliveryProfile,
                    showDelDelta: toggles.showDelDelta
                }));
            }
            if (tb.buildShapes) {
                shapes.push(...tb.buildShapes(profileResult, traceCtx, {
                    resolution: toggles.profileResolution,
                    showDeliveryProfile: toggles.showDeliveryProfile,
                    showDeliverySR: toggles.showDeliverySR,
                    showDelDelta: toggles.showDelDelta
                }));
            }
        }
    }

    const { pocVolumePrice: globalPocVolY, pocDeliveryPrice: globalPocDelY } = profileResult || { pocVolumePrice: null, pocDeliveryPrice: null };

    let instBlocksTraces: any[] = [];
    if (toggles.showInstBlocks && ibObj) {
        instBlocksTraces.push(...(chartRegistry.getTraceBuilderSync('instBlocks')?.buildTraces(ibObj, traceCtx) || []));
    }
    
    let delVwapBandsTraces: any[] = [];
    if (toggles.showDelVwapBands && dbObj) {
        delVwapBandsTraces.push(...(chartRegistry.getTraceBuilderSync('delVwapBands')?.buildTraces(dbObj, traceCtx) || []));
    }

    let delAdTraces: any[] = [];
    if (toggles.showDelAD && daObj) {
        delAdTraces.push(...(chartRegistry.getTraceBuilderSync('delAd')?.buildTraces(daObj, traceCtx) || []));
    }

    let deliveryObvTraces: any[] = [];
    if (toggles.showDeliveryObv && deliveryObv.length > 0) {
        deliveryObvTraces.push({
            x: candleIndexes,
            y: deliveryObv,
            type: 'scatter',
            mode: 'lines',
            name: 'D-OBV',
            yaxis: 'y8',
            line: { color: '#8884d8', width: 1.5 },
            hoverinfo: 'none',
            showlegend: false,
        });
    }

    // Smart Money Divergence — adapt date-based traces to candleIndexes
    let smDivTraces: any[] = [];
    if (toggles.showSmartMoneyDiv && smDivData && smDivData.length > 0) {
        smDivData.forEach((trace: any) => {
            if (!trace.x || !trace.y) return;
            const xMapped = (trace.x as string[]).map((d: string) => dateToIndex.get(d) ?? 0);
            smDivTraces.push({ ...trace, x: xMapped, yaxis: 'y', hoverinfo: 'none', showlegend: false });
        });
    }

    // Delivery Clusters — adapt date-based traces to candleIndexes
    let delClustersTraces: any[] = [];
    if (toggles.showDeliveryClusters && delClustersData && delClustersData.length > 0) {
        delClustersData.forEach((trace: any) => {
            if (trace.type === 'scatter' && trace.x) {
                const xMapped = (trace.x as string[]).map((d: string) => dateToIndex.get(d) ?? 0);
                delClustersTraces.push({ ...trace, x: xMapped, yaxis: 'y', hoverinfo: 'none', showlegend: false });
            } else if (trace.type === 'rect' && trace.x0 !== undefined) {
                // Shape-based trace — convert date refs to indexes
                const x0 = dateToIndex.get(trace.x0) ?? 0;
                const x1 = dateToIndex.get(trace.x1) ?? data.length - 1;
                delClustersTraces.push({ ...trace, x0, x1, xref: 'x', yaxis: 'y', hoverinfo: 'none', showlegend: false });
            }
        });
    }

    // Delivery-Adjusted RSI — adapt date-based traces to candleIndexes
    let delAdjRsiTraces: any[] = [];
    if (toggles.showDelAdjRsi && delAdjRsiData && delAdjRsiData.length > 0) {
        delAdjRsiData.forEach((trace: any) => {
            if (!trace.x || !trace.y) return;
            const xMapped = (trace.x as string[]).map((d: string) => dateToIndex.get(d) ?? 0);
            delAdjRsiTraces.push({ ...trace, x: xMapped, yaxis: 'y3', hoverinfo: 'none', showlegend: false });
        });
    }

    // IFI — adapt date-based traces to candleIndexes
    let ifiTraces: any[] = [];
    if (toggles.showIfi && ifiData && ifiData.length > 0) {
        ifiData.forEach((trace: any) => {
            if (!trace.x || !trace.y) return;
            const xMapped = (trace.x as string[]).map((d: string) => dateToIndex.get(d) ?? 0);
            ifiTraces.push({ ...trace, x: xMapped, yaxis: 'y3', hoverinfo: 'none', showlegend: false });
        });
    }

    // Delivery Trend — via registry trace builder (uses candleIndexes natively)
    let deliveryTrendTraces: any[] = [];
    if (toggles.showDeliveryTrend && deliveryTrendData) {
        const tb = chartRegistry.getTraceBuilderSync('deliveryTrend');
        if (tb) {
            deliveryTrendTraces.push(...tb.buildTraces(deliveryTrendData, traceCtx));
        }
    }

    // Delivery Volume Ratio — via registry trace builder (uses candleIndexes natively)
    let deliveryVolumeRatioTraces: any[] = [];
    if (toggles.showDeliveryVolumeRatio && deliveryVolumeRatioData) {
        const tb = chartRegistry.getTraceBuilderSync('deliveryVolumeRatio');
        if (tb) {
            deliveryVolumeRatioTraces.push(...tb.buildTraces(deliveryVolumeRatioData, traceCtx));
        }
    }

    // Trend regime background shapes
    const trendShapes: any[] = [];
    if (trendAlignment.length > 0) {
        const vp = viewport;
        let sIdx = 0, eIdx = trendAlignment.length - 1;
        if (vp && isFinite(vp.startIndex) && isFinite(vp.endIndex)) {
            sIdx = Math.max(0, Math.floor(vp.startIndex));
            eIdx = Math.min(trendAlignment.length - 1, Math.ceil(vp.endIndex));
        }
        for (let i = sIdx; i <= eIdx; i++) {
            const ta = trendAlignment[i];
            if (ta > 1) {
                trendShapes.push({
                    type: 'rect',
                    x0: i - 0.5, x1: i + 0.5,
                    y0: 0, y1: 1,
                    xref: 'x', yref: 'paper',
                    fillcolor: 'rgba(0,255,0,0.03)',
                    line: { width: 0 },
                    layer: 'below',
                });
            } else if (ta < -1) {
                trendShapes.push({
                    type: 'rect',
                    x0: i - 0.5, x1: i + 0.5,
                    y0: 0, y1: 1,
                    xref: 'x', yref: 'paper',
                    fillcolor: 'rgba(255,0,0,0.03)',
                    line: { width: 0 },
                    layer: 'below',
                });
            }
        }
    }

    let niftyOutTraces: any[] = [];
    if (toggles.showNiftyOut) {
        const tb = chartRegistry.getTraceBuilderSync('niftyOut');
        if (tb) niftyOutTraces.push(...tb.buildTraces(niftyOut, traceCtx));
    }

    let deliveryOverlayTraces: any[] = [];
    if (toggles.showDeliveryOverlay) {
        const scores = divScores.map((s: any) => s != null ? Number(s) : null);
        deliveryOverlayTraces.push({
            x: candleIndexes,
            y: scores,
            type: 'scatter',
            mode: 'lines',
            name: 'Delivery Divergence',
            yaxis: 'y7',
            line: { color: 'rgba(255,255,255,0.4)', width: 1 },
            hoverinfo: 'none',
            showlegend: false,
        });
    }

    // Sub-panes builders
    let volumeTraces: any[] = [];
    if (toggles.showVolume) {
        const tb = chartRegistry.getTraceBuilderSync('volume');
        if (tb) volumeTraces.push(...tb.buildTraces(volumes, traceCtx));
    }

    let deliveryTraces: any[] = [];
    if (toggles.showDelivery) {
        const tb = chartRegistry.getTraceBuilderSync('delivery');
        if (tb) {
            deliveryTraces.push(...tb.buildTraces(deliveryFinal, traceCtx, {
                showMA: toggles.showDelMA,
                maData: delMaData,
                colors: deliveryColorsInverse
            }));
        }
    }
    // Build current value annotations for the right-hand Y-axis
    const annotations: any[] = [];
    const lastIdx = dates.length - 1;
    if (lastIdx >= 0) {
        const pushLabel = (val: number | undefined | null, color: string, bg: string, yaxis: string = 'y', prefix?: string) => {
            if (typeof val === 'number' && !isNaN(val)) {
                // Show exact price for indicators (up to 2 decimals) without 'k'/'M' abbreviation
                let text = val.toFixed(2);
                if (prefix) text = prefix + ': ' + text;

                annotations.push({
                    x: 1.005, // Slight offset to ensure labels do not sit directly on top of the last candle
                    xref: 'paper',
                    y: val,
                    yref: yaxis,
                    text: ' ' + text + ' ',
                    showarrow: false,
                    xanchor: 'left',
                    yanchor: 'middle',
                    bgcolor: bg,
                    font: { color: color, size: 9 },
                    bordercolor: bg,
                    borderwidth: 1,
                    borderpad: 2,
                });
            }
        };

        if (toggles.showSma20 && smaResults[20]) pushLabel(smaResults[20][lastIdx], '#ffffff', 'rgba(234, 179, 8, 0.8)'); // eab308
        if (toggles.showSma50 && smaResults[50]) pushLabel(smaResults[50][lastIdx], '#ffffff', 'rgba(14, 165, 233, 0.8)'); // 0ea5e9
        if (toggles.showSma150 && smaResults[150]) pushLabel(smaResults[150][lastIdx], '#ffffff', 'rgba(217, 70, 239, 0.8)'); // d946ef
        if (toggles.showSma200 && smaResults[200]) pushLabel(smaResults[200][lastIdx], '#ffffff', 'rgba(249, 115, 22, 0.8)'); // f97316
        
        if (toggles.showVwap && vwapObj.length > 0) {
            const vwa = [...vwapObj].reverse().find((v: any) => v != null);
            const vwapLabel = vwapObj[lastIdx] ?? vwa;
            pushLabel(vwapLabel, '#ffffff', 'rgba(136, 136, 136, 0.8)', 'y', 'AVWAP (Anc)');
        }
        
        if (toggles.showRsi && rsiResult.length > 0) pushLabel(rsiResult[lastIdx], '#ffffff', 'rgba(139, 92, 246, 0.8)', 'y3'); // 8b5cf6
        if (toggles.showNiftyOut) pushLabel(niftyOut[lastIdx], '#ffffff', 'rgba(168, 85, 247, 0.8)', 'y4'); // a855f7
        
        if (toggles.showDelAD && daObj && daObj.length > 0) {
            pushLabel(daObj[daObj.length - 1], '#ffffff', 'rgba(236, 72, 153, 0.8)', 'y6'); // ec4899
        }
        if (toggles.showDelVwapBands && dbObj && dbObj.mid.length > 0) {
            pushLabel(dbObj.upper[dbObj.upper.length - 1], '#ffffff', 'rgba(251, 146, 60, 0.5)'); // fb923c
            pushLabel(dbObj.lower[dbObj.lower.length - 1], '#ffffff', 'rgba(251, 146, 60, 0.5)');
            pushLabel(dbObj.mid[dbObj.mid.length - 1], '#000000', 'rgba(251, 191, 36, 0.8)', 'y', 'DWAP'); // fbbf24
        }
        if (toggles.showDelivery && toggles.showDelMA && delMaData.length > 0) {
            pushLabel(delMaData[lastIdx], '#ffffff', 'rgba(245, 158, 11, 0.8)', 'y5'); // f59e0b
        }
        if (toggles.showDeliveryProfile) {
            if (globalPocVolY !== null) pushLabel(globalPocVolY, '#ffffff', 'rgba(136, 136, 136, 0.8)');
            if (globalPocDelY !== null) pushLabel(globalPocDelY, '#ffffff', 'rgba(6, 182, 212, 0.8)');
        }
        
        // Also add price label for current close price
        pushLabel(closes[lastIdx], '#ffffff', closes[lastIdx] >= opens[lastIdx] ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)');
    }
    
    return {
        smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations, profileResult, vpMaxVolume, deliveryOverlayTraces, deliveryObvTraces, trendShapes, smDivTraces, delClustersTraces, delAdjRsiTraces, ifiTraces, deliveryTrendTraces, deliveryVolumeRatioTraces
    };
}, [baseData, delMaData, swingsObj, vwapObj, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj, obObj, ehlObj, pdObj, bbObj, smDivData, delClustersData, delAdjRsiData, ifiData, paneLayout, deliveryObv, atr, atrPct, viewport, data, toggles]);

const {
        opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse
} = baseData;

const { currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain, obvDomain } = paneLayout;

const {
    smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations, profileResult, vpMaxVolume, deliveryOverlayTraces, deliveryObvTraces, trendShapes, smDivTraces, delClustersTraces, delAdjRsiTraces, ifiTraces, deliveryTrendTraces, deliveryVolumeRatioTraces
} = computed;

const allShapes = useMemo(() => [...shapes, ...trendShapes], [shapes, trendShapes]);

const dataIndex = hoveredIndex !== undefined && hoveredIndex >= 0 && hoveredIndex < dates.length 
                  ? hoveredIndex 
                  : Math.max(0, dates.length - 1);



              const plotElement = useMemo(() => {
                  const chartLayout: any = {
                      autosize: true,
                      margin: { l: 40, r: 40, t: 10, b: 24 },
                      plot_bgcolor: '#1a1c24',
                      paper_bgcolor: '#1a1c24',
                      font: { color: '#888', family: settings?.fontFamily === 'Monospace' ? 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' : settings?.fontFamily === 'Sans-serif' ? 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif' : 'system-ui' },
                      showlegend: false,
                      hovermode: 'x',
                      hoverlabel: {
                          bgcolor: 'rgba(0,0,0,0)',
                          bordercolor: 'rgba(0,0,0,0)',
                          font: { color: 'rgba(0,0,0,0)' }
                      },
                      hoverdistance: 20,
                      dragmode: 'zoom',
                      xaxis: {
                          rangeslider: { visible: true, borderwidth: 1, bordercolor: 'rgba(255,255,255,0.1)' },
                          showgrid: !toggles.performanceMode && (settings?.showGridLines ?? false),
                          gridcolor: 'rgba(255,255,255,0.05)',
                          type: 'linear',
                          tickmode: 'array',
                          tickvals: xTickInfo.tickvals,
                          ticktext: xTickInfo.ticktext,
                          nticks: toggles.performanceMode ? 5 : 10,
                          showspikes: !toggles.performanceMode,
                          showspiketext: false,
                          spikemode: 'across',
                          spikesnap: 'cursor',
                          showline: true,
                          spikedash: 'solid',
                          spikecolor: '#555',
                          spikethickness: 1,
                          rangemode: 'normal',
                      },
                      xaxis2: {
                          overlaying: 'x',
                          side: 'top',
                          type: 'linear',
                          showgrid: false,
                          zeroline: false,
                          showticklabels: false,
                          range: [0, (vpMaxVolume || 1) * 3]
                      },
                      barmode: 'overlay',
                      yaxis: {
                          domain: priceDomain,
                          showgrid: !toggles.performanceMode && (settings?.showGridLines ?? false),
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          autorange: true,
                          type: toggles.showLogScale ? 'log' : 'linear',
                          showspikes: !toggles.performanceMode,
                          showspiketext: false,
                          spikemode: 'across',
                          spikesnap: 'cursor',
                          showline: true,
                          spikedash: 'solid',
                          spikecolor: '#555',
                          spikethickness: 1,
                      },
                      yaxis2: {
                          domain: volDomain,
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          showticklabels: true,
                          tickformat: '.2s',
                          visible: toggles.showVolume
                      },
                      yaxis3: {
                          domain: rsiDomain,
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          tickvals: [0, 30, 50, 70, 100],
                          visible: toggles.showRsi
                      },
                      yaxis4: {
                          domain: priceDomain,
                          side: 'right',
                          overlaying: 'y',
                          showgrid: false,
                          zeroline: false,
                          visible: toggles.showNiftyOut
                      },
                      yaxis5: {
                          domain: delDomain,
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          tickformat: '.2s',
                          visible: toggles.showDelivery
                      },
                      yaxis6: {
                          domain: delAdDomain,
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          tickformat: '.2s',
                          visible: toggles.showDelAD,
                          title: { text: 'Del. A/D', font: { size: 10, color: '#888' } }
                      },
                      shapes: allShapes,
                      annotations: annotations
                  };
                  if (toggles.showDeliveryOverlay) {
                      chartLayout.yaxis7 = {
                          overlaying: 'y',
                          side: 'right',
                          showgrid: false,
                          zeroline: false,
                          showticklabels: false,
                          title: '',
                      };
                  }
                  if (toggles.showDeliveryObv) {
                      chartLayout.yaxis8 = {
                          domain: obvDomain,
                          gridcolor: 'rgba(255,255,255,0.05)',
                          zeroline: false,
                          showticklabels: true,
                          tickfont: { size: 9 },
                          visible: true,
                          title: { text: 'D-OBV', font: { size: 10, color: '#888' } },
                      };
                  }
                  return (
                            <PlotlyCanvas
                                  plotRef={plotRef}
                                  dates={dates}
                                  data={[
                                      // Main Candlestick
                                     {
                                         type: 'candlestick',
                                         x: candleIndexes, open: opens, high: highs, low: lows, close: closes,
                                         name: sym, yaxis: 'y',
                                         increasing: {
                                             line: {color: '#22c55e', width: 1.5}, 
                                             fillcolor: (settings?.candlestickStyle === 'Filled' ? dates.map((_, i) => {
                                                 const rv = Math.min(2, Math.max(0.2, (relVol[i] ?? 1)));
                                                 const alpha = Math.min(1, rv / 2).toFixed(2);
                                                 return `rgba(34, 197, 94, ${alpha})`;
                                             }) : '#1a1c24') as any 
                                         }, 
                                         decreasing: {
                                             line: {color: '#ef4444', width: 1.5}, 
                                             fillcolor: dates.map((_, i) => {
                                                 const rv = Math.min(2, Math.max(0.2, (relVol[i] ?? 1)));
                                                 const alpha = Math.min(1, rv / 2).toFixed(2);
                                                 return `rgba(239, 68, 68, ${alpha})`;
                                             }) as any
                                         }, 
                                         customdata: dates.map((_, i) => [
                                             (deliveryPct[i] ?? 0).toFixed(1),
                                             (relVol[i] ?? 0).toFixed(2),
                                             (volComp[i] ?? 0).toFixed(2),
                                             (stockReturn[i] ?? 0).toFixed(2),
                                             (volumes[i] || 0) >= 1000000 
                                                 ? ((volumes[i] || 0) / 1000000).toFixed(2) + 'M'
                                                 : ((volumes[i] || 0) / 1000).toFixed(1) + 'k',
                                             (data[i]?.delivery_divergence_score ?? 0).toFixed(2)
                                         ]),
                                         hoverinfo: 'x',
                                         hovertemplate: '<extra></extra>'
                                     },
                                     
                                     // Delivery Intensity Cores Overlay
                                     ...(toggles.showDelDivergence ? delIntensityCoreTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                     
                                     // Overlays
                                     ...vwapTraces.map(t => ({...t, hoverinfo: 'none'})),
                                     ...smasTraces.map(t => ({...t, hoverinfo: 'none'})),
                                      ...niftyOutTraces.map(t => ({...t, hoverinfo: 'none'})),
                                      
                                      // Delivery Divergence Overlay
                                      ...(toggles.showDeliveryOverlay ? deliveryOverlayTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                     // Swing points
                                     ...swingsTraces.map(t => ({...t, hoverinfo: 'none'})),
                                     
                                     // Smart Money Footprint
                                     ...(toggles.showSmartMoney ? smartMoneyPrintsTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
 
                                     // Institutional Blocks Marker
                                     ...(toggles.showInstBlocks ? instBlocksTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
 
                                     // Delivery VWAP Bands
                                     ...(toggles.showDelVwapBands ? delVwapBandsTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
 
                                     // Volume / Delivery Profile Overlay
                                     ...(toggles.showDeliveryProfile || toggles.showDelDelta ? volProfileTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
 
                                     // Volume Histogram Pane
                                     ...(toggles.showVolume ? volumeTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
  
                                     // Delivery Percent Pane
                                     ...(toggles.showDelivery ? deliveryTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                     
                                     // RSI
                                     ...(toggles.showRsi ? rsiTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                     
                                      // Delivery A/D
                                      ...(toggles.showDelAD ? delAdTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                      // Delivery-Weighted OBV
                                      ...(toggles.showDeliveryObv ? deliveryObvTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                      // Smart Money Divergence
                                      ...(toggles.showSmartMoneyDiv ? smDivTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                      // Delivery Clusters
                                      ...(toggles.showDeliveryClusters ? delClustersTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                      // Delivery-Adjusted RSI
                                      ...(toggles.showDelAdjRsi ? delAdjRsiTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
                                      
                                      // IFI (Institutional Flow Index)
                                      ...(toggles.showIfi ? ifiTraces.map(t => ({...t, hoverinfo: 'none'})) : []),

                                      // Delivery Trend
                                      ...(toggles.showDeliveryTrend ? deliveryTrendTraces.map(t => ({...t, hoverinfo: 'none'})) : []),

                                      // Delivery Volume Ratio
                                      ...(toggles.showDeliveryVolumeRatio ? deliveryVolumeRatioTraces.map(t => ({...t, hoverinfo: 'none'})) : [])
                                  ]}
                                  layout={chartLayout}
                                 config={{
                                     responsive: true,
                                     displayModeBar: true,
                                     modeBarButtonsToAdd: ['drawline', 'drawopenpath', 'drawcircle', 'drawrect', 'eraseshape'] as any[],
                                     modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                                     displaylogo: false,
                                     scrollZoom: true
                                 }}
                                  style={{ width: '100%', height: '100%' }}
                             />
                          );
                      }, [computed, sym, settings?.candlestickStyle, settings?.showGridLines, settings?.fontFamily, candleIndexes, dates, xTickInfo]);

              return (
                 <div key={sym} className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex flex-col h-[500px] chart-container relative overflow-hidden">
                    <ChartHeader 
                        symbol={sym}
                        dataIndex={dataIndex}
                        dates={dates}
                        opens={opens}
                        highs={highs}
                        lows={lows}
                        closes={closes}
                        volumes={volumes}
                        deliveryPct={deliveryPct}
                        relVol={relVol}
                        volComp={volComp}
                        divScores={divScores}
                        trendAlignment={trendAlignment}
                        atr={atr}
                        atrPct={atrPct}
                    />
                    <div className="flex-1 w-full relative">
                        <Suspense fallback={<div className="w-full h-full flex items-center justify-center text-white/50 text-xs">Loading Plotly...</div>}>
                            {plotElement}
                        </Suspense>
                        {crosshairEnabled && (
                            <CrosshairOverlay ref={overlayHandleRef} />
                        )}
                    </div>
                 </div>
              );
          
};

export default function AdvancedChartView({ lib, activeSymbol }: { lib: Librarian, activeSymbol?: string }) {
  const { settings } = useSettings();
  const location = useLocation();
  const queryParams = new URLSearchParams(location.search);
  const urlSymbol = queryParams.get('symbol') || undefined;
  const initialSymbol = urlSymbol || activeSymbol || 'RELIANCE';
  const [sidebarOpen, setSidebarOpen] = usePersistedState('chart-sidebar-open', false);
  const [symbols, setSymbols] = useState<string[]>([initialSymbol]);
  const [searchInput, setSearchInput] = useState('');
  const [range, setRange] = useState(settings.defaultChartRange);

  useEffect(() => {
    if (activeSymbol && !urlSymbol) {
       setSymbols([activeSymbol]);
    }
  }, [activeSymbol, urlSymbol]);
  
  const [dataCache, setDataCache] = useState<Record<string, any[]>>({});
  
  // Overlays
  const [showSma20, setShowSma20] = usePersistedState('chart-showSma20', false);
  const [showSma50, setShowSma50] = usePersistedState('chart-showSma50', false);
  const [showSma150, setShowSma150] = usePersistedState('chart-showSma150', false);
  const [showSma200, setShowSma200] = usePersistedState('chart-showSma200', false);
  const [showFvg, setShowFvg] = usePersistedState('chart-showFvg', true);
  const [showFibonacci, setShowFibonacci] = usePersistedState('chart-showFibonacci', false);

  const [showVwap, setShowVwap] = usePersistedState('chart-showVwap', true);
  const [showVolume, setShowVolume] = usePersistedState('chart-showVolume', true);
  const [showDelivery, setShowDelivery] = usePersistedState('chart-showDelivery', false);
  const [showDelMA, setShowDelMA] = usePersistedState('chart-showDelMA', false);
  const [showDeliveryProfile, setShowDeliveryProfile] = usePersistedState('chart-showDeliveryProfile', false);
  const [profileResolution, setProfileResolution] = usePersistedState('chart-profileResolution', 'auto');
  const [showDeliverySR, setShowDeliverySR] = usePersistedState('chart-showDeliverySR', false);
  const [showSmartMoney, setShowSmartMoney] = usePersistedState('chart-showSmartMoney', true);
  const [showDelDivergence, setShowDelDivergence] = usePersistedState('chart-showDelDivergence', false);

  const [showDelAD, setShowDelAD] = usePersistedState('chart-showDelAD', false);
  const [showDelVwapBands, setShowDelVwapBands] = usePersistedState('chart-showDelVwapBands', false);
  const [showLiqVoids, setShowLiqVoids] = usePersistedState('chart-showLiqVoids', false);
  const [showInstBlocks, setShowInstBlocks] = usePersistedState('chart-showInstBlocks', false);
  
  const [showRsi, setShowRsi] = usePersistedState('chart-showRsi', true);
  const [showDelDelta, setShowDelDelta] = usePersistedState('chart-showDelDelta', false);
  const [showNakedPoc, setShowNakedPoc] = usePersistedState('chart-showNakedPoc', false);
  const [showDeliveryObv, setShowDeliveryObv] = usePersistedState('chart-showDeliveryObv', false);
  
  const [liqVoidSettings, setLiqVoidSettings] = usePersistedState<LiqVoidSettings>('chart-liqVoidSettings', DEFAULT_LIQ_VOID_SETTINGS);
  const [smpSettings, setSmpSettings] = usePersistedState<SmpSettings>('chart-smpSettings', DEFAULT_SMP_SETTINGS);
  const [indicatorSettingsOpen, setIndicatorSettingsOpen] = useState(false);

  // Custom indicators on Price
  const [showNiftyOut, setShowNiftyOut] = usePersistedState('chart-showNiftyOut', false);
  const [showLogScale, setShowLogScale] = usePersistedState('chart-showLogScale', false);
  const [performanceMode, setPerformanceMode] = usePersistedState('chart-performance-mode', false);
  const [showDeliveryOverlay, setShowDeliveryOverlay] = usePersistedState('chart-showDeliveryOverlay', false);

  const [showSwings, setShowSwings] = usePersistedState('chart-showSwings', true);
  const [showOrderBlocks, setShowOrderBlocks] = usePersistedState('chart-showOrderBlocks', false);
  const [showEqualHighsLows, setShowEqualHighsLows] = usePersistedState('chart-showEqualHighsLows', false);
  const [showBreakerBlocks, setShowBreakerBlocks] = usePersistedState('chart-showBreakerBlocks', false);
  const [showPremiumDiscount, setShowPremiumDiscount] = usePersistedState('chart-showPremiumDiscount', false);
  const [showSmartMoneyDiv, setShowSmartMoneyDiv] = usePersistedState('chart-showSmartMoneyDiv', false);
  const [showDeliveryClusters, setShowDeliveryClusters] = usePersistedState('chart-showDeliveryClusters', false);
  const [showDelAdjRsi, setShowDelAdjRsi] = usePersistedState('chart-showDelAdjRsi', false);
  const [showIfi, setShowIfi] = usePersistedState('chart-showIfi', false);
  const [showDeliveryTrend, setShowDeliveryTrend] = usePersistedState('chart-showDeliveryTrend', false);
  const [showDeliveryVolumeRatio, setShowDeliveryVolumeRatio] = usePersistedState('chart-showDeliveryVolumeRatio', false);
  const [crosshairEnabled, setCrosshairEnabled] = usePersistedState('chart-crosshair', true);

  const [limitDataRange, setLimitDataRange] = usePersistedState('chart-limitDataRange', true);
  
  const [scrollEnabled, setScrollEnabled] = usePersistedState('chart-scrollEnabled', false);
  const [candleTimeframe, setCandleTimeframe] = usePersistedState<'1D'|'1W'|'1M'>('chart-candleTimeframe', '1D');
   const [allSymbols, setAllSymbols] = useState<string[]>([]);
   const chunkLoadControllerRef = useRef<AbortController | null>(null);
   
   const [indexFilter, setIndexFilter] = useState<string>('All');
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [mcapFilter, setMcapFilter] = useState<string>('All');

  const [fetchingRange, setFetchingRange] = useState<{start: number, end: number} | null>(null);
  const [earliestLoadedDates, setEarliestLoadedDates] = useState<Record<string, string>>({});

  const [aggregatedDataCache, setAggregatedDataCache] = useState<Record<string, any[]>>({});
  interface FetchError { id: number; symbol: string; message: string }
  const [fetchErrors, setFetchErrors] = useState<FetchError[]>([]);
  const [isAggregating, setIsAggregating] = useState(false);
  const workerRef = useRef<Worker | null>(null);
  const workerListenersRef = useRef({ total: 0, map: new Map<string, any[]>(), batchId: 0 });

  useEffect(() => {
     const worker = new Worker(new URL('../workers/aggregateWorker.ts', import.meta.url), { type: 'module' });
     workerRef.current = worker;
     
     const handleMessage = (e: MessageEvent) => {
         if (e.data.type === 'AGGREGATED') {
             const { symbol, candles, batchId } = e.data;
             const state = workerListenersRef.current;
             if (batchId !== state.batchId) return;
             state.map.set(symbol, candles);
             
             if (state.map.size === state.total) {
                 const newAggregated: Record<string, any[]> = {};
                 state.map.forEach((val, key) => { newAggregated[key] = val; });
                 setAggregatedDataCache(newAggregated);
                 setIsAggregating(false);
             }
         }
     };

     worker.addEventListener('message', handleMessage);
     
     worker.onerror = (e) => {
         console.error('[AggregateWorker] crashed:', e);
         setIsAggregating(false);
     };

     return () => {
         worker.removeEventListener('message', handleMessage);
         worker.terminate();
     };
  }, []);

  useEffect(() => {
      if (candleTimeframe === '1D') {
          setAggregatedDataCache(dataCache);
          setIsAggregating(false);
          return;
      }
      
      const entries = Object.entries(dataCache);
      const total = entries.length;
      if (total === 0) {
          setAggregatedDataCache({});
          setIsAggregating(false);
          return;
      }

      const batchId = Date.now();
      setIsAggregating(true);
      workerListenersRef.current = { total, map: new Map(), batchId };
      
      if (workerRef.current) {
          for (const [sym, data] of entries) {
              workerRef.current.postMessage({ type: 'AGGREGATE', data, timeframe: candleTimeframe, symbol: sym, batchId });
          }
      }
  }, [dataCache, candleTimeframe]);
  
  const [availableIndices, setAvailableIndices] = useState<string[]>([]);
  const [availableSectors, setAvailableSectors] = useState<string[]>([]);
  
  const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string; indices: string[]; bucket: string }>>(new Map());
  
  // Inverted indexes for performance
  const [indexToSymbols, setIndexToSymbols] = useState<Map<string, Set<string>>>(new Map());
  const [sectorToSymbols, setSectorToSymbols] = useState<Map<string, Set<string>>>(new Map());
  const [bucketToSymbols, setBucketToSymbols] = useState<Map<string, Set<string>>>(new Map());

  // Fetch all symbols for fast scrolling and available metadata
  useEffect(() => {
     if (!lib.isConnectedToLocalRepo) return;
     
     const fetchAll = async () => {
         try {
             const [techRes, indexRes, sectorRes] = await Promise.all([
                 lib.executeQuery('_tech_conn', 'SELECT DISTINCT symbol FROM technical_data ORDER BY symbol', {}, 5000),
                 lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000'),
                 lib.executeQuery('_meta_conn', 'SELECT symbol, sector, in_nifty500 FROM symbols_master LIMIT 5000')
             ]);

             let techSymbols: string[] = [];
             if (techRes && techRes.length > 0) {
                 techSymbols = techRes.map((r: any) => r.symbol);
             }
             
             const indicesMap = new Map<string, Set<string>>();
             const allIndicesSet = new Set<string>();
             const invIndexMap = new Map<string, Set<string>>();
             
             if (indexRes) {
                 indexRes.forEach((row: any) => {
                     if (!indicesMap.has(row.symbol)) indicesMap.set(row.symbol, new Set());
                     indicesMap.get(row.symbol)!.add(row.index_name);
                     allIndicesSet.add(row.index_name);

                     if (!invIndexMap.has(row.index_name)) invIndexMap.set(row.index_name, new Set());
                     invIndexMap.get(row.index_name)!.add(row.symbol);
                 });
             }
             
             const sectorMap = new Map<string, { sector: string; bucket: string }>();
             const allSectorsSet = new Set<string>();
             const invSectorMap = new Map<string, Set<string>>();
             const invBucketMap = new Map<string, Set<string>>();
             
             if (sectorRes) {
                 sectorRes.forEach((row: any) => {
                     const sector = (row.sector && row.sector.trim() !== '') ? row.sector : 'Uncharted Sector';
                     
                     const bucket = resolveBucket(Array.from(indicesMap.get(row.symbol) || []), row.in_nifty500);

                     sectorMap.set(row.symbol, { sector, bucket });
                     allSectorsSet.add(sector);

                     if (!invSectorMap.has(sector)) invSectorMap.set(sector, new Set());
                     invSectorMap.get(sector)!.add(row.symbol);

                     if (!invBucketMap.has(bucket)) invBucketMap.set(bucket, new Set());
                     invBucketMap.get(bucket)!.add(row.symbol);
                 });
             }
             
             setAvailableIndices(Array.from(allIndicesSet).sort());
             setAvailableSectors(Array.from(allSectorsSet).sort());
             setIndexToSymbols(invIndexMap);
             setSectorToSymbols(invSectorMap);
             setBucketToSymbols(invBucketMap);
             
             const metaMap = new Map<string, {sector: string, indices: string[], bucket: string}>();
             for (const sym of techSymbols) {
                 const meta = sectorMap.get(sym) || { sector: 'Uncharted Sector', bucket: 'Deep Frontier' };
                 metaMap.set(sym, {
                     sector: meta.sector,
                     bucket: meta.bucket,
                     indices: Array.from(indicesMap.get(sym) || [])
                 });
             }
             setMetadataMap(metaMap);
             setAllSymbols(techSymbols);
             
         } catch (e) {
             console.error("Could not fetch metadata for filters", e);
         }
     };
     
     fetchAll();
  }, [lib]);

  const filteredSymbolsSet = useMemo(() => {
     if (indexFilter === 'All' && sectorFilter === 'All' && mcapFilter === 'All') return null;
     
     let currentSet = new Set(allSymbols);

     if (indexFilter !== 'All') {
         const idxSet = indexToSymbols.get(indexFilter) || new Set();
         currentSet = new Set([...currentSet].filter(x => idxSet.has(x)));
     }
     
     if (sectorFilter !== 'All') {
         const secSet = sectorToSymbols.get(sectorFilter) || new Set();
         currentSet = new Set([...currentSet].filter(x => secSet.has(x)));
     }
     
     if (mcapFilter !== 'All') {
         const bktSet = bucketToSymbols.get(mcapFilter) || new Set();
         currentSet = new Set([...currentSet].filter(x => bktSet.has(x)));
     }

     return currentSet;
  }, [allSymbols, indexFilter, sectorFilter, mcapFilter, indexToSymbols, sectorToSymbols, bucketToSymbols]);

  const effectiveSymbolsDesc = useMemo(() => {
     return filteredSymbolsSet ? Array.from(filteredSymbolsSet) : allSymbols;
  }, [filteredSymbolsSet, allSymbols]);

  // When filters change, reset to first symbol in the new list if fast-scroll is enabled
  useEffect(() => {
      if (scrollEnabled && effectiveSymbolsDesc.length > 0) {
          // If the currently selected symbol is no longer in the filtered list, reset it
          if (symbols.length === 1 && !effectiveSymbolsDesc.includes(symbols[0])) {
              setSymbols([effectiveSymbolsDesc[0]]);
          }
      }
  }, [effectiveSymbolsDesc, scrollEnabled]); // DO NOT add 'symbols' here or it could reset constantly


  const containerRef = useRef<HTMLDivElement>(null);
  const retryControllersRef = useRef<Map<string, AbortController>>(new Map());



  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    let start: Date;
    if (range === '1M') { start = new Date(); start.setMonth(start.getMonth() - 1); }
    else if (range === '3M') { start = new Date(); start.setMonth(start.getMonth() - 3); }
    else if (range === '6M') { start = new Date(); start.setMonth(start.getMonth() - 6); }
    else if (range === '1Y') { start = new Date(); start.setFullYear(start.getFullYear() - 1); }
    else start = new Date('2015-01-01');

    if (limitDataRange) {
        const twoYearsAgo = new Date();
        twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
        if (start < twoYearsAgo) {
            start.setTime(twoYearsAgo.getTime());
        }
    }

    return { startDate: start.toISOString().split('T')[0], endDate: end.toISOString().split('T')[0] };
  }, [range, limitDataRange]);

  const fetchSymbolData = useCallback(async (symbol: string, signal: AbortSignal, specificStart?: string, specificEnd?: string) => {
    try {
      setFetchErrors(prev => prev.filter(e => e.symbol !== symbol));
      const qStart = specificStart || startDate;
      const qEnd = specificEnd || endDate;
      
      if (settings.mockDataMode) {
          const mock = generateMockData(symbol, qStart, qEnd);
          if (specificStart && specificEnd) {
             setDataCache(prev => {
                const existing = prev[symbol] || [];
                return {...prev, [symbol]: [...mock, ...existing]};
             });
          } else {
             setDataCache(prev => ({...prev, [symbol]: mock}));
          }
          setEarliestLoadedDates(prev => ({...prev, [symbol]: qStart}));
          return;
      }

      const safeSymbol = symbol.replace(/[^A-Z0-9&\-]/g, '').substring(0, 20);
      const query = 'SELECT date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio, delivery_qty, stock_return, market_return, delivery_divergence_score, volatility_compression_score, relative_volume_score, nifty_outperformance_score, trend_alignment, delivery_ma_60, sma_50, high_52w, low_52w, bullish_fvg, bearish_fvg, fvg_top, fvg_bottom, fvg_boundary, fvg_freshness, swing_high, swing_low, liquidity_distance, htf_bullish, htf_bearish, mtf_bullish, mtf_bearish, delivery as delivery_final, volume as volume_final FROM technical_data WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date ASC';
      const result = await lib.executeQuery('_tech_conn', query, [safeSymbol, qStart, qEnd], 10000);
      
      if (signal.aborted) return;
      
      if (result && result.length > 0) {
          if (specificStart && specificEnd) {
             setDataCache(prev => {
                const existing = prev[symbol] || [];
                const firstOldDate = existing.length > 0 ? existing[0].date : '';
                const filteredNew = result.filter((d: any) => d.date < firstOldDate);
                return {...prev, [symbol]: [...filteredNew, ...existing]};
             });
          } else {
             setDataCache(prev => ({...prev, [symbol]: result}));
          }
          setEarliestLoadedDates(prev => ({...prev, [symbol]: result[0]?.date || qStart}));
      } else {
          // If no data returned and not a chunk fetch, fallback only if mockDataMode is on or specifically requested
      if (!specificStart) {
               if (settings.mockDataMode) {
                   setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol, qStart, qEnd)}));
                   setEarliestLoadedDates(prev => ({...prev, [symbol]: qStart}));
               } else {
                    setFetchErrors(prev => [...prev.filter(e => e.symbol !== symbol), { id: Date.now(), symbol, message: `No data found for ${symbol} in selected range.` }]);
               }
           }
       }
     } catch (e) {
       if (signal.aborted) return;
       console.error(e);
        setFetchErrors(prev => [...prev.filter(e => e.symbol !== symbol), { id: Date.now(), symbol, message: `Network error fetching ${symbol}. Check connection.` }]);
      if (!specificStart && settings.mockDataMode) {
         setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol, startDate, endDate)}));
         setEarliestLoadedDates(prev => ({...prev, [symbol]: startDate}));
      }
    }
  }, [startDate, endDate, lib, settings.mockDataMode]);

  const generateMockData = (sym: string, startStr: string, endStr: string) => {
    const mock = [];
    let currentPrice = Math.random() * 1000 + 100;
    const start = new Date(startStr);
    const end = new Date(endStr);
    
    let d = new Date(start);
    let i = 0;
    while (d <= end) {
        if (d.getDay() !== 0 && d.getDay() !== 6) {
            const volatility = currentPrice * 0.02;
            const open = currentPrice + (Math.random() - 0.5) * volatility;
            const close = open + (Math.random() - 0.5) * volatility;
            const high = Math.max(open, close) + Math.random() * volatility * 0.5;
            const low = Math.min(open, close) - Math.random() * volatility * 0.5;
            const volume = Math.floor(Math.random() * 5000000) + 100000;
            
            const idx = mock.length;
            const isBullFvg = idx >= 2 && Math.random() > 0.95;
            const isBearFvg = idx >= 2 && !isBullFvg && Math.random() > 0.95;

            mock.push({
                date: d.toISOString().split('T')[0],
                open, high, low, close, volume,
                vwap: close * (1 + (Math.random() - 0.5) * 0.01),
                delivery_final: volume * (Math.random() * 0.6 + 0.2), // Mock delivery_final
                delivery_pct: Math.random() * 100,
                delivery_ratio: Math.random() * 4,
                stock_return: (Math.random() - 0.5) * 2,
                volatility_compression_score: Math.random(),
                relative_volume_score: Math.random() * 2,
                delivery_divergence_score: (Math.random() - 0.5) * (close > open ? 1 : -1),
                nifty_outperformance_score: (Math.random() - 0.5),
                delivery_ma_60: volume * (Math.random() * 0.5 + 0.3),
                bullish_fvg: isBullFvg ? 1 : 0,
                bearish_fvg: isBearFvg ? 1 : 0,
                fvg_top: isBullFvg ? high : (isBearFvg ? mock[idx - 2].high : null),
                fvg_bottom: isBullFvg ? mock[idx - 2].low : (isBearFvg ? low : null),
                fvg_freshness: (isBullFvg || isBearFvg) ? Math.random() : null,
                fvg_boundary: null,
                swing_high: Math.random() > 0.9 ? 1 : null,
                swing_low: Math.random() > 0.9 ? 1 : null,
                trend_alignment: close > open ? Math.floor(Math.random() * 3) : -Math.floor(Math.random() * 3)
            });
            currentPrice = close;
            i++;
        }
        d.setDate(d.getDate() + 1);
    }
    return mock;
  };

  // Re-fetch when range changes or symbols added
  useEffect(() => {
    setDataCache({}); 
    setAggregatedDataCache({}); 
    setEarliestLoadedDates({});
    
    const controller = new AbortController();
    const uniqueSymbols = Array.from(new Set(symbols));
    uniqueSymbols.forEach(sym => {
       fetchSymbolData(sym, controller.signal);
    });
    return () => controller.abort();
  }, [symbols, range, startDate, endDate, fetchSymbolData]);

  // Viewport tracking for Chunk Loading
  const viewport = useChartStore(s => s.viewport);
  const isFetchingRef = useRef(false);
  const NSE_INCEPTION_DATE = new Date('2000-01-01'); // Chunk loading stops before NSE's electronic trading era.

  useEffect(() => {
     if (chunkLoadControllerRef.current) chunkLoadControllerRef.current.abort();
     const controller = new AbortController();
     chunkLoadControllerRef.current = controller;
     if (limitDataRange || fetchingRange !== null || symbols.length === 0 || !viewport || isFetchingRef.current) {
         return () => controller.abort();
     }

     const currentSym = symbols[0];
     const nearLeftEdge = symbols.some(s => {
       const len = aggregatedDataCache[s]?.length || 0;
       return len > 0 && viewport.startIndex < len * 0.1;
     });
     
     // Only trigger if we're near the left edge and not already fetching
     if (nearLeftEdge && !isFetchingRef.current) {
         const earliestStr = earliestLoadedDates[currentSym];
         if (!earliestStr) return () => controller.abort();
         
         const curEarliest = new Date(earliestStr);
         if (curEarliest <= NSE_INCEPTION_DATE) return () => controller.abort();

         isFetchingRef.current = true;
         const newStart = new Date(curEarliest);
         newStart.setFullYear(newStart.getFullYear() - 2); // load 2 more years
         const newStartStr = newStart.toISOString().split('T')[0];
         const curEarliestStr = curEarliest.toISOString().split('T')[0];

         setFetchingRange({ start: newStart.getFullYear(), end: curEarliest.getFullYear() });
         console.debug('[ChunkLoad] Fetching chunk', { symbol: currentSym, start: newStartStr, end: curEarliestStr });
         
         Promise.all(symbols.map(sym => 
             fetchSymbolData(sym, controller.signal, newStartStr, curEarliestStr)
         )).then(() => {
             setFetchingRange(null);
             isFetchingRef.current = false;
         }).catch(() => {
             setFetchingRange(null);
             isFetchingRef.current = false;
         });
     }
     return () => controller.abort();
  }, [viewport, limitDataRange, symbols, earliestLoadedDates, fetchingRange, aggregatedDataCache, fetchSymbolData]);



  useEffect(() => {
    if (!scrollEnabled || effectiveSymbolsDesc.length === 0 || symbols.length === 0) return;

    let timeoutId: any;
    const handleWheel = (e: WheelEvent) => {
        if (!containerRef.current?.contains(e.target as Node)) return;
        const isInsidePlotly = (e.target as HTMLElement).closest('.js-plotly-plot');
        if (isInsidePlotly) return;
        e.preventDefault();
        const delta = Math.sign(e.deltaY);
        if (delta === 0) return;
        
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            const currentSym = symbols[0];
            const currentIndex = effectiveSymbolsDesc.indexOf(currentSym);
            if (currentIndex !== -1) {
                const nextIndex = Math.max(0, Math.min(effectiveSymbolsDesc.length - 1, currentIndex + delta));
                setSymbols([effectiveSymbolsDesc[nextIndex]]);
            }
        }, 150);
    };

    window.addEventListener('wheel', handleWheel, { passive: false });
    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            const delta = e.key === 'ArrowDown' ? 1 : -1;
            const currentSym = symbols[0];
            const currentIndex = effectiveSymbolsDesc.indexOf(currentSym);
            if (currentIndex !== -1) {
                const nextIndex = Math.max(0, Math.min(effectiveSymbolsDesc.length - 1, currentIndex + delta));
                setSymbols([effectiveSymbolsDesc[nextIndex]]);
            }
        }
    };
    window.addEventListener('keydown', handleKeyDown);

    return () => {
        window.removeEventListener('wheel', handleWheel);
        window.removeEventListener('keydown', handleKeyDown);
        if (timeoutId) clearTimeout(timeoutId);
    };
  }, [scrollEnabled, effectiveSymbolsDesc, symbols]); // Depends on range so changing range refetches all

  const addSymbol = () => {
    const sym = searchInput.trim().toUpperCase();
    if (sym && !symbols.includes(sym) && symbols.length < 4) {
      setSymbols([...symbols, sym]);
      setSearchInput('');
    }
  };

  const removeSymbol = (sym: string) => {
    setSymbols(symbols.filter(s => s !== sym));
    setDataCache(prev => { const n = {...prev}; delete n[sym]; return n; });
    setAggregatedDataCache(prev => { const n = {...prev}; delete n[sym]; return n; });
    setEarliestLoadedDates(prev => { const n = {...prev}; delete n[sym]; return n; });
  };

  const overlayToggles = useMemo(() => ({
      showSma20, showSma50, showSma150, showSma200, showFvg, showFibonacci,
      showVwap, showSwings, showNiftyOut, showSmartMoney, showDelDivergence, 
      showDelVwapBands, showLiqVoids, showInstBlocks, showDelDelta, showNakedPoc,
      showDeliveryOverlay, showOrderBlocks, showEqualHighsLows, showBreakerBlocks,
      showPremiumDiscount, showSmartMoneyDiv, showDeliveryClusters, showDelAdjRsi, showIfi,
      showDeliveryTrend, showDeliveryVolumeRatio
  }), [
      showSma20, showSma50, showSma150, showSma200, showFvg, showFibonacci,
      showVwap, showSwings, showNiftyOut, showSmartMoney, showDelDivergence, 
      showDelVwapBands, showLiqVoids, showInstBlocks, showDelDelta, showNakedPoc,
      showDeliveryOverlay, showOrderBlocks, showEqualHighsLows, showBreakerBlocks,
      showPremiumDiscount, showSmartMoneyDiv, showDeliveryClusters, showDelAdjRsi, showIfi,
      showDeliveryTrend, showDeliveryVolumeRatio
  ]);

  const paneToggles = useMemo(() => ({
      showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showDelAD, showRsi, showDeliveryObv
  }), [
      showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showDelAD, showRsi, showDeliveryObv
  ]);

  const perfToggles = useMemo(() => ({
      showLogScale, performanceMode
  }), [
      showLogScale, performanceMode
  ]);

  const sidebarIndicatorToggles = useMemo(() => [
    { id: 'sma20', label: 'SMA 20', color: '#eab308', state: showSma20, set: setShowSma20 },
    { id: 'sma50', label: 'SMA 50', color: '#0ea5e9', state: showSma50, set: setShowSma50 },
    { id: 'sma150', label: 'SMA 150', color: '#d946ef', state: showSma150, set: setShowSma150 },
    { id: 'sma200', label: 'SMA 200', color: '#f97316', state: showSma200, set: setShowSma200 },
    { id: 'vwap', label: 'VWAP', state: showVwap, set: setShowVwap },
    { id: 'fvg', label: 'Fair Value Gaps', state: showFvg, set: setShowFvg },
    { id: 'fibonacci', label: 'Auto Fibonacci', state: showFibonacci, set: setShowFibonacci },
    { id: 'swings', label: 'Swing Points', state: showSwings, set: setShowSwings },
    { id: 'nifty', label: 'Nifty Outperf.', state: showNiftyOut, set: setShowNiftyOut },
    { id: 'logscale', label: 'Log Scale', state: showLogScale, set: setShowLogScale },
    { id: 'volume', label: 'Volume Pane', state: showVolume, set: setShowVolume },
    { id: 'delivery', label: 'Delivery Pane', state: showDelivery, set: setShowDelivery },
    { id: 'del_ma', label: 'Delivery MA (20)', state: showDelMA, set: setShowDelMA },
    { id: 'del_profile', label: 'Vol/Del Profile (FRVP)', state: showDeliveryProfile, set: setShowDeliveryProfile, desc: 'Fixed Range Volume Profile (Visible Area). Shows standard Volume (gray) overlaid with Delivery Volume (cyan) and their POCs.' },
    { id: 'del_sr', label: 'Delivery Auto S/R', state: showDeliverySR, set: setShowDeliverySR, desc: 'Auto-draws support/resistance at high delivery price levels.' },
    { id: 'smart_money', label: 'Smart Money Prints', state: showSmartMoney, set: setShowSmartMoney, desc: 'Highlights bars with 1.5x average volume and > 60% delivery ratio.', settingsAction: () => setIndicatorSettingsOpen(true) },
    { id: 'del_divergence', label: 'Delivery Intensity Core', state: showDelDivergence, set: setShowDelDivergence, desc: 'Draws a colored vertical core inside candles representing delivery %. Blue=Institutional, Gold=Divergence, Grey=Retail.' },
    { id: 'del_ad', label: 'Delivery A/D', state: showDelAD, set: setShowDelAD, desc: 'Accumulation/Distribution strictly using delivery volume.' },
    { id: 'del_vwap_bands', label: 'Delivery VWAP (DWAP)', state: showDelVwapBands, set: setShowDelVwapBands, desc: 'DWAP (Delivery Weighted Average Price) with 1.5 standard deviation bands.' },
    { id: 'del_delta', label: 'Delivery Delta Profile', state: showDelDelta, set: setShowDelDelta, desc: 'Adaptive bin sizes using delta volume (Buyer Init - Seller Init).' },
    { id: 'naked_poc', label: 'Naked POC Lines', state: showNakedPoc, set: setShowNakedPoc, desc: 'Tracks unrested volume & delivery Nodes until they are tested.' },
    { id: 'liq_voids', label: 'Liquidity Voids', state: showLiqVoids, set: setShowLiqVoids, desc: 'Shaded areas where large price movement occurred on low relative volume (potential gap fills).', settingsAction: () => setIndicatorSettingsOpen(true) },
    { id: 'inst_blocks', label: 'Inst. Blocks', state: showInstBlocks, set: setShowInstBlocks, desc: 'Massive volume anomalies (> 3.5x average) paired with > 65% delivery.' },
    { id: 'order_blocks', label: 'Order Blocks', state: showOrderBlocks, set: setShowOrderBlocks, desc: 'Institutional accumulation/distribution zones — last candle before strong moves.' },
    { id: 'equal_hl', label: 'Equal Highs/Lows', state: showEqualHighsLows, set: setShowEqualHighsLows, desc: 'Liquidity pools — multiple peaks/troughs at similar price levels.' },
    { id: 'breaker_blocks', label: 'Breaker Blocks', state: showBreakerBlocks, set: setShowBreakerBlocks, desc: 'Failed order blocks that reverse after liquidity sweeps.' },
    { id: 'premium_discount', label: 'Premium / Discount', state: showPremiumDiscount, set: setShowPremiumDiscount, desc: 'Fibonacci-based zones — Premium (sell), Discount (buy), Equilibrium.' },
    { id: 'smart_money_div', label: 'SM Divergence', state: showSmartMoneyDiv, set: setShowSmartMoneyDiv, desc: 'Divergence between price action and institutional delivery flow.' },
    { id: 'del_clusters', label: 'Delivery Clusters', state: showDeliveryClusters, set: setShowDeliveryClusters, desc: 'Consecutive high delivery days forming support/resistance zones.' },
    { id: 'del_adj_rsi', label: 'Del-Adj RSI', state: showDelAdjRsi, set: setShowDelAdjRsi, desc: 'RSI weighted by delivery percentage for institutional signal clarity.' },
    { id: 'ifi', label: 'IFI (Institutional Flow)', state: showIfi, set: setShowIfi, desc: 'Composite score (-100 to +100) — delivery, volume, and momentum.' },
    { id: 'del_trend', label: 'Delivery Trend', state: showDeliveryTrend, set: setShowDeliveryTrend, desc: 'EMA20 + signal line of delivery percentage — bullish/bearish crossover signals.' },
    { id: 'del_volume_ratio', label: 'Delivery Volume Ratio', state: showDeliveryVolumeRatio, set: setShowDeliveryVolumeRatio, desc: 'Current vs 20-day avg delivery volume — accumulation (>1.5x) / distribution (<0.8x).' },
    { id: 'rsi', label: 'RSI Pane', state: showRsi, set: setShowRsi },
    { id: 'delivery_obv', label: 'Delivery OBV', state: showDeliveryObv, set: setShowDeliveryObv, desc: 'Cumulative delivery-weighted OBV. Adds delivery on up days, subtracts on down days.' },
    { id: 'limit_data_range', label: 'Limit to 2 Years', state: limitDataRange, set: setLimitDataRange, desc: 'Limit data fetching to recent 2 years to improve performance.', group: 'Hardware' },
    { id: 'perf_mode', label: '🚀 Performance Mode', state: performanceMode, set: setPerformanceMode, desc: 'Optimizes rendering by disabling spikes and gridlines.', group: 'Hardware' }
  ], [
      showSma20, showSma50, showSma150, showSma200, showVwap, showFvg, showFibonacci, showSwings,
      showNiftyOut, showLogScale, showVolume, showDelivery, showDelMA, showDeliveryProfile, 
      showDeliverySR, showSmartMoney, showDelDivergence, showDelAD, showDelVwapBands, 
      showLiqVoids, showInstBlocks, showRsi, performanceMode, limitDataRange, showDelDelta, showNakedPoc,
      showDeliveryOverlay, showDeliveryObv, showOrderBlocks, showEqualHighsLows, showBreakerBlocks,
      showPremiumDiscount, showSmartMoneyDiv, showDeliveryClusters, showDelAdjRsi, showIfi
  ]);

  return (
    <div className="flex bg-[#0e1117] min-h-[600px] border border-[#ffffff1a] rounded overflow-hidden relative" ref={containerRef}>
      <ChartSidebar 
          sidebarOpen={sidebarOpen}
          setSidebarOpen={setSidebarOpen}
          toggles={sidebarIndicatorToggles}
          profileResolution={profileResolution}
          setProfileResolution={setProfileResolution}
          candleTimeframe={candleTimeframe}
          setCandleTimeframe={setCandleTimeframe}
      />

      {/* Main Charts Area */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6 relative">
          
          {/* Notifications Container */}
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2 pointer-events-none">
              {fetchingRange && (
                  <div className="bg-[#2563eb] text-white text-xs px-3 py-1.5 rounded shadow flex items-center gap-2 border border-[#3b82f6] opacity-90">
                      <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                      Fetching {fetchingRange.start}-{fetchingRange.end}...
                  </div>
              )}
              {isAggregating && (
                  <div className="bg-[#8b5cf6] text-white text-xs px-3 py-1.5 rounded-full shadow-lg flex items-center gap-2 border border-[#7c3aed]">
                      <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                      Aggregating timeframe...
                  </div>
              )}
              {limitDataRange && viewport && viewport.startIndex < (aggregatedDataCache[symbols[0]]?.length ?? 1) * 0.1 && (
                  <div className="bg-[#1a1c24]/80 backdrop-blur text-gray-400 text-xs px-3 py-1.5 rounded shadow border border-white/5 opacity-80">
                      Data limited to 2 years (toggle off to load more)
                  </div>
              )}
          </div>

          {fetchErrors.length > 0 && (
              <div className="flex flex-col gap-2 mb-4">
                  {fetchErrors.length > 1 && (
                      <button
                        onClick={() => {
                            const map = retryControllersRef.current;
                            fetchErrors.forEach(err => {
                                const existing = map.get(err.symbol);
                                if (existing) existing.abort();
                                const controller = new AbortController();
                                map.set(err.symbol, controller);
                                fetchSymbolData(err.symbol, controller.signal);
                            });
                            setFetchErrors([]);
                        }}
                        className="bg-red-500/20 hover:bg-red-500/30 text-red-300 text-xs px-3 py-1.5 rounded transition-colors self-end"
                      >Retry All ({fetchErrors.length})</button>
                  )}
                  {fetchErrors.map(err => (
                      <div key={err.id} className="bg-red-500/10 border border-red-500/30 text-red-400 text-xs px-4 py-2 rounded flex items-center justify-between gap-3">
                          <span className="flex-1">{err.message}</span>
                          <div className="flex items-center gap-2">
                              <button 
                                onClick={() => {
                                    const map = retryControllersRef.current;
                                    const existing = map.get(err.symbol);
                                    if (existing) existing.abort();
                                    const controller = new AbortController();
                                    map.set(err.symbol, controller);
                                    fetchSymbolData(err.symbol, controller.signal);
                                }}
                                className="bg-red-500/20 hover:bg-red-500/30 px-2 py-0.5 rounded transition-colors"
                              >Retry</button>
                              <button 
                                onClick={() => setFetchErrors(prev => prev.filter(e => e.id !== err.id))}
                                className="text-red-400/60 hover:text-red-300 transition-colors"
                                title="Dismiss"
                              ><X size={14} /></button>
                          </div>
                      </div>
                  ))}
              </div>
          )}

          {/* Controls Bar - Redesigned with ButtonGroup */}
          <div className="flex flex-col gap-3 mb-4">
              {/* Top Row: Symbol Management & Filters */}
              <div className="flex flex-wrap items-center gap-3">
                  {!sidebarOpen && (
                      <IconButton 
                         onClick={() => setSidebarOpen(true)}
                         variant="ghost"
                         size="sm"
                         title="Open Settings Panel"
                         aria-label="Open indicator settings sidebar"
                      >
                         <Settings2 size={16} />
                      </IconButton>
                  )}
                  
                  {/* Active Symbols */}
                  {symbols.length > 0 && (
                      <ButtonGroup>
                          {symbols.map(sym => (
                             <div key={sym} className="flex items-center gap-1 bg-cyan-500/10 text-cyan-400 text-[12px] px-2 py-1 rounded border border-cyan-500/20 font-mono font-bold">
                                {sym}
                                <button 
                                    onClick={() => removeSymbol(sym)} 
                                    className="hover:text-white transition-colors"
                                    aria-label={`Remove ${sym}`}
                                >
                                    <X size={10} />
                                </button>
                             </div>
                          ))}
                      </ButtonGroup>
                  )}
                  
                  {/* Filters */}
                  {symbols.length < 4 && (
                      <div className="flex flex-wrap items-center gap-2">
                          <select 
                              value={indexFilter} 
                              onChange={e => setIndexFilter(e.target.value)}
                              className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors hover:border-[#ffffff33] focus:border-cyan-500/50"
                              aria-label="Filter by index"
                          >
                              <option value="All">Index: All</option>
                              {availableIndices.map(idx => (
                                  <option key={idx} value={idx}>{idx}</option>
                              ))}
                          </select>
                          
                          <select 
                              value={sectorFilter} 
                              onChange={e => setSectorFilter(e.target.value)}
                              className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors hover:border-[#ffffff33] focus:border-cyan-500/50"
                              aria-label="Filter by sector"
                          >
                              <option value="All">Sector: All</option>
                              {availableSectors.map(sec => (
                                  <option key={sec} value={sec}>{sec}</option>
                              ))}
                          </select>
                          
                          <select 
                              value={mcapFilter} 
                              onChange={e => setMcapFilter(e.target.value)}
                              className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors hover:border-[#ffffff33] focus:border-cyan-500/50"
                              aria-label="Filter by market cap"
                          >
                              <option value="All">Market Cap: All</option>
                              <option value="Large Cap (N50)">Large Cap (N50)</option>
                              <option value="Large Cap (N100)">Large Cap (N100)</option>
                              <option value="Broader Market (N500)">Broader Market (N500)</option>
                              <option value="Nifty Small Cap 250">Nifty Small Cap 250</option>
                              <option value="Deep Frontier">Deep Frontier</option>
                          </select>
                          
                          <div className="w-48">
                            <SymbolSearch 
                                lib={lib}
                                onSymbolSelect={(sym) => {
                                    if (sym && !symbols.includes(sym)) {
                                        if (scrollEnabled) setSymbols([sym]);
                                        else setSymbols(prev => [...prev, sym]);
                                    }
                                }}
                                placeholder="Add symbol..."
                                clearOnSelect={!scrollEnabled}
                                filterSymbols={filteredSymbolsSet}
                            />
                          </div>
                      </div>
                  )}
              </div>

              {/* Bottom Row: Range, Tools & Actions */}
              <div className="flex flex-wrap items-center gap-3">
                  {/* Range Selector */}
                  <ButtonGroup orientation="horizontal">
                      {(['1M', '3M', '6M', '1Y', 'All'] as const).map(r => (
                         <Button
                           key={r} 
                           onClick={() => setRange(r)} 
                           variant={range === r ? 'primary' : 'ghost'}
                           size="sm"
                           className={`font-mono text-[11px] ${range === r ? '' : 'text-[#888] hover:text-white'}`}
                         >
                            {r}
                         </Button>
                      ))}
                  </ButtonGroup>
                  
                  <div className="h-6 w-px bg-[#ffffff1a]" />
                  
                  {/* Quick Toggles */}
                  <label className="flex items-center gap-2 cursor-pointer group">
                      <input 
                          type="checkbox" 
                          checked={scrollEnabled} 
                          onChange={e => setScrollEnabled(e.target.checked)} 
                          className="accent-cyan-500 w-3.5 h-3.5 m-0 rounded border-[#ffffff33] bg-transparent focus:ring-0" 
                      />
                      <span className="text-[12px] font-mono text-[#888] group-hover:text-white transition-colors">Fast Scroll</span>
                  </label>
                  
                  <Button
                      onClick={() => setCrosshairEnabled(prev => !prev)}
                      variant={crosshairEnabled ? 'primary' : 'outline'}
                      size="sm"
                      leftIcon={<Crosshair size={12} />}
                      className="font-mono text-[11px]"
                      aria-pressed={crosshairEnabled}
                      aria-label="Toggle crosshair tool"
                      title="Crosshair — ← → to move, Home / End to jump"
                  >
                      Crosshair
                  </Button>
                  
                  <Button
                      onClick={() => setShowDeliveryOverlay(prev => !prev)}
                      variant={showDeliveryOverlay ? 'primary' : 'outline'}
                      size="sm"
                      leftIcon={<Layers size={12} />}
                      className="font-mono text-[11px]"
                      aria-pressed={showDeliveryOverlay}
                      aria-label="Toggle delivery divergence overlay"
                  >
                      Delivery Overlay
                  </Button>
                  
                  <div className="flex-1" />
                  
                  {/* View Actions */}
                  <ButtonGroup orientation="horizontal">
                      <IconButton
                          variant="ghost"
                          size="sm"
                          title="Zoom In"
                          aria-label="Zoom in chart"
                      >
                          <ZoomIn size={14} />
                      </IconButton>
                      <IconButton
                          variant="ghost"
                          size="sm"
                          title="Zoom Out"
                          aria-label="Zoom out chart"
                      >
                          <ZoomOut size={14} />
                      </IconButton>
                      <IconButton
                          variant="ghost"
                          size="sm"
                          title="Reset View"
                          aria-label="Reset chart view"
                      >
                          <RefreshCw size={14} />
                      </IconButton>
                  </ButtonGroup>
              </div>
          </div>
          
          {symbols.map((sym, idx) => (
              <div key={sym} className="relative">
                  <ChartItem 
                    sym={sym} 
                    data={aggregatedDataCache[sym]} 
                    overlayToggles={overlayToggles} 
                    paneToggles={paneToggles} 
                    perfToggles={perfToggles} 
                    settings={settings} 
                    bucket={metadataMap.get(sym)?.bucket ?? null}
                    liqVoidSettings={liqVoidSettings}
                    smpSettings={smpSettings}
                    crosshairEnabled={crosshairEnabled}
                  />
                  {scrollEnabled && symbols.length === 1 && effectiveSymbolsDesc.length > 0 && (
                      <div className="absolute top-12 right-12 z-10 pointer-events-none">
                          <div className="bg-[#1a1c24]/80 backdrop-blur px-3 py-1 rounded-full border border-white/10 shadow-lg flex items-center gap-2">
                              <span className="text-cyan-400 font-mono text-[12px] font-bold">
                                  {effectiveSymbolsDesc.indexOf(sym) + 1} / {effectiveSymbolsDesc.length}
                              </span>
                          </div>
                      </div>
                  )}
              </div>
          ))}

          {symbols.length === 0 && (
             <div className="flex-1 flex items-center justify-center flex-col text-[#888]">
                <BarChart2 size={48} className="mb-4 opacity-50" />
                <p className="font-mono text-sm tracking-tight text-[#aaa]">Search for a ticker to begin analysis</p>
             </div>
          )}

          {indicatorSettingsOpen && createPortal(
              <IndicatorSettingsPanel
                 bucket={symbols.length > 0 ? metadataMap.get(symbols[0])?.bucket ?? null : null}
                 liqVoidSettings={liqVoidSettings}
                 smpSettings={smpSettings}
                 onLiqVoidChange={setLiqVoidSettings}
                 onSmpChange={setSmpSettings}
                 onClose={() => setIndicatorSettingsOpen(false)}
              />,
              document.body
          )}
      </div>
    </div>
  );
}
