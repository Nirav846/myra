import * as fs from 'fs';

const path = '/src/views/AdvancedChart.tsx';
let content = fs.readFileSync(path, 'utf8');

// #12 VWAP
content = content.replace(
    `if (toggles.showVwap) pushLabel(vwap[lastIdx], '#ffffff', 'rgba(136, 136, 136, 0.8)', 'y', 'VWAP');`,
    `if (toggles.showVwap && vwapObj.length > 0) {
            const vwapLabel = vwapObj[lastIdx] ?? vwapObj.findLast((v: any) => v != null);
            pushLabel(vwapLabel, '#ffffff', 'rgba(136, 136, 136, 0.8)', 'y', 'VWAP');
        }`
);

const allMath = 
`    const indicators = useMemo(() => {
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

    const swingsObj = toggles.showSwings ? chartRegistry.getIndicator('swings')?.calculate(data, {}) : null;

    const vwapObj = [];
    if (toggles.showVwap) {
      let cumPV = 0, cumVol = 0;
      for (const d of data) {
        const typPrice = (d.high + d.low + d.close) / 3;
        const vol = Number(d.volume_final ?? d.volume ?? 0);
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
    const smObj = toggles.showSmartMoney ? chartRegistry.getIndicator('smartMoneyPrints')?.calculate(data, {}) : null;
    const diObj = toggles.showDelDivergence ? chartRegistry.getIndicator('delIntensityCore')?.calculate(data, {}) : null;
    const profileResult = (toggles.showDeliveryProfile || toggles.showDeliverySR) ? chartRegistry.getIndicator('volumeProfile')?.calculate(data, { resolution: toggles.profileResolution }) : null;
    
    let vpMaxVolume = 1;
    if (profileResult) {
        vpMaxVolume = profileResult.volProfileX.length > 0 ? Math.max(...profileResult.volProfileX) : 1;
        if (isNaN(vpMaxVolume) || !isFinite(vpMaxVolume) || vpMaxVolume <= 0) vpMaxVolume = 1;
    }
    const ibObj = toggles.showInstBlocks ? chartRegistry.getIndicator('instBlocks')?.calculate(data, {}) : null;
    
    let dbObj: any = null;
    if (toggles.showDelVwapBands) {
        dbObj = { mid: [], upper: [], lower: [] };
        let cumDPV = 0, cumDel = 0, m2 = 0;
        for (const d of data) {
            const typPrice = (d.high + d.low + d.close) / 3;
            // Support either delivery_final or delivery column
            const del = Number(d.delivery_final ?? d.delivery ?? 0);
            
            cumDPV += typPrice * del;
            cumDel += del;
            const dwap = cumDel > 0 ? cumDPV / cumDel : null;
            dbObj.mid.push(dwap);
            
            if (dwap !== null && del > 0) {
                m2 += del * Math.pow(typPrice - dwap, 2);
                const stdDev = Math.sqrt(m2 / cumDel);
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
    
    const priceDomain = [Math.max(0.1, Math.min(0.8, currentY)), 1];

    return {
        dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
        delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, smObj, diObj, profileResult, vpMaxVolume, ibObj, dbObj, daObj,
        currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
    };
}, [data, toggles]);

const computed = useMemo(() => {
    const {
        dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
        delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, smObj, diObj, profileResult, vpMaxVolume, ibObj, dbObj, daObj,
        currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
    } = indicators;

    const traceCtx: any = {
      data,
      viewport
    };

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

    if (toggles.showLiqVoids) {
        const lb = chartRegistry.getLayoutBuilder('liqVoids');
        if (lb && lb.buildShapes) {
            shapes.push(...lb.buildShapes(traceCtx));
        }
    }

    let smartMoneyPrintsTraces: any[] = [];
    if (toggles.showSmartMoney && smObj) {
        smartMoneyPrintsTraces.push(...(chartRegistry.getTraceBuilder('smartMoneyPrints')?.buildTraces(smObj, traceCtx) || []));
    }

    let delIntensityCoreTraces: any[] = [];
    if (toggles.showDelDivergence && diObj) {
        delIntensityCoreTraces.push(...(chartRegistry.getTraceBuilder('delIntensityCore')?.buildTraces(diObj, traceCtx) || []));
    }

    let volProfileTraces: any[] = [];
    
    if (profileResult && (toggles.showDeliveryProfile || toggles.showDeliverySR)) {
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

    const { pocVolY: globalPocVolY, pocDelY: globalPocDelY } = profileResult || { pocVolY: null, pocDelY: null };

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
                maData: delMaData
            }));
        }
    }
    
    // Build current value annotations for the right-hand Y-axis
    const annotations: any[] = [];
    const lastIdx = dates.length - 1;
    if (lastIdx >= 0) {
        const pushLabel = (val: number | undefined | null, color: string, bg: string, yaxis: string = 'y', prefix?: string) => {
            if (typeof val === 'number' && !isNaN(val)) {
                let text = val.toFixed(2);
                if (prefix) text = prefix + ': ' + text;

                annotations.push({
                    x: 1.005,
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
            const vwapLabel = vwapObj[lastIdx] ?? vwapObj.findLast((v: any) => v != null);
            pushLabel(vwapLabel, '#ffffff', 'rgba(136, 136, 136, 0.8)', 'y', 'VWAP');
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
        smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations
    };
}, [indicators, viewport, data]);`

// Note: I also need to update the destructuring below the new `computed` memo!
// Before:
// const { dates, opens, ... } = computed;
// After:
// const { dates, opens, highs, lows, closes, volumes, vpMaxVolume, currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain } = indicators;
// const { smasTraces, rsiTraces, ... } = computed;

const newDestructure = 
`const {
    dates, opens, highs, lows, closes, volumes, vwap, deliveryFinal, deliveryPct, deliveryRatio, stockReturn, volComp, relVol, divScores, niftyOut, trendAlignment, volumeColors, deliveryColorsInverse,
    delMaData, swingsObj, vwapObj, smaConfigs, smaResults, rsiResult, activeFVGs, smObj, diObj, profileResult, vpMaxVolume, ibObj, dbObj, daObj,
    currentY, rsiDomain, delAdDomain, delDomain, volDomain, priceDomain
} = indicators;

const {
    smasTraces, rsiTraces, volProfileTraces, vwapTraces, swingsTraces, instBlocksTraces, delVwapBandsTraces, delAdTraces, niftyOutTraces, smartMoneyPrintsTraces, delIntensityCoreTraces, shapes, volumeTraces, deliveryTraces, annotations
} = computed;`;

const regexDestructure = /const \{[\s\S]*?\} = computed;/;
const destructureMatch = content.match(regexDestructure);

const regexComputed = /const computed = useMemo\(\(\) => \{[\s\S]*?\}, \[data, toggles, viewport\]\);/;
const computedMatch = content.match(regexComputed);

if (computedMatch && destructureMatch) {
    let result = content;
    result = result.replace(computedMatch[0], allMath);
    // Replace the destructuring block
    result = result.replace(destructureMatch[0], newDestructure);
    fs.writeFileSync(path, result, 'utf8');
    console.log("Success");
} else {
    console.error("Could not find blocks to replace.");
}
