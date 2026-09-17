/**
 * Chart Data Service - Phase 5: Data Wiring & Performance
 *
 * Handles fetching, caching, and preparation of chart data from API.
 * Supports chunk loading for historical data and integrates with decimation.
 *
 * @module chartDataService
 */

import { API_BASE } from '../config';
import { isDebug } from '../lib/debug';

/**
 * Candle data structure matching backend schema
 */
export interface CandleData {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  delivery?: number;
  delivery_pct?: number;
  vwap?: number;
}

/**
 * Response type from chart API endpoint
 */
interface ChartApiResponse {
  symbol: string;
  candles: CandleData[];
  metadata?: {
    total_count: number;
    start_date: string;
    end_date: string;
  };
}

/**
 * Query parameters for chart data fetch
 */
export interface ChartQueryParams {
  symbol: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
}

/**
 * In-memory cache for fetched chunks
 * Key: `${symbol}:${from_date}:${to_date}`
 */
const chunkCache = new Map<string, CandleData[]>();

/**
 * Pending requests tracker to avoid duplicate fetches
 */
const pendingRequests = new Map<string, Promise<CandleData[]>>();

/**
 * Generate cache key from query params
 */
function getCacheKey(params: ChartQueryParams): string {
  return `${params.symbol}:${params.from_date || 'start'}:${params.to_date || 'end'}`;
}

/**
 * Fetch chart data from API with caching and deduplication
 *
 * @param params Query parameters
 * @param signal Optional abort signal for cancellation
 * @returns Promise resolving to candle data array
 */
