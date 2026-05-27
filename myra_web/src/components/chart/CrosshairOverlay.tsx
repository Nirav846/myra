import React, { forwardRef, useImperativeHandle, useRef, memo } from 'react';

export interface CrosshairElementRefs {
  overlay: HTMLDivElement | null;
  vLine: HTMLDivElement | null;
  hLine: HTMLDivElement | null;
  priceLabel: HTMLDivElement | null;
  dateLabel: HTMLDivElement | null;
}

export interface CrosshairOverlayHandle {
  refs: CrosshairElementRefs;
}

const CrosshairOverlay = forwardRef<CrosshairOverlayHandle, {}>((_props, ref) => {
  const overlayRef = useRef<HTMLDivElement>(null);
  const vLineRef = useRef<HTMLDivElement>(null);
  const hLineRef = useRef<HTMLDivElement>(null);
  const priceLabelRef = useRef<HTMLDivElement>(null);
  const dateLabelRef = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    refs: {
      get overlay() { return overlayRef.current; },
      get vLine() { return vLineRef.current; },
      get hLine() { return hLineRef.current; },
      get priceLabel() { return priceLabelRef.current; },
      get dateLabel() { return dateLabelRef.current; },
    },
  }), []);

  return (
    <div
      ref={overlayRef}
      className="crosshair-overlay"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 20,
        overflow: 'hidden',
      }}
    >
      <div
        ref={vLineRef}
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          width: 0,
          borderLeft: '1px dashed rgba(255, 170, 0, 0.6)',
          display: 'none',
          willChange: 'transform',
        }}
      />
      <div
        ref={hLineRef}
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          height: 0,
          borderTop: '1px dashed rgba(255, 170, 0, 0.6)',
          display: 'none',
          willChange: 'transform',
        }}
      />
      <div
        ref={priceLabelRef}
        style={{
          position: 'absolute',
          left: '2px',
          padding: '1px 5px',
          fontSize: '10px',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
          color: '#fff',
          backgroundColor: 'rgba(26, 28, 36, 0.92)',
          borderRadius: '2px',
          whiteSpace: 'nowrap',
          display: 'none',
          transform: 'translateY(-50%)',
          pointerEvents: 'none',
          zIndex: 21,
        }}
      />
      <div
        ref={dateLabelRef}
        style={{
          position: 'absolute',
          bottom: '0px',
          padding: '1px 5px',
          fontSize: '10px',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
          color: '#fff',
          backgroundColor: 'rgba(26, 28, 36, 0.92)',
          borderRadius: '2px',
          whiteSpace: 'nowrap',
          display: 'none',
          transform: 'translateX(-50%)',
          pointerEvents: 'none',
          zIndex: 21,
        }}
      />
    </div>
  );
});

CrosshairOverlay.displayName = 'CrosshairOverlay';

export default memo(CrosshairOverlay);
