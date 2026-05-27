import { useEffect, useRef, useCallback } from 'react';
import { useChartStore } from '../store/chartStore';
import {
  getGraphDiv,
  clientToDataCoords,
  nearestVisibleCandle,
  getCandleCenterX,
} from '../utils/chartCoords';
import type { CrosshairOverlayHandle } from '../components/chart/CrosshairOverlay';

interface UseCrosshairOptions {
  enabled?: boolean;
  dates?: string[];
  dataLength?: number;
  formatDate?: (index: number) => string;
  formatPrice?: (price: number) => string;
}

export function useCrosshair(
  plotRef: React.RefObject<any>,
  overlayHandleRef: React.RefObject<CrosshairOverlayHandle | null>,
  options: UseCrosshairOptions = {},
) {
  const {
    enabled = true,
    dates = [],
    dataLength = 0,
    formatDate: formatDateFn,
    formatPrice: formatPriceFn,
  } = options;

  const setHoveredIndex = useChartStore(state => state.setHoveredIndex);
  const rafId = useRef<number | null>(null);
  const lastUpdate = useRef<number>(0);
  const mouseEventRef = useRef<MouseEvent | null>(null);
  const listenersAttached = useRef(false);

  const hideCrosshair = useCallback(() => {
    if (!overlayHandleRef.current) return;
    const el = overlayHandleRef.current.refs;
    if (el.vLine) el.vLine.style.display = 'none';
    if (el.hLine) el.hLine.style.display = 'none';
    if (el.priceLabel) el.priceLabel.style.display = 'none';
    if (el.dateLabel) el.dateLabel.style.display = 'none';
  }, [overlayHandleRef]);

  const updateCrosshairVerticalOnly = useCallback((
    snappedIndex: number,
    snappedX: number,
  ) => {
    if (!overlayHandleRef.current) return;
    const el = overlayHandleRef.current.refs;

    if (el.vLine) {
      el.vLine.style.display = 'block';
      el.vLine.style.transform = `translateX(${snappedX}px)`;
    }
    if (el.hLine) el.hLine.style.display = 'none';
    if (el.priceLabel) el.priceLabel.style.display = 'none';

    if (el.dateLabel) {
      el.dateLabel.style.display = 'block';
      const dateText = formatDateFn
        ? formatDateFn(snappedIndex)
        : dates[snappedIndex] || '';
      el.dateLabel.textContent = dateText;
      const dw = el.dateLabel.offsetWidth || 80;
      const pw = el.dateLabel.parentElement?.offsetWidth || 0;
      el.dateLabel.style.left = `${Math.max(dw / 2, Math.min(snappedX, pw - dw / 2))}px`;
    }
  }, [overlayHandleRef, dates, formatDateFn]);

  const updateCrosshairFull = useCallback((
    snappedIndex: number,
    price: number,
    snappedX: number,
    snappedY: number,
  ) => {
    if (!overlayHandleRef.current) return;
    const el = overlayHandleRef.current.refs;

    if (el.vLine) {
      el.vLine.style.display = 'block';
      el.vLine.style.transform = `translateX(${snappedX}px)`;
    }
    if (el.hLine) {
      el.hLine.style.display = 'block';
      el.hLine.style.transform = `translateY(${snappedY}px)`;
    }

    if (el.priceLabel) {
      el.priceLabel.style.display = 'block';
      el.priceLabel.textContent = formatPriceFn
        ? formatPriceFn(price)
        : price.toFixed(2);
      const ph = el.priceLabel.offsetHeight || 16;
      const maxY = (el.priceLabel.parentElement?.offsetHeight || 0) - ph;
      el.priceLabel.style.top = `${Math.max(ph / 2, Math.min(snappedY, maxY + ph / 2))}px`;
    }

    if (el.dateLabel) {
      el.dateLabel.style.display = 'block';
      const dateText = formatDateFn
        ? formatDateFn(snappedIndex)
        : dates[snappedIndex] || '';
      el.dateLabel.textContent = dateText;
      const dw = el.dateLabel.offsetWidth || 80;
      const pw = el.dateLabel.parentElement?.offsetWidth || 0;
      el.dateLabel.style.left = `${Math.max(dw / 2, Math.min(snappedX, pw - dw / 2))}px`;
    }
  }, [overlayHandleRef, dates, formatDateFn, formatPriceFn]);

  const isMouseInYAxisPane = useCallback((gd: any, clientY: number, yAxisKey: string): boolean => {
    const ax = gd._fullLayout[yAxisKey];
    if (!ax) return false;
    const rect = gd.getBoundingClientRect();
    const graphY = clientY - rect.top;
    return graphY >= ax._offset && graphY <= ax._offset + ax._length;
  }, []);

  const processMouseMove = useCallback(() => {
    const e = mouseEventRef.current;
    if (!e || !overlayHandleRef.current || !overlayHandleRef.current.refs.overlay) return;

    const gd = getGraphDiv(plotRef);
    if (!gd || !gd._fullLayout) {
      hideCrosshair();
      return;
    }

    const coords = clientToDataCoords(gd, e.clientX, e.clientY);
    if (!coords) {
      hideCrosshair();
      return;
    }

    const snappedIndex = nearestVisibleCandle(gd, coords.dataX, dataLength);
    const clampedIndex = Math.max(0, Math.min(dataLength - 1, snappedIndex));

    if (clampedIndex >= 0 && clampedIndex < dataLength) {
      setHoveredIndex(clampedIndex);
    }

    const overlayRect = overlayHandleRef.current.refs.overlay.getBoundingClientRect();

    const candleCenterClientX = getCandleCenterX(gd, clampedIndex);
    const snappedX = candleCenterClientX != null
      ? candleCenterClientX - overlayRect.left
      : e.clientX - overlayRect.left;

    const inMainPane = isMouseInYAxisPane(gd, e.clientY, 'yaxis');

    if (inMainPane && coords.dataY != null) {
      const gdRect = gd.getBoundingClientRect();
      const yaxis = gd._fullLayout.yaxis;
      const priceYPixel = yaxis.d2p(coords.dataY);
      const priceY = gdRect.top + yaxis._offset + priceYPixel;
      const snappedY = priceY - overlayRect.top;
      updateCrosshairFull(clampedIndex, coords.dataY, snappedX, snappedY);
    } else {
      updateCrosshairVerticalOnly(clampedIndex, snappedX);
    }
  }, [plotRef, overlayHandleRef, dataLength, setHoveredIndex, hideCrosshair, isMouseInYAxisPane, updateCrosshairFull, updateCrosshairVerticalOnly]);

  useEffect(() => {
    if (!enabled) return;

    listenersAttached.current = false;
    let mountCleanup: (() => void) | null = null;
    let isMounted = true;

    const tryAttach = () => {
      if (!isMounted) return;
      if (listenersAttached.current) return;

      const gd = getGraphDiv(plotRef);
      const oh = overlayHandleRef.current;
      if (!gd || !gd._fullLayout || !oh || !oh.refs || !oh.refs.overlay) {
        requestAnimationFrame(tryAttach);
        return;
      }

      listenersAttached.current = true;

      const handleMouseMove = (e: MouseEvent) => {
        mouseEventRef.current = e;
        const now = performance.now();
        if (now - lastUpdate.current < 8) {
          if (rafId.current) cancelAnimationFrame(rafId.current);
          rafId.current = requestAnimationFrame(processMouseMove);
          return;
        }
        lastUpdate.current = now;
        processMouseMove();
      };

      const handleMouseLeave = () => {
        mouseEventRef.current = null;
        if (rafId.current) {
          cancelAnimationFrame(rafId.current);
          rafId.current = null;
        }
        setHoveredIndex(-1);
        hideCrosshair();
      };

      gd.addEventListener('mousemove', handleMouseMove);
      gd.addEventListener('mouseleave', handleMouseLeave);

      mountCleanup = () => {
        gd.removeEventListener('mousemove', handleMouseMove);
        gd.removeEventListener('mouseleave', handleMouseLeave);
        if (rafId.current) {
          cancelAnimationFrame(rafId.current);
          rafId.current = null;
        }
        mouseEventRef.current = null;
        listenersAttached.current = false;
        hideCrosshair();
        setHoveredIndex(-1);
      };
    };

    tryAttach();

    return () => {
      isMounted = false;
      if (mountCleanup) mountCleanup();
      if (rafId.current) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      listenersAttached.current = false;
    };
  }, [enabled, plotRef, overlayHandleRef, dataLength, setHoveredIndex, hideCrosshair, processMouseMove]);
}
