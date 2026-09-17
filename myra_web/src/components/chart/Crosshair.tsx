/**
 * Simple Crosshair Component for Chart V2
 *
 * Renders crosshair lines at specified coordinates.
 * Lightweight alternative to the ref-based CrosshairOverlay.
 */

import { memo } from 'react';

export type CrosshairStyle = 'solid' | 'dashed' | 'dotted';

interface CrosshairProps {
  /** X coordinate (pixel position from left) */
  x: number;
  /** Y coordinate (pixel position from top) */
  y: number;
  /** X-axis date labels for tooltip */
  dates?: string[];
  /** Line style */
  style?: CrosshairStyle;
  /** Color override */
  color?: string;
  /** Opacity 0-1 */
  opacity?: number;
}

const styleMap: Record<CrosshairStyle, string> = {
  solid: 'solid',
  dashed: 'dashed',
  dotted: 'dotted',
};

export const Crosshair = memo(({
  x,
  y,
  dates,
  style = 'dashed',
  color = 'rgba(255, 170, 0, 0.6)',
  opacity = 0.6,
}: CrosshairProps) => {
  const borderStyle = styleMap[style];

  return (
    <div
      className="crosshair-overlay"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 20,
        overflow: 'hidden',
      }}
    >
      {/* Vertical line */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: x,
          borderLeft: `1px ${borderStyle} ${color}`,
          opacity,
        }}
      />

      {/* Horizontal line */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: y,
          borderTop: `1px ${borderStyle} ${color}`,
          opacity,
        }}
      />

      {/* Date label */}
      {dates && dates.length > 0 && (
        <div
          style={{
            position: 'absolute',
            bottom: '2px',
            left: x,
            transform: 'translateX(-50%)',
            padding: '1px 5px',
            fontSize: '10px',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            color: '#fff',
            backgroundColor: 'rgba(26, 28, 36, 0.92)',
            borderRadius: '2px',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}
        >
          {dates[Math.min(Math.floor(x / 8), dates.length - 1)] || ''}
        </div>
      )}

      {/* Price label (placeholder - would need y-axis scale) */}
      <div
        style={{
          position: 'absolute',
          right: '2px',
          top: y,
          transform: 'translateY(-50%)',
          padding: '1px 5px',
          fontSize: '10px',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
          color: '#fff',
          backgroundColor: 'rgba(26, 28, 36, 0.92)',
          borderRadius: '2px',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}
      >
        {/* Price value would be calculated from y coordinate and y-axis scale */}
      </div>
    </div>
  );
});

Crosshair.displayName = 'Crosshair';

export default Crosshair;
