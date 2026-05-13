import { useState, useEffect, useMemo, useRef, memo } from 'react';
import { Librarian } from '../lib/Librarian';
import { PlotlyCanvas } from '../components/chart/PlotlyCanvas';
import { ChartHeader } from '../components/chart/ChartHeader';
import { ChartSidebar } from '../components/chart/ChartSidebar';
import { Search, Plus, X, BarChart2, PanelLeftClose, Settings2, Info } from 'lucide-react';
import { SymbolSearch } from '../components/SymbolSearch';
import { useSettings } from '../lib/SettingsContext';

// Removed static import of aggregateData, using worker instead
import { useChartStore } from '../store/chartStore';
import { chartRegistry } from '../core/chart/registry';
import { TraceBuilderContext } from '../core/chart/traces/types';

const usePersistedState = <T,>(key: string, initialValue: T): [T, (val: T) => void] => {
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

  const setValue = (value: T) => {
    setState(value);
    localStorage.setItem(key, JSON.stringify(value));
  };

  return [state, setValue];
};


const ChartItem = memo(({ sym, data, toggles, settings }: any) => {
    if (!data) return <div key={sym} className="h-[400px] flex items-center justify-center font-mono text-[#888] text-xs animate-pulse">Loading {sym}...</div>;
    return <ChartItemInner sym={sym} data={data} toggles={toggles} settings={settings} />;
});

