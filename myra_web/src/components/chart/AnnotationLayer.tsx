/**
 * Annotation Layer Component
 *
 * Provides basic annotation capabilities for the chart:
 * - Horizontal lines (price levels)
 * - Vertical lines (time markers)
 * - Text labels
 * - Trend lines (start/end points)
 *
 * Annotations are stored in component state (not persisted across sessions).
 * Drag-to-move functionality included.
 */

import { useState, useCallback, useRef, memo, useEffect } from 'react';

export interface Annotation {
  id: string;
  type: 'hline' | 'vline' | 'text' | 'trendline';
  x?: number; // For vline, trendline start/end
  y?: number; // For hline, trendline start/end
  x2?: number; // For trendline end
  y2?: number; // For trendline end
  text?: string; // For text labels
  color: string;
  lineWidth?: number;
  fontSize?: number;
}

interface AnnotationLayerProps {
  /** Current annotations to display */
  annotations: Annotation[];
  /** Callback when annotations change */
  onChange: (annotations: Annotation[]) => void;
  /** Chart dimensions for coordinate mapping */
  chartWidth: number;
  /** Chart height for coordinate mapping */
  chartHeight: number;
  /** Enable annotation mode (click to add) */
  annotationMode: boolean;
  /** Current annotation type to add */
  annotationType: Annotation['type'];
  /** X-axis date categories for positioning */
  xCategories: string[];
  /** Y-axis price range */
  yRange: [number, number];
}

/**
 * Generate unique ID for annotations
 */
