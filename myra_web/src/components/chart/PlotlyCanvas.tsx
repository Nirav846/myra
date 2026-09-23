import { memo, useRef, useCallback, useEffect } from 'react';
// Use plotly.js-dist-min for better tree-shaking instead of full plotly.js
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import { useChartStore } from '../../store/chartStore';

interface PlotlyCanvasProps {
  data: any[];
  layout: any;
  config?: any;
  style?: React.CSSProperties;
  dates: string[];
  plotRef?: React.RefObject<any | null>;
}

// Debounce helper for relayout events
function createRelayoutDebounce() {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  let pendingData: any | null = null;
  
  const flush = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
  };
  
  const schedule = (data: any, callback: (data: any) => void, delayMs: number) => {
    flush();
    pendingData = data;
    timeoutId = setTimeout(() => {
      if (pendingData) {
        callback(pendingData);
        pendingData = null;
      }
      timeoutId = null;
    }, delayMs);
  };
  
  return { schedule, flush };
}

export const PlotlyCanvas = memo(({ data, layout, config, style, dates, plotRef }: PlotlyCanvasProps) => {
  const setViewport = useChartStore(state => state.setViewport);
  const setHoveredIndex = useChartStore(state => state.setHoveredIndex);
  const hoverRaf = useRef<number | null>(null);
  const lastUpdate = useRef<number>(0);
  const relayoutDebounce = useRef(createRelayoutDebounce());
  
  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      relayoutDebounce.current.flush();
    };
  }, []);

  const handleRelayout = useCallback((e: any) => {
    // Debounce relayout events to prevent excessive API calls during rapid zooming/panning
    relayoutDebounce.current.schedule(e, (debouncedData: any) => {
      if (debouncedData['xaxis.range[0]'] !== undefined && debouncedData['xaxis.range[1]'] !== undefined) {
        const from = Number(debouncedData['xaxis.range[0]']);
        const to = Number(debouncedData['xaxis.range[1]']);
        
        if (isFinite(from) && isFinite(to)) {
            const startIndex = Math.min(from, to);
            const endIndex = Math.max(from, to);
            setViewport({
                startIndex,
                endIndex,
                startTime: dates[Math.floor(startIndex)] || null,
                endTime: dates[Math.ceil(endIndex)] || null,
                candleCount: endIndex - startIndex + 1,
            });
        } else {
            setViewport(null);
        }
      } else if (debouncedData['xaxis.autorange']) {
          setViewport(null);
      }
    }, 150); // 150ms debounce delay
  }, [setViewport, dates]);

  const handleHover = useCallback((e: any) => {
    if (!e.points || e.points.length === 0) return;
    const pt = e.points[0];
    let idx = pt.pointIndex !== undefined ? pt.pointIndex : pt.pointNumber;
    
    if (Array.isArray(idx)) {
        idx = idx[0];
    }

    if (typeof idx === 'number' && isFinite(idx)) {
        const now = performance.now();
        if (now - lastUpdate.current < 33) return;
        lastUpdate.current = now;

        if (hoverRaf.current !== null) {
            cancelAnimationFrame(hoverRaf.current);
        }
        hoverRaf.current = requestAnimationFrame(() => {
            setHoveredIndex(idx);
        });
    }
  }, [setHoveredIndex]);

  const handleUnhover = useCallback(() => {
    if (hoverRaf.current !== null) {
        cancelAnimationFrame(hoverRaf.current);
    }
    setHoveredIndex(-1);
  }, [setHoveredIndex]);

  return (
    <Plot
      ref={plotRef}
      data={data}
      layout={layout}
      config={config}
      style={style}
      onRelayout={handleRelayout}
      onHover={handleHover}
      onUnhover={handleUnhover}
      useResizeHandler={true}
    />
  );
});
