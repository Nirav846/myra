import { useEffect, useRef, useCallback } from 'react';
import Plotly from 'plotly.js-dist-min';

export function useCrosshair(
  plotRef: React.RefObject<any>,
  options: { enabled?: boolean; lineColor?: string; lineWidth?: number; lineDash?: string; opacity?: number } = {}
) {
  const {
    enabled = true,
    lineColor = '#ffaa00',
    lineWidth = 1,
    lineDash = 'dot',
    opacity = 0.6,
  } = options;

  const lastCall = useRef(0);
  const rafId = useRef<number | null>(null);

  const clear = useCallback(() => {
    const el = plotRef?.current?.el || plotRef?.current;
    if (!el) return;
    if (rafId.current) cancelAnimationFrame(rafId.current);
    const layout = el.layout || {};
    const existingShapes = layout.shapes || [];
    const existingAnnotations = layout.annotations || [];
    const filteredShapes = existingShapes.filter((s: any) => s._isCrosshair !== true);
    const filteredAnnotations = existingAnnotations.filter((a: any) => a._isCrosshair !== true);
    if (filteredShapes.length !== existingShapes.length || filteredAnnotations.length !== existingAnnotations.length) {
      Plotly.relayout(el, { shapes: filteredShapes, annotations: filteredAnnotations });
    }
  }, [plotRef]);

  const update = useCallback(
    (xval: any, yval: number) => {
      const el = plotRef?.current?.el || plotRef?.current;
      if (!el || !enabled) return;

      const now = performance.now();
      if (now - lastCall.current < 16) {
        if (rafId.current) cancelAnimationFrame(rafId.current);
        rafId.current = requestAnimationFrame(() => update(xval, yval));
        return;
      }
      lastCall.current = now;

      const shapes: any[] = [
        {
          type: 'line',
          x0: xval, y0: 0, x1: xval, y1: 1,
          xref: 'x', yref: 'paper',
          line: { color: lineColor, width: lineWidth, dash: lineDash as any },
          opacity, _isCrosshair: true,
        },
        {
          type: 'line',
          x0: 0, y0: yval, x1: 1, y1: yval,
          xref: 'paper', yref: 'y',
          line: { color: lineColor, width: lineWidth, dash: lineDash as any },
          opacity, _isCrosshair: true,
        },
      ];

      const dateStr = formatDate(xval);
      const priceStr = yval != null ? yval.toFixed(2) : '';

      const annotations: any[] = [];
      if (dateStr) {
        annotations.push({
          x: xval, y: 0, xref: 'x', yref: 'paper',
          text: dateStr, showarrow: false,
          font: { size: 11, color: '#fff', family: 'monospace' },
          bgcolor: 'rgba(20,20,20,0.85)', borderpad: 2,
          yanchor: 'bottom', yshift: -6, _isCrosshair: true,
        });
      }
      if (priceStr) {
        annotations.push({
          x: 0, y: yval, xref: 'paper', yref: 'y',
          text: priceStr, showarrow: false,
          font: { size: 11, color: '#fff', family: 'monospace' },
          bgcolor: 'rgba(20,20,20,0.85)', borderpad: 2,
          xanchor: 'left', xshift: 4, _isCrosshair: true,
        });
      }

      const layout = el.layout || {};
      const existingShapes = (layout.shapes || []).filter((s: any) => s._isCrosshair !== true);
      const existingAnnotations = (layout.annotations || []).filter((a: any) => a._isCrosshair !== true);
      Plotly.relayout(el, {
        shapes: [...existingShapes, ...shapes],
        annotations: [...existingAnnotations, ...annotations],
      });
    },
    [enabled, lineColor, lineWidth, lineDash, opacity, plotRef]
  );

  useEffect(() => {
    const el = plotRef?.current?.el || plotRef?.current;
    if (!el) return;

    const onHover = (data: any) => {
      if (data.points && data.points.length > 0) {
        const pt = data.points[0];
        update(pt.x, pt.y);
      }
    };
    const onUnhover = () => clear();

    el.on('plotly_hover', onHover);
    el.on('plotly_unhover', onUnhover);

    return () => {
      try { el.removeListener('plotly_hover', onHover); } catch (e) {}
      try { el.removeListener('plotly_unhover', onUnhover); } catch (e) {}
      clear();
    };
  }, [plotRef?.current, update, clear]);

  function formatDate(xval: any): string {
    if (xval == null) return '';
    if (typeof xval === 'string' && xval.match(/^\d{4}-\d{2}-\d{2}/)) {
      const d = new Date(xval);
      return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    }
    if (typeof xval === 'number') {
      const d = new Date(xval);
      if (!isNaN(d.getTime())) {
        return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
      }
    }
    return String(xval);
  }
}
