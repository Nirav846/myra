import { CSSProperties } from 'react';

/**
 * Skeleton Component - Loading placeholder for content
 * Front-end only update - No backend changes required
 * 
 * @param width - Width of skeleton (string or number)
 * @param height - Height of skeleton (string or number)
 * @param variant - Shape variant (text, circular, rectangular, rounded)
 * @param animation - Animation type (pulse, wave, none)
 */
interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  variant?: 'text' | 'circular' | 'rectangular' | 'rounded';
  animation?: 'pulse' | 'wave' | 'none';
  className?: string;
  style?: CSSProperties;
}

export function Skeleton({
  width = '100%',
  height = '1rem',
  variant = 'rectangular',
  animation = 'pulse',
  className = '',
  style = {}
}: SkeletonProps) {
  const baseStyles: CSSProperties = {
    width,
    height,
    background: 'linear-gradient(90deg, #21262d 25%, #2d333b 50%, #21262d 75%)',
    backgroundSize: '200% 100%',
    borderRadius: variant === 'circular' ? '50%' : variant === 'rounded' ? '8px' : '4px',
    ...(variant === 'text' && { borderRadius: '4px', height: '0.875rem' }),
  };
  
  const animations = {
    pulse: 'skeleton-pulse 1.5s ease-in-out infinite',
    wave: 'skeleton-wave 1.5s ease-in-out infinite',
    none: 'none'
  };
  
  return (
    <div
      className={`skeleton ${className}`}
      style={{
        ...baseStyles,
        ...style,
        animation: animations[animation]
      }}
      aria-hidden="true"
      role="status"
    />
  );
}

/**
 * TableSkeleton - Pre-built skeleton for table loading states
 * 
 * @param rows - Number of rows to display
 * @param columns - Number of columns to display
 */
interface TableSkeletonProps {
  rows?: number;
  columns?: number;
}

export function TableSkeleton({ rows = 5, columns = 6 }: TableSkeletonProps) {
  return (
    <div className="w-full">
      {/* Header row skeleton */}
      <div className="flex gap-2 mb-3">
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton 
            key={i} 
            height="0.75rem" 
            width={`${100 / columns}%`} 
            variant="text" 
            animation="none"
            style={{ opacity: 0.5 }}
          />
        ))}
      </div>
      
      {/* Data row skeletons */}
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex gap-2 mb-2">
          {Array.from({ length: columns }).map((_, colIndex) => (
            <Skeleton 
              key={colIndex} 
              height="2rem" 
              width={`${100 / columns}%`}
              variant="rounded"
              animation={rowIndex % 2 === 0 ? 'pulse' : 'wave'}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * CardSkeleton - Pre-built skeleton for card loading states
 * 
 * @param showHeader - Include header placeholder
 * @param showImage - Include image placeholder
 * @param lines - Number of text lines in content
 */
interface CardSkeletonProps {
  showHeader?: boolean;
  showImage?: boolean;
  lines?: number;
}

export function CardSkeleton({ 
  showHeader = true, 
  showImage = false, 
  lines = 3 
}: CardSkeletonProps) {
  return (
    <div className="p-5 rounded-xl bg-[#21262d]/80 border border-[rgba(148,163,184,0.12)]">
      {showHeader && (
        <div className="flex items-center justify-between mb-4">
          <Skeleton height="1.25rem" width="60%" variant="text" />
          <Skeleton height="1rem" width="2rem" variant="circular" />
        </div>
      )}
      
      {showImage && (
        <Skeleton 
          height="12rem" 
          width="100%" 
          variant="rounded" 
          className="mb-4"
        />
      )}
      
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton 
            key={i} 
            height="0.875rem" 
            width={`${100 - (i * 10)}%`} 
            variant="text"
            animation={i % 2 === 0 ? 'pulse' : 'wave'}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * WidgetSkeleton - Pre-built skeleton for dashboard widgets
 * 
 * @param title - Widget title
 * @param height - Widget height
 */
interface WidgetSkeletonProps {
  title?: string;
  height?: string;
}

export function WidgetSkeleton({ 
  title = 'Widget', 
  height = 'auto' 
}: WidgetSkeletonProps) {
  return (
    <div 
      className="p-5 rounded-xl bg-[#21262d]/80 border border-[rgba(148,163,184,0.12)]"
      style={{ height }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <Skeleton height="1rem" width="40%" variant="text" />
        <Skeleton height="1.5rem" width="2rem" variant="rounded" />
      </div>
      
      {/* Content placeholder */}
      <div className="flex-1 flex items-center justify-center">
        <Skeleton 
          height="3rem" 
          width="80%" 
          variant="rounded"
          animation="wave"
        />
      </div>
    </div>
  );
}
