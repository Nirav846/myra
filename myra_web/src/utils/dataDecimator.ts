/**
 * Data Decimator - Reduces large datasets while preserving visual fidelity
 * Uses LTTB (Largest-Triangle-Three-Buckets) algorithm for optimal downsampling
 */

interface DataPoint {
  x: number;
  y: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
  [key: string]: any;
}

/**
 * LTTB Algorithm - Preserves visual appearance better than simple averaging
 * @param data Array of data points
 * @param threshold Target number of points
 * @returns Downsampled array maintaining visual characteristics
 */
export function lttbDecimate<T extends DataPoint>(data: T[], threshold: number): T[] {
  if (data.length <= threshold) return data;
  
  const result: T[] = [];
  const dataLength = data.length;
  const bucketSize = Math.max(1, Math.floor(dataLength / threshold));
  
  // Always include first point
  result.push(data[0]);
  
  let lastSelectedIndex = 0;
  
  for (let i = 1; i < threshold - 1; i++) {
    const bucketStart = Math.floor(i * bucketSize);
    const bucketEnd = Math.min(dataLength, bucketStart + bucketSize);
    
    if (bucketStart >= dataLength) break;
    
    // Find point in current bucket that maximizes triangle area with previous and next average
    const nextBucketStart = Math.min(dataLength, bucketEnd + bucketSize);
    const nextBucketEnd = Math.min(dataLength, nextBucketStart + bucketSize);
    
    // Calculate average of next bucket
    let avgX = 0;
    let avgY = 0;
    let nextCount = 0;
    
    for (let j = nextBucketStart; j < nextBucketEnd; j++) {
      avgX += data[j].x ?? j;
      avgY += data[j].y ?? data[j].close ?? 0;
      nextCount++;
    }
    
    if (nextCount > 0) {
      avgX /= nextCount;
      avgY /= nextCount;
    } else {
      // Fallback: use last point
      avgX = data[dataLength - 1].x ?? (dataLength - 1);
      avgY = data[dataLength - 1].y ?? data[dataLength - 1].close ?? 0;
    }
    
    const prevPoint = data[lastSelectedIndex];
    const prevX = prevPoint.x ?? lastSelectedIndex;
    const prevY = prevPoint.y ?? prevPoint.close ?? 0;
    
    // Find point in current bucket with largest triangle area
    let maxArea = -1;
    let maxIndex = bucketStart;
    
    for (let j = bucketStart; j < bucketEnd; j++) {
      const currX = data[j].x ?? j;
      const currY = data[j].y ?? data[j].close ?? 0;
      
      // Calculate triangle area using cross product
      const area = Math.abs(
        (prevX - currX) * (avgY - currY) - 
        (prevX - avgX) * (currY - prevY)
      );
      
      if (area > maxArea) {
        maxArea = area;
        maxIndex = j;
      }
    }
    
    result.push(data[maxIndex]);
    lastSelectedIndex = maxIndex;
  }
  
  // Always include last point
  if (result[result.length - 1] !== data[dataLength - 1]) {
    result.push(data[dataLength - 1]);
  }
  
  return result;
}

/**
 * Simple step-based decimation (faster but less accurate)
 * @param data Array of data points
 * @param threshold Target number of points
 * @returns Downsampled array
 */
export function stepDecimate<T extends DataPoint>(data: T[], threshold: number): T[] {
  if (data.length <= threshold) return data;
  
  const result: T[] = [];
  const step = Math.ceil(data.length / threshold);
  
  for (let i = 0; i < data.length; i += step) {
    result.push(data[i]);
  }
  
  // Ensure last point is included
  if (result[result.length - 1] !== data[data.length - 1]) {
    result.push(data[data.length - 1]);
  }
  
  return result;
}

/**
 * Adaptive decimation - chooses algorithm based on data size
 * @param data Raw OHLCV data array
 * @param maxPoints Maximum points to render (default: 2000)
 * @returns Downsampled data array
 */
export function decimateOHLCVData<T extends Record<string, any>>(
  data: T[], 
  maxPoints: number = 2000
): T[] {
  if (!data || data.length <= maxPoints) return data;
  
  // For very large datasets (>10k points), use LTTB for better quality
  // For moderate datasets, use faster step decimation
  if (data.length > 10000) {
    // Convert to DataPoint format for LTTB
    const formattedData: DataPoint[] = data.map((d, i) => ({
      x: i,
      y: d.close ?? ((d.high + d.low) / 2),
      ...d
    }));
    
    const decimated = lttbDecimate(formattedData, maxPoints);
    return decimated.map(d => {
      const { x, y, ...rest } = d;
      return rest as T;
    });
  } else {
    return stepDecimate(data, maxPoints);
  }
}

/**
 * Check if decimation should be applied based on chart width and data density
 * @param dataLength Number of data points
 * @param chartWidth Chart width in pixels
 * @param pointsPerPixel Maximum points per pixel before decimation kicks in
 * @returns True if decimation should be applied
 */
export function shouldDecimate(
  dataLength: number, 
  chartWidth: number = 1200, 
  pointsPerPixel: number = 2
): boolean {
  return dataLength > chartWidth * pointsPerPixel;
}