const ChartItemInner = memo(({ sym, data, toggles, settings }: any) => {
    const viewport = useChartStore(state => state.viewport);
    const hoveredIndex = useChartStore(state => state.hoveredIndex);
    
    const computed = useMemo(() => {
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
        if (d.delivery_pct != null) return d.delivery_pct;
        const delVal = deliveryFinal[i];
        const vol = d.volume || 1;
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
    const deliveryColorsInverse = data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e');

    // Delivery MA
    const delMaData = toggles.showDelMA 
        ? chartRegistry.getIndicator('sma')?.calculate(data.map(d => ({...d, close: d.delivery_final != null ? Number(d.delivery_final) : Number(d.delivery_qty) || 0})), { period: 20 }) || []
        : [];

    const traceCtx: TraceBuilderContext = {
      data,
      viewport
    };

    let swingsTraces: any[] = [];
    if (toggles.showSwings) {
      const swingsObj = chartRegistry.getIndicator('swings')?.calculate(data, {});
      if (swingsObj) {
         swingsTraces.push(...(chartRegistry.getTraceBuilder('swings')?.buildTraces(swingsObj, traceCtx) || []));
      }
    }
    
    let vwapTraces: any[] = [];
    if (toggles.showVwap) {
      const vwapObj = [];
      let cumPV = 0, cumVol = 0;
      for (const d of data) {
        if (d.vwap != null && !isNaN(d.vwap)) {
          vwapObj.push(d.vwap);
        } else {
          const typPrice = (d.high + d.low + d.close) / 3;
          const vol = d.volume_final ?? d.volume ?? 0;
          cumPV += typPrice * vol;
          cumVol += vol;
          vwapObj.push(cumVol > 0 ? cumPV / cumVol : null);
        }
      }
      vwapTraces.push(...(chartRegistry.getTraceBuilder('vwap')?.buildTraces(vwapObj, traceCtx) || []));
    }

    let smasTraces: any[] = [];
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
        if (p) {
          smaResults[cfg.period] = p;
          smasTraces.push(...(chartRegistry.getTraceBuilder('sma')?.buildTraces(p, traceCtx, { period: cfg.period, color: cfg.color, width: cfg.width, yaxis: 'y' }) || []));
        }
      }
    });

    let rsiTraces: any[] = [];
    let rsiResult: number[] = [];
    const shapes: any[] = [];

    if (toggles.showRsi) {
      rsiResult = chartRegistry.getIndicator('rsi')?.calculate(data, { period: 14 }) || [];
      const tb = chartRegistry.getTraceBuilder('rsi');
      if (tb) {
         rsiTraces.push(...tb.buildTraces(rsiResult, traceCtx, { period: 14, color: '#8b5cf6', width: 1.5, yaxis: 'y3' }));
         if (tb.buildShapes) shapes.push(...tb.buildShapes(rsiResult, traceCtx));
      }
    }

    if (toggles.showFvg) {
        const activeFVGs = chartRegistry.getIndicator('fvg')?.calculate(data, { showMitigated: true }) || [];
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

    if (toggles.showLiqVoids) {
        const lb = chartRegistry.getLayoutBuilder('liqVoids');
        if (lb && lb.buildShapes) {
            shapes.push(...lb.buildShapes(traceCtx));
        }
    }

    let smartMoneyPrintsTraces: any[] = [];
    if (toggles.showSmartMoney) {
        const smObj = chartRegistry.getIndicator('smartMoneyPrints')?.calculate(data, {});
        if (smObj) smartMoneyPrintsTraces.push(...(chartRegistry.getTraceBuilder('smartMoneyPrints')?.buildTraces(smObj, traceCtx) || []));
    }

    let delIntensityCoreTraces: any[] = [];
    if (toggles.showDelDivergence) {
        const diObj = chartRegistry.getIndicator('delIntensityCore')?.calculate(data, {});
        if (diObj) delIntensityCoreTraces.push(...(chartRegistry.getTraceBuilder('delIntensityCore')?.buildTraces(diObj, traceCtx) || []));
    }

    // Volume / Delivery Profile & SR (FRVP)
    let vpMaxVolume = 1;
    let volProfileTraces: any[] = [];
    let profileResult: any = null;
    
    if (toggles.showDeliveryProfile || toggles.showDeliverySR) {
        profileResult = chartRegistry.getIndicator('volumeProfile')?.calculate(data, { resolution: toggles.profileResolution }, traceCtx);
        if (profileResult) {
            vpMaxVolume = profileResult.volProfileX.length > 0 ? Math.max(...profileResult.volProfileX) : 1;
            if (isNaN(vpMaxVolume) || !isFinite(vpMaxVolume) || vpMaxVolume <= 0) vpMaxVolume = 1;
            
            const tb = chartRegistry.getTraceBuilder('volumeProfile');
            if (tb) {
                if (toggles.showDeliveryProfile) {
                   volProfileTraces.push(...tb.buildTraces(profileResult, traceCtx, { resolution: toggles.profileResolution, showDeliveryProfile: true }));
                }
                if (tb.buildShapes) {
                   shapes.push(...tb.buildShapes(profileResult, traceCtx, {
                       resolution: toggles.profileResolution,
                       showDeliveryProfile: toggles.showDeliveryProfile,
                       showDeliverySR: toggles.showDeliverySR
                   }));
                }
            }
        }
    }

    const { pocVolY: globalPocVolY, pocDelY: globalPocDelY } = profileResult || { pocVolY: null, pocDelY: null };

    // 4 Intelligent Indicators
    let instBlocksTraces: any[] = [];
    if (toggles.showInstBlocks) {
        const ibObj = chartRegistry.getIndicator('instBlocks')?.calculate(data, {});
        if (ibObj) instBlocksTraces.push(...(chartRegistry.getTraceBuilder('instBlocks')?.buildTraces(ibObj, traceCtx) || []));
    }
    
    let delVwapBandsTraces: any[] = [];
    let dbObj: any;
    if (toggles.showDelVwapBands) {
        dbObj = { mid: [], upper: [], lower: [] };
        let cumDPV = 0, cumDel = 0, squaredDevSum = 0;
        for (const d of data) {
            const typPrice = (d.high + d.low + d.close) / 3;
            // Support either delivery_final or delivery column
            const del = Number(d.delivery_final ?? d.delivery ?? 0);
            
            cumDPV += typPrice * del;
            cumDel += del;
            const dwap = cumDel > 0 ? cumDPV / cumDel : null;
            dbObj.mid.push(dwap);
            
            if (dwap !== null) {
                squaredDevSum += del * Math.pow(typPrice - dwap, 2);
                const stdDev = cumDel > 0 ? Math.sqrt(squaredDevSum / cumDel) : 0;
                dbObj.upper.push(dwap + (stdDev * 1.5));
                dbObj.lower.push(dwap - (stdDev * 1.5));
            } else {
                dbObj.upper.push(null);
                dbObj.lower.push(null);
            }
        }
        delVwapBandsTraces.push(...(chartRegistry.getTraceBuilder('delVwapBands')?.buildTraces(dbObj, traceCtx) || []));
    }

    let delAdTraces: any[] = [];
    let daObj: any;
    if (toggles.showDelAD) {
        daObj = chartRegistry.getIndicator('delAd')?.calculate(data, {});
        if (daObj) delAdTraces.push(...(chartRegistry.getTraceBuilder('delAd')?.buildTraces(daObj, traceCtx) || []));
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
                maData: delMaData
            }));
        }
    }
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
    
    const priceDomain = [Math.max(0.1, Math.min(0.8, currentY)), 1];

    

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
        if (toggles.showVwap) pushLabel(vwap[lastIdx], '#ffffff', 'rgba(136, 136, 136, 0.8)', 'y', 'VWAP');
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
        dates,
        opens,
        highs,
        lows,
        closes,
        volumes,
        vwap,
        deliveryFinal,
        deliveryPct,
        deliveryRatio,
        stockReturn,
        volComp,
        relVol,
        divScores,
        niftyOut,
        trendAlignment,
        volumeColors,
        deliveryColorsInverse,
        smasTraces,
        rsiTraces,
        volProfileTraces,
        vwapTraces,
        swingsTraces,
        instBlocksTraces,
        delVwapBandsTraces,
        delAdTraces,
        niftyOutTraces,
        smartMoneyPrintsTraces,
        delIntensityCoreTraces,
        vpMaxVolume,
        delMaData,
        shapes,
        volumeTraces,
        deliveryTraces,
        annotations,
        currentY,
        rsiDomain,
        delAdDomain,
        delDomain,
        volDomain,
        priceDomain
    };
}, [data, toggles, viewport]);

