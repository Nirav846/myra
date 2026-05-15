import { useState, useEffect, useMemo, useRef, memo, useCallback, lazy, Suspense } from 'react';
import { Librarian } from '../lib/Librarian';
const PlotlyCanvas = lazy(() => import('../components/chart/PlotlyCanvas').then(m => ({ default: m.PlotlyCanvas })));
import { ChartHeader } from '../components/chart/ChartHeader';
import { ChartSidebar } from '../components/chart/ChartSidebar';
import { Search, Plus, X, BarChart2, PanelLeftClose, Settings2, Info } from 'lucide-react';
import { SymbolSearch } from '../components/SymbolSearch';
import { useSettings } from '../lib/SettingsContext';

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


const ChartItem = memo(({ sym, data, overlayToggles, paneToggles, perfToggles, settings, bucket, liqVoidSettings, smpSettings }: any) => {
    if (!data) return (
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
    return <ChartItemInner sym={sym} data={data} overlayToggles={overlayToggles} paneToggles={paneToggles} perfToggles={perfToggles} settings={settings} bucket={bucket} liqVoidSettings={liqVoidSettings} smpSettings={smpSettings} />;
}, (prev, next) => {
    return prev.sym === next.sym && 
           prev.data === next.data && 
           prev.overlayToggles === next.overlayToggles &&
           prev.paneToggles === next.paneToggles &&
           prev.perfToggles === next.perfToggles &&
           prev.settings === next.settings &&
           prev.bucket === next.bucket &&
           prev.liqVoidSettings === next.liqVoidSettings &&
           prev.smpSettings === next.smpSettings;
});

const ChartItemInner = ({ sym, data, overlayToggles, paneToggles, perfToggles, settings, bucket, liqVoidSettings, smpSettings }: any) => {
    const viewport = useChartStore(state => state.viewport);
    const hoveredIndex = useChartStore(state => state.hoveredIndex);
    
    // Merge toggles for backward compatibility in internal logic
    const toggles = useMemo(() => ({
        ...overlayToggles,
        ...paneToggles,
        ...perfToggles
    }), [overlayToggles, paneToggles, perfToggles]);

    const indicators = useMemo(() => {
        const dates = data.map(d => d.date);
        const opens = data.map(d => d.open);
        const highs = data.map(d => d.high);
        const lows = data.map(d => d.low);
        const closes = data.map(d => d.close);
    const volumes = data.map(d => {
        const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume);
        return isNaN(vol) ? 0 : vol;
    });
    
    const vwap = data.map(d => d.vwap);
    const deliveryFinal = data.map(d => {
        const delVal = d.delivery_final ? Number(d.delivery_final) : 0;
        return isNaN(delVal) ? 0 : delVal;
    });
    const deliveryPct = data.map((d, i) => {
        if (d.delivery_pct != null && !isNaN(Number(d.delivery_pct))) return Number(d.delivery_pct);
        const delVal = deliveryFinal[i];
        const vol = Math.max(1, Number(d.volume) || 0);
        return (delVal / vol) * 100;
    });
    const deliveryRatio = data.map(d => d.delivery_ratio);
    const stockReturn = data.map(d => d.stock_return);
    const volComp = data.map(d => d.volatility_compression_score);
    const relVol = data.map(d => d.relative_volume_score);
    const divScores = data.map(d => d.delivery_divergence_score);
    const niftyOut = data.map(d => d.nifty_outperformance_score);
    const trendAlignment = data.map(d => d.trend_alignment);
    const volumeColors = data.map(d => d.close >= d.open ? '#22c55e' : '#ef4444');

    // Delivery bars are coloured inverse to price direction — a red delivery bar on a green candle highlights divergence.
    const deliveryColorsInverse = data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e');

    // Delivery MA
    const delMaData = toggles.showDelMA 
        ? chartRegistry.getIndicator('sma')?.calculate(data.map(d => ({...d, close: d.delivery_final != null ? Number(d.delivery_final) : Number(d.delivery_qty) || 0})), { period: 20 }) || []
        : [];

    const swingsObj = toggles.showSwings ? chartRegistry.getIndicator('swings')?.calculate(data, {}) : null;

    const vwapObj = [];
    if (toggles.showVwap) {
      // VWAP shown is Anchored VWAP (resets per trading day) — for intraday VWAP, use the DB column.
      let cumPV = 0, cumVol = 0;
      let lastDate = '';
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
          vwapObj.push(d.vwap);
        } else {
          vwapObj.push(cumVol > 0 ? cumPV / cumVol : null);
        }
      }
    }

    const smaConfigs = [
      { toggle: toggles.showSma20, period: 20, color: '#eab308', width: 1 },
      { toggle: toggles.showSma50, period: 50, color: '#0ea5e9', width: 1 },
      { toggle: toggles.showSma150, period: 150, color: '#d946ef', width: 1.5 },
      { toggle: toggles.showSma200, period: 200, color: '#f97316', width: 1.5 },
    ];
    
    const smaResults: Record<number, number[]> = {};

    smaConfigs.forEach(cfg => {
      if (cfg.toggle) {
        const p = chartRegistry.getIndicator('sma')?.calculate(data, { period: cfg.period });
        if (p) smaResults[cfg.period] = p;
      }
    });

    const rsiResult = toggles.showRsi ? chartRegistry.getIndicator('rsi')?.calculate(data, { period: 14 }) || [] : [];
    const activeFVGs = toggles.showFvg ? chartRegistry.getIndicator('fvg')?.calculate(data, { showMitigated: true }) || [] : [];
    
    // Updated LiqVoids and SmartMoney logic
    const liqVoidsResult = toggles.showLiqVoids 
        ? computeLiquidityVoids(data, bucket, liqVoidSettings) 
        : null;
    const smObj = toggles.showSmartMoney 
        ? computeSmartMoneyPrints(data, bucket, smpSettings) 
        : null;

    const diObj = toggles.showDelDivergence ? chartRegistry.getIndicator('delIntensityCore')?.calculate(data, {}) : null;
    const ibObj = toggles.showInstBlocks ? chartRegistry.getIndicator('instBlocks')?.calculate(data, {}) : null;
    
    let dbObj: any = null;
    if (toggles.showDelVwapBands) {
        dbObj = { mid: [], upper: [], lower: [] };
        let cumDPV = 0, cumDel = 0, n = 0, mean = 0, m2 = 0;
        for (const d of data) {
            const typPrice = (d.high + d.low + d.close) / 3;
            // Support either delivery_final or delivery column
            const del = Number(d.delivery_final ?? d.delivery ?? 0);
            
            n++;
            cumDPV += typPrice * del;
            cumDel += del;
            const dwap = cumDel > 0 ? cumDPV / cumDel : null;
            dbObj.mid.push(dwap);
            
            if (dwap !== null && del > 0) {
                const delta = typPrice - mean;
                mean += (delta * del) / cumDel;
                const delta2 = typPrice - mean;
                m2 += del * delta * delta2;
                
                const variance = m2 / cumDel;
                const stdDev = Math.sqrt(variance);
                dbObj.upper.push(dwap + stdDev * 1.5);
                dbObj.lower.push(dwap - stdDev * 1.5);
            } else {
                dbObj.upper.push(null);
                dbObj.lower.push(null);
            }
        }
    }

    const daObj = toggles.showDelAD ? chartRegistry.getIndicator('delAd')?.calculate(data, {}) : null;

    let currentY = 0;
    const gap = 0.04;
    const activePanes = [toggles.showRsi, toggles.showDelAD, toggles.showDelivery, toggles.showVolume].filter(Boolean).length;
    // Limit sub-panes to max 60% of total height to ensure price chart is visible
    const paneHeight = activePanes > 0 ? Math.min(0.16, Math.max(0.05, (0.6 - (activePanes * gap)) / activePanes)) : 0;
    
    const rsiDomain = toggles.showRsi ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showRsi) currentY += paneHeight + gap;
    
    const delAdDomain = toggles.showDelAD ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showDelAD) currentY += paneHeight + gap;
    
    const delDomain = toggles.showDelivery ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showDelivery) currentY += paneHeight + gap;
    
    const volDomain = toggles.showVolume ? [currentY, currentY + paneHeight] : [0, 0];
    if (toggles.showVolume) currentY += paneHeight + gap;
    
    const totalPaneSpace = currentY;
    const safeCurrentY = Math.min(0.65, totalPaneSpace);
    const priceDomain = [safeCurrentY, 1.0];

    return {
        dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
        delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj,
        currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
    };
}, [data, toggles]);