const generateId = () => `ann_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

/**
 * Convert pixel coordinates to data coordinates
 */
const pixelToData = (
  pixelX: number,
  pixelY: number,
  width: number,
  height: number,
  xCategories: string[],
  yRange: [number, number]
) => {
  const xIndex = Math.round((pixelX / width) * (xCategories.length - 1));
  const clampedIndex = Math.max(0, Math.min(xIndex, xCategories.length - 1));

  const yValue = yRange[1] - (pixelY / height) * (yRange[1] - yRange[0]);

  return {
    dataIndex: clampedIndex,
    date: xCategories[clampedIndex],
    price: yValue,
  };
};

export const AnnotationLayer = memo(({
  annotations,
  onChange,
  chartWidth,
  chartHeight,
  annotationMode,
  annotationType,
  xCategories,
  yRange,
}: AnnotationLayerProps) => {
  const [isDrawing, setIsDrawing] = useState(false);
  const [currentAnnotation, setCurrentAnnotation] = useState<Partial<Annotation> | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  /**
   * Handle click to add annotation
   */
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!annotationMode || !containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    const pixelX = e.clientX - rect.left;
    const pixelY = e.clientY - rect.top;

    const data = pixelToData(pixelX, pixelY, chartWidth, chartHeight, xCategories, yRange);

    const newAnnotation: Annotation = {
      id: generateId(),
      type: annotationType,
      color: '#ffd700',
      lineWidth: 2,
      fontSize: 12,
    };

    switch (annotationType) {
      case 'hline':
        newAnnotation.y = data.price;
        break;
      case 'vline':
        newAnnotation.x = data.dataIndex;
        break;
      case 'text':
        newAnnotation.x = data.dataIndex;
        newAnnotation.y = data.price;
        newAnnotation.text = 'Label';
        break;
      case 'trendline':
        // Start point - will complete on second click
        newAnnotation.x = data.dataIndex;
        newAnnotation.y = data.price;
        setIsDrawing(true);
        setCurrentAnnotation(newAnnotation);
        return;
    }

    onChange([...annotations, newAnnotation]);
  }, [annotationMode, annotationType, annotations, onChange, chartWidth, chartHeight, xCategories, yRange]);

  /**
   * Handle second click for trendline completion
   */
  useEffect(() => {
    if (!isDrawing || !currentAnnotation) return;

    const handleSecondClick = (e: MouseEvent) => {
      if (!containerRef.current) return;

      const rect = containerRef.current.getBoundingClientRect();
      const pixelX = e.clientX - rect.left;
      const pixelY = e.clientY - rect.top;

      const data = pixelToData(pixelX, pixelY, chartWidth, chartHeight, xCategories, yRange);

      const completedAnnotation: Annotation = {
        ...currentAnnotation as Annotation,
        x2: data.dataIndex,
        y2: data.price,
      };

      onChange([...annotations, completedAnnotation]);
      setIsDrawing(false);
      setCurrentAnnotation(null);
    };

    window.addEventListener('click', handleSecondClick, { once: true });
    return () => window.removeEventListener('click', handleSecondClick);
  }, [isDrawing, currentAnnotation, annotations, onChange, chartWidth, chartHeight, xCategories, yRange]);

  /**
   * Handle drag start
   */
  const handleMouseDown = useCallback((e: React.MouseEvent, annotationId: string) => {
    e.stopPropagation();
    setDraggingId(annotationId);
  }, []);

  /**
   * Handle drag move
   */
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!draggingId || !containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    const pixelX = e.clientX - rect.left;
    const pixelY = e.clientY - rect.top;

    const data = pixelToData(pixelX, pixelY, chartWidth, chartHeight, xCategories, yRange);

    onChange(annotations.map(ann => {
      if (ann.id !== draggingId) return ann;

      const updated = { ...ann };
      switch (ann.type) {
        case 'hline':
          updated.y = data.price;
          break;
        case 'vline':
          updated.x = data.dataIndex;
          break;
        case 'text':
          updated.x = data.dataIndex;
          updated.y = data.price;
          break;
        case 'trendline':
          // Move both endpoints proportionally
          if (updated.x !== undefined && updated.x2 !== undefined) {
            const dx = data.dataIndex - updated.x;
            updated.x = data.dataIndex;
            updated.x2 = updated.x2 + dx;
          }
          if (updated.y !== undefined && updated.y2 !== undefined) {
            const dy = data.price - updated.y;
            updated.y = data.price;
            updated.y2 = updated.y2 + dy;
          }
          break;
      }
      return updated;
    }));
  }, [draggingId, annotations, onChange, chartWidth, chartHeight, xCategories, yRange]);

  /**
   * Handle drag end
   */
  const handleMouseUp = useCallback(() => {
    setDraggingId(null);
  }, []);

  /**
   * Delete annotation on right-click
   */
  const handleContextMenu = useCallback((e: React.MouseEvent, annotationId: string) => {
    e.preventDefault();
    e.stopPropagation();
    onChange(annotations.filter(ann => ann.id !== annotationId));
  }, [annotations, onChange]);

  return (
    <div
      ref={containerRef}
      className="annotation-layer"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: annotationMode ? 'auto' : 'none',
        zIndex: 30,
        cursor: annotationMode ? 'crosshair' : 'default',
      }}
      onClick={handleClick}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Render annotations */}
      {annotations.map(ann => {
        switch (ann.type) {
          case 'hline':
            if (ann.y === undefined) return null;
            const yPercent = ((yRange[1] - ann.y) / (yRange[1] - yRange[0])) * 100;
            return (
              <div
                key={ann.id}
                className="annotation-hline"
                style={{
                  position: 'absolute',
                  left: 0,
                  right: 0,
                  top: `${yPercent}%`,
                  height: `${ann.lineWidth || 1}px`,
                  backgroundColor: ann.color,
                  opacity: 0.8,
                  pointerEvents: 'auto',
                  cursor: 'move',
                }}
                onMouseDown={(e) => handleMouseDown(e, ann.id)}
                onContextMenu={(e) => handleContextMenu(e, ann.id)}
                title={`Price: ${ann.y?.toFixed(2)}`}
              />
            );

          case 'vline':
            if (ann.x === undefined) return null;
            const xPercent = ((ann.x + 0.5) / xCategories.length) * 100;
            return (
              <div
                key={ann.id}
                className="annotation-vline"
                style={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: `${xPercent}%`,
                  width: `${ann.lineWidth || 1}px`,
                  backgroundColor: ann.color,
                  opacity: 0.8,
                  pointerEvents: 'auto',
                  cursor: 'move',
                }}
                onMouseDown={(e) => handleMouseDown(e, ann.id)}
                onContextMenu={(e) => handleContextMenu(e, ann.id)}
                title={`Date: ${xCategories[ann.x]}`}
              />
            );

          case 'text':
            if (ann.x === undefined || ann.y === undefined) return null;
            const textXPercent = ((ann.x + 0.5) / xCategories.length) * 100;
            const textYPercent = ((yRange[1] - ann.y) / (yRange[1] - yRange[0])) * 100;
            return (
              <div
                key={ann.id}
                className="annotation-text"
                style={{
                  position: 'absolute',
                  left: `${textXPercent}%`,
                  top: `${textYPercent}%`,
                  color: ann.color,
                  fontSize: `${ann.fontSize || 12}px`,
                  fontWeight: 'bold',
                  textShadow: '1px 1px 2px black',
                  pointerEvents: 'auto',
                  cursor: 'move',
                  transform: 'translate(-50%, -50%)',
                }}
                onMouseDown={(e) => handleMouseDown(e, ann.id)}
                onContextMenu={(e) => handleContextMenu(e, ann.id)}
                title="Right-click to delete"
              >
                {ann.text || 'Label'}
              </div>
            );

          case 'trendline':
            if (ann.x === undefined || ann.y === undefined || ann.x2 === undefined || ann.y2 === undefined) return null;
            const x1Percent = ((ann.x + 0.5) / xCategories.length) * 100;
            const y1Percent = ((yRange[1] - ann.y) / (yRange[1] - yRange[0])) * 100;
            const x2Percent = ((ann.x2 + 0.5) / xCategories.length) * 100;
            const y2Percent = ((yRange[1] - ann.y2) / (yRange[1] - yRange[0])) * 100;

            const length = Math.sqrt(Math.pow(x2Percent - x1Percent, 2) + Math.pow(y2Percent - y1Percent, 2));
            const angle = Math.atan2(y2Percent - y1Percent, x2Percent - x1Percent) * 180 / Math.PI;

            return (
              <div
                key={ann.id}
                className="annotation-trendline"
                style={{
                  position: 'absolute',
                  left: `${x1Percent}%`,
                  top: `${y1Percent}%`,
                  width: `${length}%`,
                  height: `${ann.lineWidth || 2}px`,
                  backgroundColor: ann.color,
                  opacity: 0.8,
                  transformOrigin: '0 50%',
                  transform: `rotate(${angle}deg)`,
                  pointerEvents: 'auto',
                  cursor: 'move',
                }}
                onMouseDown={(e) => handleMouseDown(e, ann.id)}
                onContextMenu={(e) => handleContextMenu(e, ann.id)}
                title="Trend line - Right-click to delete"
              />
            );

          default:
            return null;
        }
      })}

      {/* Drawing indicator */}
      {isDrawing && (
        <div className="annotation-drawing-indicator" style={{
          position: 'fixed',
          top: 10,
          left: '50%',
          transform: 'translateX(-50%)',
          backgroundColor: 'rgba(0,0,0,0.8)',
          color: 'white',
          padding: '8px 16px',
          borderRadius: '4px',
          fontSize: '12px',
          zIndex: 100,
        }}>
          Click to complete trendline
        </div>
      )}
    </div>
  );
});

AnnotationLayer.displayName = 'AnnotationLayer';

export default AnnotationLayer;