const {
    dates,
    opens,
    highs,
    lows,
    closes,
    volumes,
    vwap,
    deliveryFinal,
    deliveryPct,
    deliveryRatio,
    stockReturn,
    volComp,
    relVol,
    divScores,
    niftyOut,
    trendAlignment,
    volumeColors,
    deliveryColorsInverse,
    smasTraces,
    rsiTraces,
    volProfileTraces,
    vwapTraces,
    swingsTraces,
    instBlocksTraces,
    delVwapBandsTraces,
    delAdTraces,
    niftyOutTraces,
    smartMoneyPrintsTraces,
    delIntensityCoreTraces,
    vpMaxVolume,
    delMaData,
    shapes,
    volumeTraces,
    deliveryTraces,
    annotations,
    currentY,
    rsiDomain,
    delAdDomain,
    delDomain,
    volDomain,
    priceDomain
} = computed;

const dataIndex = hoveredIndex !== undefined && hoveredIndex >= 0 && hoveredIndex < dates.length 
                  ? hoveredIndex 
                  : dates.length - 1;



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
                                     ...(toggles.showDeliveryProfile ? volProfileTraces.map(t => ({...t, hoverinfo: 'none'})) : []),
 
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
                        ), [computed, toggles, sym, data, dates, settings?.candlestickStyle, settings?.showGridLines, settings?.fontFamily]);

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
                        {plotElement}
                    </div>
                 </div>
              );
          
});

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
  const [isAggregating, setIsAggregating] = useState(false);
  const workerRef = useRef<Worker | null>(null);

  useEffect(() => {
     workerRef.current = new Worker(new URL('../workers/aggregateWorker.ts', import.meta.url), { type: 'module' });
     return () => workerRef.current?.terminate();
  }, []);

  useEffect(() => {
      if (candleTimeframe === '1D') {
          setAggregatedDataCache(dataCache);
          setIsAggregating(false);
          return;
      }
      
      const total = Object.keys(dataCache).length;
      if (total === 0) {
          setAggregatedDataCache({});
          setIsAggregating(false);
          return;
      }

      setIsAggregating(true);
      let completed = 0;
      const newAggregated: Record<string, any[]> = {};
      
      if (workerRef.current) {
          workerRef.current.onmessage = (e) => {
              if (e.data.type === 'AGGREGATED') {
                  const { symbol, candles } = e.data;
                  newAggregated[symbol] = candles;
                  completed++;
                  if (completed === total) {
                      setAggregatedDataCache(newAggregated);
                      setIsAggregating(false);
                  }
              }
          };

          for (const [sym, data] of Object.entries(dataCache)) {
              workerRef.current.postMessage({ type: 'AGGREGATE', data, timeframe: candleTimeframe, symbol: sym });
          }
      }
  }, [dataCache, candleTimeframe]);
  
  const [availableIndices, setAvailableIndices] = useState<string[]>([]);
  const [availableSectors, setAvailableSectors] = useState<string[]>([]);
  
  const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string; indices: string[]; bucket: string }>>(new Map());

  // Fetch all symbols for fast scrolling and available metadata
  useEffect(() => {
     if (!lib.isConnectedToLocalRepo) return;
     
     const fetchAll = async () => {
         try {
             const techRes = await lib.executeQuery('_tech_conn', 'SELECT DISTINCT symbol FROM technical_data ORDER BY symbol', {}, 5000);
             let techSymbols: string[] = [];
             if (techRes && techRes.length > 0) {
                 techSymbols = techRes.map((r: any) => r.symbol);
             }
             
             const indexRes = await lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000');
             const indicesMap = new Map<string, Set<string>>();
             const allIndices = new Set<string>();
             
             if (indexRes) {
                 indexRes.forEach((row: any) => {
                     if (!indicesMap.has(row.symbol)) indicesMap.set(row.symbol, new Set());
                     indicesMap.get(row.symbol)!.add(row.index_name);
                     allIndices.add(row.index_name);
                 });
             }
             
             const sectorRes = await lib.executeQuery('_meta_conn', 'SELECT symbol, sector, in_nifty500 FROM symbols_master LIMIT 5000');
             const sectorMap = new Map<string, { sector: string; bucket: string }>();
             const allSectors = new Set<string>();
             
             if (sectorRes) {
                 sectorRes.forEach((row: any) => {
                     const sector = (row.sector && row.sector.trim() !== '') ? row.sector : 'Uncharted Sector';
                     
                     let bucket = 'Deep Frontier';
                     const indices = Array.from(indicesMap.get(row.symbol) || []);
                     if (indices.some(i => i.includes('NIFTY 50') && !i.includes('NEXT'))) {
                         bucket = 'Large Cap (N50)';
                     } else if (indices.some(i => i.includes('NIFTY NEXT 50'))) {
                         bucket = 'Large Cap (N100)';
                     } else if (indices.some(i => i.includes('NIFTY SMALLCAP 250') || i.includes('SMALL CAP 250'))) {
                         bucket = 'Nifty Small Cap 250';
                     } else if (row.in_nifty500 === 1 || indices.some(i => i.includes('500'))) {
                         bucket = 'Broader Market (N500)';
                     }

                     sectorMap.set(row.symbol, { sector, bucket });
                     allSectors.add(sector);
                 });
             }
             
             setAvailableIndices(Array.from(allIndices).sort());
             setAvailableSectors(Array.from(allSectors).sort());
             
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
     if (indexFilter === 'All' && sectorFilter === 'All' && mcapFilter === 'All') return null; // No filter, return null to enable quick fallback
     
     const result = new Set<string>();
     for (const sym of allSymbols) {
         const meta = metadataMap.get(sym) || { sector: 'Uncharted Sector', indices: [], bucket: 'Deep Frontier' };
         let matchesIndex = indexFilter === 'All' || meta.indices.includes(indexFilter);
         let matchesSector = sectorFilter === 'All' || meta.sector === sectorFilter;
         let matchesMcap = mcapFilter === 'All' || meta.bucket === mcapFilter;
         
         if (matchesIndex && matchesSector && matchesMcap) {
             result.add(sym);
         }
     }
     return result;
  }, [allSymbols, metadataMap, indexFilter, sectorFilter, mcapFilter]);

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

  // Fast scrolling logic
  useEffect(() => {
    if (!scrollEnabled || effectiveSymbolsDesc.length === 0 || symbols.length === 0) return;

    let timeoutId: any;
    const handleWheel = (e: WheelEvent) => {
        if (!containerRef.current?.contains(e.target as Node)) return;
        
        e.preventDefault();
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            setSymbols((prevSymbols) => {
                const currentIndex = effectiveSymbolsDesc.indexOf(prevSymbols[0]);
                if (currentIndex === -1) return prevSymbols;
                
                if (e.deltaY > 0) {
                    const nextIdx = Math.min(currentIndex + 1, effectiveSymbolsDesc.length - 1);
                    return [effectiveSymbolsDesc[nextIdx]];
                } else {
                    const prevIdx = Math.max(currentIndex - 1, 0);
                    return [effectiveSymbolsDesc[prevIdx]];
                }
            });
        }, 50);
    };

    const handleKeyDown = (e: KeyboardEvent) => {
        if (!scrollEnabled) return;
        setSymbols((prevSymbols) => {
            const currentIndex = effectiveSymbolsDesc.indexOf(prevSymbols[0]);
            if (currentIndex === -1) return prevSymbols;
            
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const nextIdx = Math.min(currentIndex + 1, effectiveSymbolsDesc.length - 1);
                return [effectiveSymbolsDesc[nextIdx]];
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                const prevIdx = Math.max(currentIndex - 1, 0);
                return [effectiveSymbolsDesc[prevIdx]];
            }
            return prevSymbols;
        });
    };

    window.addEventListener('wheel', handleWheel, { passive: false });
    window.addEventListener('keydown', handleKeyDown);
    return () => {
        window.removeEventListener('wheel', handleWheel);
        window.removeEventListener('keydown', handleKeyDown);
        if (timeoutId) clearTimeout(timeoutId);
    };
  }, [scrollEnabled, effectiveSymbolsDesc, symbols]);

  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    const start = new Date();
    if (range === '1M') start.setMonth(start.getMonth() - 1);
    else if (range === '3M') start.setMonth(start.getMonth() - 3);
    else if (range === '6M') start.setMonth(start.getMonth() - 6);
    else if (range === '1Y') start.setFullYear(start.getFullYear() - 1);
    else start.setFullYear(start.getFullYear() - 10);

    const twoYearsAgo = new Date();
    twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
    if (start < twoYearsAgo) {
        start.setTime(twoYearsAgo.getTime());
    }

    return { startDate: start.toISOString().split('T')[0], endDate: end.toISOString().split('T')[0] };
  }, [range]);

  const fetchSymbolData = async (symbol: string, signal: AbortSignal, specificStart?: string, specificEnd?: string) => {
    try {
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
      const query = `SELECT *, delivery as delivery_final, volume as volume_final FROM technical_data WHERE symbol = '${symbol}' AND date >= '${qStart}' AND date <= '${qEnd}' ORDER BY date ASC`;
      const result = await lib.executeQuery('_tech_conn', query, {}, 10000);
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
          if (!specificStart) {
             setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol, qStart, qEnd)}));
             setEarliestLoadedDates(prev => ({...prev, [symbol]: qStart}));
          }
      }
    } catch (e) {
      if (signal.aborted) return;
      console.error(e);
      if (!specificStart) {
         setDataCache(prev => ({...prev, [symbol]: generateMockData(symbol, startDate, endDate)}));
         setEarliestLoadedDates(prev => ({...prev, [symbol]: startDate}));
      }
    }
  };

  const generateMockData = (sym: string, startStr: string, endStr: string) => {
    const mock = [];
    let currentPrice = Math.random() * 1000 + 100;
    const start = new Date(startStr);
    const end = new Date(endStr);
    
    let d = new Date(start);
    while (d <= end) {
        if (d.getDay() !== 0 && d.getDay() !== 6) {
            const volatility = currentPrice * 0.02;
            const open = currentPrice + (Math.random() - 0.5) * volatility;
            const close = open + (Math.random() - 0.5) * volatility;
            const high = Math.max(open, close) + Math.random() * volatility * 0.5;
            const low = Math.min(open, close) - Math.random() * volatility * 0.5;
            const volume = Math.floor(Math.random() * 5000000) + 100000;
            
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
                nifty_outperformance_score: (Math.random() - 0.5),
                delivery_ma_60: volume * (Math.random() * 0.5 + 0.3),
                bullish_fvg: Math.random() > 0.95 ? 1 : 0,
                bearish_fvg: Math.random() > 0.95 ? 1 : 0,
                fvg_top: high + volatility,
                fvg_bottom: low - volatility,
                fvg_freshness: Math.random(),
                fvg_boundary: Math.random() > 0.9 ? low - volatility : null,
                swing_high: Math.random() > 0.9 ? 1 : null,
                swing_low: Math.random() > 0.9 ? 1 : null,
                trend_alignment: Math.floor(Math.random() * 5) - 2
            });
            currentPrice = close;
        }
        d.setDate(d.getDate() + 1);
    }
    return mock;
  };

  // Re-fetch when range changes or symbols added
  useEffect(() => {
    const controller = new AbortController();
    symbols.forEach(sym => {
       fetchSymbolData(sym, controller.signal);
    });
    return () => controller.abort();
  }, [symbols, range]);

  // Viewport tracking for Chunk Loading
  const viewport = useChartStore(s => s.viewport);
  useEffect(() => {
     if (limitDataRange || fetchingRange !== null || symbols.length === 0 || !viewport) return;
     const currentSym = symbols[0];
     const maxCandles = aggregatedDataCache[currentSym]?.length || 0;
     
     // Only trigger if we're near the left edge and not already fetching
     if (viewport.startIndex < maxCandles * 0.1 && maxCandles > 0) {
         const earliestStr = earliestLoadedDates[currentSym];
         if (!earliestStr) return;
         
         const earliestDbDate = new Date('2000-01-01'); // arbitrary far past to stop fetching forever
         const curEarliest = new Date(earliestStr);
         if (curEarliest <= earliestDbDate) return;

         const newStart = new Date(curEarliest);
         newStart.setFullYear(newStart.getFullYear() - 2); // load 2 more years
         const newStartStr = newStart.toISOString().split('T')[0];
         const curEarliestStr = curEarliest.toISOString().split('T')[0];

         setFetchingRange({ start: newStart.getFullYear(), end: curEarliest.getFullYear() });
         
         const controller = new AbortController();
         
         Promise.all(symbols.map(sym => 
             fetchSymbolData(sym, controller.signal, newStartStr, curEarliestStr)
         )).then(() => {
             setFetchingRange(null);
         }).catch(() => {
             setFetchingRange(null);
         });
         
         return () => controller.abort();
     }
  }, [viewport, limitDataRange, symbols, earliestLoadedDates, fetchingRange, aggregatedDataCache]);

  useEffect(() => {
      if (scrollEnabled && effectiveSymbolsDesc.length > 0) {
          if (symbols.length === 1 && !effectiveSymbolsDesc.includes(symbols[0])) {
              setSymbols([effectiveSymbolsDesc[0]]);
          }
      }
  }, [effectiveSymbolsDesc, scrollEnabled, symbols]);

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
  };

  const memoizedToggles = useMemo(() => ({
      showSma20, showSma50, showSma150, showSma200, showFvg, showFibonacci,
      showVwap, showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showSmartMoney, showDelDivergence, showDelAD, showDelVwapBands,
      showLiqVoids, showInstBlocks, showRsi, showNiftyOut, showLogScale, showSwings, performanceMode
  }), [
      showSma20, showSma50, showSma150, showSma200, showFvg, showFibonacci,
      showVwap, showVolume, showDelivery, showDelMA, showDeliveryProfile, profileResolution,
      showDeliverySR, showSmartMoney, showDelDivergence, showDelAD, showDelVwapBands,
      showLiqVoids, showInstBlocks, showRsi, showNiftyOut, showLogScale, showSwings, performanceMode
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
    { id: 'smart_money', label: 'Smart Money Prints', state: showSmartMoney, set: setShowSmartMoney, desc: 'Highlights bars with 1.5x average volume and > 60% delivery ratio.' },
    { id: 'del_divergence', label: 'Delivery Intensity Core', state: showDelDivergence, set: setShowDelDivergence, desc: 'Draws a colored vertical core inside candles representing delivery %. Blue=Institutional, Gold=Divergence, Grey=Retail.' },
    { id: 'del_ad', label: 'Delivery A/D', state: showDelAD, set: setShowDelAD, desc: 'Accumulation/Distribution strictly using delivery volume.' },
    { id: 'del_vwap_bands', label: 'Delivery VWAP (DWAP)', state: showDelVwapBands, set: setShowDelVwapBands, desc: 'DWAP (Delivery Weighted Average Price) with 1.5 standard deviation bands.' },
    { id: 'liq_voids', label: 'Liquidity Voids', state: showLiqVoids, set: setShowLiqVoids, desc: 'Shaded areas where large price movement occurred on low relative volume (potential gap fills).' },
    { id: 'inst_blocks', label: 'Inst. Blocks', state: showInstBlocks, set: setShowInstBlocks, desc: 'Massive volume anomalies (> 3.5x average) paired with > 65% delivery.' },
    { id: 'rsi', label: 'RSI Pane', state: showRsi, set: setShowRsi },
    { id: 'limit_data_range', label: 'Limit to 2 Years', state: limitDataRange, set: setLimitDataRange, desc: 'Limit data fetching to recent 2 years to improve performance.', group: 'Hardware' },
    { id: 'perf_mode', label: '🚀 Performance Mode', state: performanceMode, set: setPerformanceMode, desc: 'Optimizes rendering by disabling spikes and gridlines.', group: 'Hardware' }
  ], [
      showSma20, showSma50, showSma150, showSma200, showVwap, showFvg, showFibonacci, showSwings,
      showNiftyOut, showLogScale, showVolume, showDelivery, showDelMA, showDeliveryProfile, 
      showDeliverySR, showSmartMoney, showDelDivergence, showDelAD, showDelVwapBands, 
      showLiqVoids, showInstBlocks, showRsi, performanceMode, limitDataRange
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
          
          {fetchingRange && (
              <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-[#2563eb] text-white text-xs px-3 py-1.5 rounded shadow z-50 flex items-center gap-2 border border-[#3b82f6] opacity-90 pointer-events-none">
                  <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                  Fetching {fetchingRange.start}-{fetchingRange.end}...
              </div>
          )}

          {isAggregating && (
              <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-[#8b5cf6] text-white text-xs px-3 py-1.5 rounded-full shadow-lg z-50 flex items-center gap-2 border border-[#7c3aed]">
                  <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                  Aggregating timeframe...
              </div>
          )}

          {limitDataRange && viewport && viewport.startIndex < (aggregatedDataCache[symbols[0]]?.length ?? 1) * 0.1 && (
              <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-[#1a1c24]/80 backdrop-blur text-gray-400 text-xs px-3 py-1.5 rounded shadow z-50 border border-white/5 opacity-80 pointer-events-none">
                  Data limited to 2 years (toggle off to load more)
              </div>
          )}

          {/* Controls Bar */}
          <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-3">
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
              <ChartItem key={sym} sym={sym} data={aggregatedDataCache[sym]} toggles={memoizedToggles} settings={settings} />
          ))}

          {symbols.length === 0 && (
             <div className="flex-1 flex items-center justify-center flex-col text-[#888]">
                <BarChart2 size={48} className="mb-4 opacity-50" />
                <p className="font-mono text-sm tracking-tight text-[#aaa]">Search for a ticker to begin analysis</p>
             </div>
          )}
      </div>
    </div>
  );
}