export async function fetchChartData(
  params: ChartQueryParams,
  signal?: AbortSignal
): Promise<CandleData[]> {
  const cacheKey = getCacheKey(params);

  // Check cache first
  const cached = chunkCache.get(cacheKey);
  if (cached) {
    if (isDebug()) console.log('[ChartDataService] Cache hit for', cacheKey);
    return cached;
  }

  // Check if request is already pending
  const pending = pendingRequests.get(cacheKey);
  if (pending) {
    if (isDebug()) console.log('[ChartDataService] Joining pending request for', cacheKey);
    return pending;
  }

  // Build URL
  const url = new URL(`${API_BASE}/chart/${params.symbol}`);

  if (params.from_date) {
    url.searchParams.set('from_date', params.from_date);
  }
  if (params.to_date) {
    url.searchParams.set('to_date', params.to_date);
  }
  if (params.limit) {
    url.searchParams.set('limit', params.limit.toString());
  }

  // Create fetch promise
  const fetchPromise = (async () => {
    try {
      if (isDebug()) console.log('[ChartDataService] Fetching from API:', url.toString());

      const response = await fetch(url.toString(), {
        signal,
        headers: {
          'Accept': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data: ChartApiResponse = await response.json();

      // Validate response — accept either 'candles' or 'data' field name
      const candles = data.candles || (data as any).data;
      if (!candles || !Array.isArray(candles)) {
        throw new Error('Invalid response format: missing candles/data array');
      }

      if (isDebug()) console.log('[ChartDataService] Received', candles.length, 'candles');

      // Cache the result
      chunkCache.set(cacheKey, candles);

      return candles;
    } catch (error) {
      if (isDebug()) console.error('[ChartDataService] Fetch failed:', error);
      throw error;
    } finally {
      // Remove from pending
      pendingRequests.delete(cacheKey);
    }
  })();

  // Track pending request
  pendingRequests.set(cacheKey, fetchPromise);

  return fetchPromise;
}

/**
 * Fetch multiple chunks in parallel with concurrency limit
 * Used for loading historical data in batches
 *
 * @param params Base query params
 * @param chunks Array of date ranges to fetch
 * @param concurrency Max parallel requests (default: 3)
 * @returns Promise resolving to merged candle data
 */
export async function fetchChartChunks(
  params: Omit<ChartQueryParams, 'from_date' | 'to_date'>,
  chunks: Array<{ from_date: string; to_date: string }>,
  concurrency: number = 3,
  externalSignal?: AbortSignal
): Promise<CandleData[]> {
  const results: CandleData[][] = [];
  const controller = new AbortController();

  // If external signal aborts, cancel our internal controller too
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort(externalSignal.reason);
    } else {
      externalSignal.addEventListener('abort', () => {
        controller.abort(externalSignal.reason);
      }, { once: true });
    }
  }

  // Process chunks in batches
  for (let i = 0; i < chunks.length; i += concurrency) {
    const batch = chunks.slice(i, i + concurrency);

    const batchPromises = batch.map(chunk =>
      fetchChartData(
        {
          ...params,
          from_date: chunk.from_date,
          to_date: chunk.to_date,
        },
        controller.signal
      )
    );

    try {
      const batchResults = await Promise.all(batchPromises);
      results.push(...batchResults);
    } catch (error) {
      controller.abort('batch failed, cancelling remaining chunks');
      throw error;
    }
  }

  // Merge and sort results by date
  const merged = results.flat().sort((a, b) =>
    new Date(a.date).getTime() - new Date(b.date).getTime()
  );

  return merged;
}

/**
 * Clear cache for a specific symbol or all symbols
 *
 * @param symbol Optional symbol to clear (clears all if not provided)
 */
export function clearChartCache(symbol?: string): void {
  if (symbol) {
    // Clear only entries for this symbol
    const keysToRemove: string[] = [];
    chunkCache.forEach((_, key) => {
      if (key.startsWith(`${symbol}:`)) {
        keysToRemove.push(key);
      }
    });
    keysToRemove.forEach(key => chunkCache.delete(key));
    if (isDebug()) console.log('[ChartDataService] Cleared cache for symbol:', symbol);
  } else {
    chunkCache.clear();
    if (isDebug()) console.log('[ChartDataService] Cleared all cache');
  }
}

/**
 * Get cache statistics for debugging
 */
export function getCacheStats(): { size: number; keys: string[] } {
  return {
    size: chunkCache.size,
    keys: Array.prototype.slice.call(chunkCache.keys()),
  };
}

/**
 * Prepare data for rendering with optional decimation
 * Integrates with dataDecimator utility
 *
 * @param data Raw candle data
 * @param maxPoints Maximum points to render (default: 2000)
 * @param enableDecimation Whether to apply decimation (default: true)
 * @returns Prepared data array
 */
export async function prepareChartData(
  data: CandleData[],
  maxPoints: number = 2000,
  enableDecimation: boolean = true
): Promise<CandleData[]> {
  if (!enableDecimation || data.length <= maxPoints) {
    return data;
  }

  // Dynamic import to avoid circular dependencies
  const { decimateOHLCVData } = await import('../utils/dataDecimator');

  if (isDebug()) console.log('[ChartDataService] Decimating', data.length, 'points to max', maxPoints);
  return decimateOHLCVData(data, maxPoints);
}

/**
 * Calculate date ranges for chunk loading
 * Used when fetching large historical datasets
 *
 * @param startDate Start date (ISO string)
 * @param endDate End date (ISO string)
 * @param chunkDays Days per chunk (default: 365)
 * @returns Array of date range objects
 */
export function calculateDateChunks(
  startDate: string,
  endDate: string,
  chunkDays: number = 365
): Array<{ from_date: string; to_date: string }> {
  const chunks: Array<{ from_date: string; to_date: string }> = [];
  const start = new Date(startDate);
  const end = new Date(endDate);

  let current = start;
  while (current < end) {
    const chunkEnd = new Date(current);
    chunkEnd.setDate(chunkEnd.getDate() + chunkDays);

    // Don't go past the end date
    if (chunkEnd > end) {
      chunkEnd.setTime(end.getTime());
    }

    chunks.push({
      from_date: current.toISOString().split('T')[0],
      to_date: chunkEnd.toISOString().split('T')[0],
    });

    // Move to next chunk (add 1 day to avoid overlap)
    current = new Date(chunkEnd);
    current.setDate(current.getDate() + 1);
  }

  return chunks;
}
