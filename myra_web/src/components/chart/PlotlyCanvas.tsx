import { memo, useRef, useCallback } from 'react';
import Plot from 'react-plotly.js';
import { useChartStore } from '../../store/chartStore';

interface PlotlyCanvasProps {
  data: any[];
  layout: any;
  config?: any;
  style?: React.CSSProperties;
  dates: string[];
  plotRef?: React.RefObject<any | null>;
}

export const PlotlyCanvas = memo(({ data, layout, config, style, dates, plotRef }: PlotlyCanvasProps) => {
  const setViewport = useChartStore(state => state.setViewport);
  const setHoveredIndex = useChartStore(state => state.setHoveredIndex);
  const hoverRaf = useRef<number | null>(null);
  const lastUpdate = useRef<number>(0);

  const handleRelayout = useCallback((e: any) => {
    if (e['xaxis.range[0]'] !== undefined && e['xaxis.range[1]'] !== undefined) {
      const from = Number(e['xaxis.range[0]']);
      const to = Number(e['xaxis.range[1]']);
      
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
    } else if (e['xaxis.autorange']) {
        setViewport(null);
    }
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