const computed = useMemo(() => {
    const {
        dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
        delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj,
        currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
    } = indicators;

    const traceCtx: TraceBuilderContext = {
      data,
      viewport
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
        
        profileResult = chartRegistry.getIndicator('volumeProfile')?.calculate(profileData, { 
            resolution: toggles.profileResolution,
            bucket: bucket 
        });
        
        if (profileResult) {
            vpMaxVolume = profileResult.maxVolume || 1;
        }
    }

    let swingsTraces: any[] = [];
    if (toggles.showSwings && swingsObj) {
        swingsTraces.push(...(chartRegistry.getTraceBuilder('swings')?.buildTraces(swingsObj, traceCtx) || []));
    }
    
    let vwapTraces: any[] = [];
    if (toggles.showVwap && vwapObj.length > 0) {
        vwapTraces.push(...(chartRegistry.getTraceBuilder('vwap')?.buildTraces(vwapObj, traceCtx) || []));
    }

    let smasTraces: any[] = [];
    smaConfigs.forEach(cfg => {
      if (cfg.toggle && smaResults[cfg.period]) {
        smasTraces.push(...(chartRegistry.getTraceBuilder('sma')?.buildTraces(smaResults[cfg.period], traceCtx, { period: cfg.period, color: cfg.color, width: cfg.width, yaxis: 'y' }) || []));
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
        const latestDate = data[data.length - 1].date;
        shapes.push(...nakedPocsToShapes(pocs, latestDate));
    }

    if (toggles.showRsi && rsiResult.length > 0) {
      const tb = chartRegistry.getTraceBuilder('rsi');
      if (tb) {
         rsiTraces.push(...tb.buildTraces(rsiResult, traceCtx, { period: 14, color: '#8b5cf6', width: 1.5, yaxis: 'y3' }));
         if (tb.buildShapes) shapes.push(...tb.buildShapes(rsiResult, traceCtx));
      }
    }

    if (toggles.showFvg && activeFVGs.length > 0) {
        const tb = chartRegistry.getTraceBuilder('fvg');
        if (tb && tb.buildShapes) {
            shapes.push(...tb.buildShapes(activeFVGs, traceCtx, { showMitigated: true }));
        }
    }

    if (toggles.showFibonacci) {
        const lb = chartRegistry.getLayoutBuilder('fibonacci');
        if (lb && lb.buildShapes) {
            shapes.push(...lb.buildShapes(traceCtx));
        }
    }

    if (toggles.showLiqVoids && liqVoidsResult && liqVoidsResult.length > 0) {
        shapes.push(...liqVoidsToShapes(liqVoidsResult, dates, liqVoidSettings));
    }

    let smartMoneyPrintsTraces: any[] = [];
    if (toggles.showSmartMoney && smObj && smObj.length > 0) {
        smartMoneyPrintsTraces.push(...smpToTraces(smObj, dates));
    }

    let delIntensityCoreTraces: any[] = [];
    if (toggles.showDelDivergence && diObj) {
        delIntensityCoreTraces.push(...(chartRegistry.getTraceBuilder('delIntensityCore')?.buildTraces(diObj, traceCtx) || []));
    }

    let volProfileTraces: any[] = [];
    
    if (profileResult && (toggles.showDeliveryProfile || toggles.showDeliverySR || toggles.showDelDelta)) {
        const tb = chartRegistry.getTraceBuilder('volumeProfile');
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
        instBlocksTraces.push(...(chartRegistry.getTraceBuilder('instBlocks')?.buildTraces(ibObj, traceCtx) || []));
    }
    
    let delVwapBandsTraces: any[] = [];
    if (toggles.showDelVwapBands && dbObj) {
        delVwapBandsTraces.push(...(chartRegistry.getTraceBuilder('delVwapBands')?.buildTraces(dbObj, traceCtx) || []));
    }

    let delAdTraces: any[] = [];
    if (toggles.showDelAD && daObj) {
        delAdTraces.push(...(chartRegistry.getTraceBuilder('delAd')?.buildTraces(daObj, traceCtx) || []));
    }

    let niftyOutTraces: any[] = [];
    if (toggles.showNiftyOut) {
        const tb = chartRegistry.getTraceBuilder('niftyOut');
        if (tb) niftyOutTraces.push(...tb.buildTraces(niftyOut, traceCtx));
    }

    // Sub-panes builders
    let volumeTraces: any[] = [];
    if (toggles.showVolume) {
        const tb = chartRegistry.getTraceBuilder('volume');
        if (tb) volumeTraces.push(...tb.buildTraces(volumes, traceCtx));
    }

    let deliveryTraces: any[] = [];
    if (toggles.showDelivery) {
        const tb = chartRegistry.getTraceBuilder('delivery');
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
        smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations, profileResult, vpMaxVolume
    };
}, [indicators, viewport, data, toggles]);

const {
    dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
    delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, liqVoidsResult, smObj, diObj, ibObj, dbObj, daObj,
    currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
} = indicators;

const {
    smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations, profileResult, vpMaxVolume
} = computed;

const dataIndex = hoveredIndex !== undefined && hoveredIndex >= 0 && hoveredIndex < dates.length 
                  ? hoveredIndex 
                  : Math.max(0, dates.length - 1);



              const plotElement = useMemo(() => (
                            <PlotlyCanvas
                                 dates={dates}
                                 data={[
                                     // Main Candlestick
                                     {
                                         type: 'candlestick',
                                         x: dates, open: opens, high: highs, low: lows, close: closes,
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
                                     ...(toggles.showDelAD ? delAdTraces.map(t => ({...t, hoverinfo: 'none'})) : [])
                                 ]}
                                 layout={{
                                     autosize: true,
                                     margin: { l: 40, r: 40, t: 10, b: 24 }, // minimal padding
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
                                        type: 'category',
                                        nticks: toggles.performanceMode ? 5 : 10,
                                        showspikes: !toggles.performanceMode,
                                        showspiketext: false,
                                        spikemode: 'across',
                                        spikesnap: 'cursor',
                                        showline: true,
                                        spikedash: 'solid',
                                        spikecolor: '#555',
                                        spikethickness: 1,
                                     },
                                     xaxis2: {
                                        overlaying: 'x',
                                        side: 'top',
                                        type: 'linear',
                                        showgrid: false,
                                        zeroline: false,
                                        showticklabels: false,
                                        range: [0, (vpMaxVolume || 1) * 3] // Bars take up max 1/3 of chart
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
                                     shapes: shapes,
                                     annotations: annotations
                                 }}
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
                        ), [computed, sym, settings?.candlestickStyle, settings?.showGridLines, settings?.fontFamily]);

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
                    />
                    <div className="flex-1 w-full relative">
                        <Suspense fallback={<div className="w-full h-full flex items-center justify-center text-white/50 text-xs">Loading Plotly...</div>}>
                            {plotElement}
                        </Suspense>
                    </div>
                 </div>
              );
          
};

export default function AdvancedChartView({ lib, activeSymbol }: { lib: Librarian, activeSymbol?: string }) {
  const { settings } = useSettings();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [symbols, setSymbols] = useState<string[]>([activeSymbol || 'RELIANCE']);
  const [searchInput, setSearchInput] = useState('');
  const [range, setRange] = useState(settings.defaultChartRange);

  useEffect(() => {
    if (activeSymbol) {
       setSymbols([activeSymbol]);
    }
  }, [activeSymbol]);
  
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
  
  const [liqVoidSettings, setLiqVoidSettings] = usePersistedState<LiqVoidSettings>('chart-liqVoidSettings', DEFAULT_LIQ_VOID_SETTINGS);
  const [smpSettings, setSmpSettings] = usePersistedState<SmpSettings>('chart-smpSettings', DEFAULT_SMP_SETTINGS);
  const [indicatorSettingsOpen, setIndicatorSettingsOpen] = useState(false);

  // Custom indicators on Price
  const [showNiftyOut, setShowNiftyOut] = usePersistedState('chart-showNiftyOut', false);
  const [showLogScale, setShowLogScale] = usePersistedState('chart-showLogScale', false);
  const [performanceMode, setPerformanceMode] = usePersistedState('chart-performance-mode', false);

  const [showSwings, setShowSwings] = usePersistedState('chart-showSwings', true);
  
  const [limitDataRange, setLimitDataRange] = usePersistedState('chart-limitDataRange', true);
  
  const [scrollEnabled, setScrollEnabled] = usePersistedState('chart-scrollEnabled', false);
  const [candleTimeframe, setCandleTimeframe] = usePersistedState<'1D'|'1W'|'1M'>('chart-candleTimeframe', '1D');
  const [allSymbols, setAllSymbols] = useState<string[]>([]);
  
  const [indexFilter, setIndexFilter] = useState<string>('All');
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [mcapFilter, setMcapFilter] = useState<string>('All');

  const [fetchingRange, setFetchingRange] = useState<{start: number, end: number} | null>(null);
  const [earliestLoadedDates, setEarliestLoadedDates] = useState<Record<string, string>>({});

  const [aggregatedDataCache, setAggregatedDataCache] = useState<Record<string, any[]>>({});
  const [fetchErrors, setFetchErrors] = useState<Record<string, string>>({});
  const [isAggregating, setIsAggregating] = useState(false);
  const workerRef = useRef<Worker | null>(null);
  const workerListenersRef = useRef({ total: 0, map: new Map<string, any[]>() });

  useEffect(() => {
     const worker = new Worker(new URL('../workers/aggregateWorker.ts', import.meta.url), { type: 'module' });
     workerRef.current = worker;
     
     const handleMessage = (e: MessageEvent) => {
         if (e.data.type === 'AGGREGATED') {
             const { symbol, candles } = e.data;
             const state = workerListenersRef.current;
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

      setIsAggregating(true);
      workerListenersRef.current = { total, map: new Map() };
      
      if (workerRef.current) {
          for (const [sym, data] of entries) {
              workerRef.current.postMessage({ type: 'AGGREGATE', data, timeframe: candleTimeframe, symbol: sym });
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
  const retryControllerRef = useRef<AbortController | null>(null);



  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    const start = new Date();
    if (range === '1M') start.setMonth(start.getMonth() - 1);
    else if (range === '3M') start.setMonth(start.getMonth() - 3);
    else if (range === '6M') start.setMonth(start.getMonth() - 6);
    else if (range === '1Y') start.setFullYear(start.getFullYear() - 1);
    else start.setFullYear(start.getFullYear() - 10);

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
      setFetchErrors(prev => {
          const n = {...prev};
          delete n[symbol];
          return n;
      });
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
      const query = 'SELECT *, delivery as delivery_final, volume as volume_final FROM technical_data WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date ASC';
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
                  setFetchErrors(prev => ({...prev, [symbol]: `No data found for ${symbol} in selected range.`}));
              }
          }
      }
    } catch (e) {
      if (signal.aborted) return;
      console.error(e);
      setFetchErrors(prev => ({...prev, [symbol]: `Network error fetching ${symbol}. Check connection.`}));
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
     const controller = new AbortController();
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
      showDelVwapBands, showLiqVoids, showInstBlocks, showDelDelta, showNakedPoc
  }), [
      showSma20, showSma50, showSma150, showSma200, showFvg, showFibonacci,
      showVwap, showSwings, showNiftyOut, showSmartMoney, showDelDivergence, 
      showDelVwapBands, showLiqVoids, showInstBlocks, showDelDelta, showNakedPoc
  ]);

  const paneToggles = useMemo(() => ({
      showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showDelAD, showRsi
  }), [
      showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showDelAD, showRsi
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
    { id: 'rsi', label: 'RSI Pane', state: showRsi, set: setShowRsi },
    { id: 'limit_data_range', label: 'Limit to 2 Years', state: limitDataRange, set: setLimitDataRange, desc: 'Limit data fetching to recent 2 years to improve performance.', group: 'Hardware' },
    { id: 'perf_mode', label: '🚀 Performance Mode', state: performanceMode, set: setPerformanceMode, desc: 'Optimizes rendering by disabling spikes and gridlines.', group: 'Hardware' }
  ], [
      showSma20, showSma50, showSma150, showSma200, showVwap, showFvg, showFibonacci, showSwings,
      showNiftyOut, showLogScale, showVolume, showDelivery, showDelMA, showDeliveryProfile, 
      showDeliverySR, showSmartMoney, showDelDivergence, showDelAD, showDelVwapBands, 
      showLiqVoids, showInstBlocks, showRsi, performanceMode, limitDataRange, showDelDelta, showNakedPoc
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

          {Object.keys(fetchErrors).length > 0 && (
              <div className="flex flex-col gap-2 mb-4">
                  {Object.entries(fetchErrors).map(([s, err]) => (
                      <div key={s} className="bg-red-500/10 border border-red-500/30 text-red-400 text-xs px-4 py-2 rounded flex items-center justify-between">
                          <span>{err}</span>
                          <button 
                            onClick={() => {
                                if (retryControllerRef.current) retryControllerRef.current.abort();
                                retryControllerRef.current = new AbortController();
                                fetchSymbolData(s, retryControllerRef.current.signal);
                            }}
                            className="bg-red-500/20 hover:bg-red-500/30 px-2 py-0.5 rounded transition-colors pointer-events-auto"
                          >Retry</button>
                      </div>
                  ))}
              </div>
          )}

          {/* Controls Bar */}
          <div className="flex items-start lg:items-center justify-start gap-4 mb-2 flex-col lg:flex-row flex-wrap">
              <div className="flex flex-wrap items-center gap-3">
                  {!sidebarOpen && (
                      <button 
                         onClick={() => setSidebarOpen(true)}
                         className="bg-[#1a1c24] border border-[#ffffff1a] p-1.5 rounded text-[#888] hover:text-white transition-all shadow-sm hover:bg-[#2a2c34]"
                         title="Open Settings Panel"
                      >
                         <Settings2 size={16} />
                      </button>
                  )}
                  <div className="flex flex-wrap gap-2">
                      {symbols.map(sym => (
                         <div key={sym} className="flex items-center gap-1 bg-cyan-500/10 text-cyan-400 text-[10px] px-2 py-1 rounded border border-cyan-500/20 font-mono font-bold">
                            {sym}
                            <button onClick={() => removeSymbol(sym)} className="hover:text-white"><X size={10} /></button>
                         </div>
                      ))}
                  </div>
                  {symbols.length < 4 && (
                      <div className="flex items-center gap-2">
                            <div className="flex items-center gap-2">
                               <select 
                                   value={indexFilter} 
                                   onChange={e => setIndexFilter(e.target.value)}
                                   className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors"
                               >
                                   <option value="All">Index Filter: All</option>
                                   {availableIndices.map(idx => (
                                       <option key={idx} value={idx}>{idx}</option>
                                   ))}
                               </select>
                               <select 
                                   value={sectorFilter} 
                                   onChange={e => setSectorFilter(e.target.value)}
                                   className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors"
                               >
                                   <option value="All">Sector Filter: All</option>
                                   {availableSectors.map(sec => (
                                       <option key={sec} value={sec}>{sec}</option>
                                   ))}
                               </select>
                               <select 
                                   value={mcapFilter} 
                                   onChange={e => setMcapFilter(e.target.value)}
                                   className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono outline-none uppercase transition-colors"
                               >
                                   <option value="All">Market Cap: All</option>
                                   <option value="Large Cap (N50)">Large Cap (N50)</option>
                                   <option value="Large Cap (N100)">Large Cap (N100)</option>
                                   <option value="Broader Market (N500)">Broader Market (N500)</option>
                                   <option value="Nifty Small Cap 250">Nifty Small Cap 250</option>
                                   <option value="Deep Frontier">Deep Frontier</option>
                               </select>
                            </div>
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

              <div className="flex items-center gap-4">
                  <div className="flex items-center gap-1 bg-[#1a1c24] p-0.5 rounded border border-[#ffffff1a]">
                      {(['1M', '3M', '6M', '1Y', 'All'] as const).map(r => (
                         <button 
                           key={r} 
                           onClick={() => setRange(r)} 
                           className={`px-2 py-1 text-[10px] rounded font-mono transition-colors ${range === r ? 'bg-cyan-500/20 text-cyan-400' : 'text-[#888] hover:bg-[#ffffff1a]'}`}
                         >{r}</button>
                      ))}
                  </div>
                  
                  <label className="flex items-center gap-2 cursor-pointer group">
                      <input 
                          type="checkbox" 
                          checked={scrollEnabled} 
                          onChange={e => setScrollEnabled(e.target.checked)} 
                          className="accent-cyan-500 w-3 h-3 m-0" 
                      />
                      <span className="text-[10px] font-mono text-[#666] group-hover:text-white transition-colors">Fast Scroll</span>
                  </label>
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
                  />
                  {scrollEnabled && symbols.length === 1 && effectiveSymbolsDesc.length > 0 && (
                      <div className="absolute top-12 right-12 z-10 pointer-events-none">
                          <div className="bg-[#1a1c24]/80 backdrop-blur px-3 py-1 rounded-full border border-white/10 shadow-lg flex items-center gap-2">
                              <span className="text-cyan-400 font-mono text-[10px] font-bold">
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

          {indicatorSettingsOpen && (
              <IndicatorSettingsPanel
                 bucket={symbols.length > 0 ? metadataMap.get(symbols[0])?.bucket ?? null : null}
                 liqVoidSettings={liqVoidSettings}
                 smpSettings={smpSettings}
                 onLiqVoidChange={setLiqVoidSettings}
                 onSmpChange={setSmpSettings}
                 onClose={() => setIndicatorSettingsOpen(false)}
              />
          )}
      </div>
    </div>
  );
}
