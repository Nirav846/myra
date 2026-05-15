import { memo, useRef, useEffect, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { useChartStore } from '../../store/chartStore';

interface PlotlyCanvasProps {
  data: any[];
  layout: any;
  config?: any;
  style?: React.CSSProperties;
  dates: string[];
}

export const PlotlyCanvas = memo(({ data, layout, config, style, dates }: PlotlyCanvasProps) => {
  const setViewport = useChartStore(state => state.setViewport);
  const setHoveredIndex = useChartStore(state => state.setHoveredIndex);
  const hoverRaf = useRef<number | null>(null);
  const lastUpdate = useRef<number>(0);

  const handleRelayout = (e: any) => {
    if (e['xaxis.range[0]'] !== undefined && e['xaxis.range[1]'] !== undefined) {
      let from = e['xaxis.range[0]'];
      let to = e['xaxis.range[1]'];
      
      if (typeof from === 'string') {
         const idx = dates.indexOf(from);
         if (idx !== -1) from = idx;
      }
      if (typeof to === 'string') {
         const idx = dates.indexOf(to);
         if (idx !== -1) to = idx;
      }
      
      if (typeof from === 'number' && typeof to === 'number') {
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
    } else if (e['xaxis.autorange']) {
        setViewport(null);
    }
  };

  const handleHover = useCallback((e: any) => {
    if (e.points && e.points.length > 0) {
        const pt = e.points[0];
        let idx = pt.pointIndex !== undefined ? pt.pointIndex : pt.pointNumber;
        
        if (typeof idx !== 'number' && pt.x) {
            const dateIdx = dates.indexOf(pt.x);
            if (dateIdx !== -1) {
                idx = dateIdx;
            }
        }
        
        // Sometimes Plotly candlestick pointNumber is an array [index, ...]
        if (Array.isArray(idx)) {
            idx = idx[0];
        }

        if (idx !== undefined && typeof idx === 'number') {
            const now = performance.now();
            if (now - lastUpdate.current < 33) return; // 30fps throttle
            lastUpdate.current = now;

            if (hoverRaf.current !== null) {
                cancelAnimationFrame(hoverRaf.current);
            }
            hoverRaf.current = requestAnimationFrame(() => {
                setHoveredIndex(idx);
            });
        }
    }
  }, [setHoveredIndex, dates]);

  const handleUnhover = useCallback(() => {
    if (hoverRaf.current !== null) {
        cancelAnimationFrame(hoverRaf.current);
    }
    setHoveredIndex(-1);
  }, [setHoveredIndex]);

  return (
    <Plot
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
