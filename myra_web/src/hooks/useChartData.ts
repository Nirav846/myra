/**
 * useChartData Hook - Phase 5: Data Wiring & Performance
 *
 * React hook for fetching and managing chart data with caching,
 * chunk loading, and decimation support.
 *
 * @module useChartData
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchChartData,
  fetchChartChunks,
  prepareChartData,
  calculateDateChunks,
  clearChartCache,
  type CandleData,
  type ChartQueryParams,
} from '../services/chartDataService';
import { isDebug } from '../lib/debug';

/**
 * Hook state interface
 */
interface UseChartDataResult {
  /** Fetched candle data */
  data: CandleData[] | null;
  /** Loading state */
  loading: boolean;
  /** Error state */
  error: string | null;
  /** Manual refetch function */
  refetch: () => Promise<void>;
  /** Load more historical data (backward in time) */
  loadMore: () => Promise<void>;
  /** Whether more historical data is available */
  hasMore: boolean;
  /** Total count of available candles (from API metadata) */
  totalCount: number | null;
}

/**
 * Hook options
 */
interface UseChartDataOptions {
  /** Enable/disable auto-fetch on mount */
  enabled?: boolean;
  /** Enable decimation for large datasets */
  enableDecimation?: boolean;
  /** Maximum points after decimation */
  maxPoints?: number;
  /** Days per chunk when loading historically */
  chunkDays?: number;
  /** Initial date range to fetch */
  initialRange?: { from_date?: string; to_date?: string };
  /** Pause automatic refresh */
  pauseRefresh?: boolean;
}

/**
 * Hook for fetching and managing chart data
 *
 * Features:
 * - Automatic caching with deduplication
 * - Chunk loading for historical data
 * - LTTB decimation for performance
 * - AbortController integration for cleanup
 * - Manual refetch and loadMore capabilities
 *
 * @param symbol Stock symbol to fetch
 * @param options Configuration options
 * @returns State and control functions
 */
export function useChartData(
  symbol: string,
  options: UseChartDataOptions = {}
): UseChartDataResult {
  const {
    enabled = true,
    enableDecimation = true,
    maxPoints = 2000,
    chunkDays = 365,
    initialRange,
    pauseRefresh = false,
  } = options;

  // State
  const [data, setData] = useState<CandleData[] | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState<boolean>(true);

  // Refs for tracking fetch state
  const controllerRef = useRef<AbortController | null>(null);
  const fetchedRangeRef = useRef<{ from: string | null; to: string | null }>({
    from: null,
    to: null,
  });

  /**
   * Fetch initial data
   */
  const fetchData = useCallback(async () => {
    // Cancel any pending request
    if (controllerRef.current) {
      controllerRef.current.abort();
    }

    controllerRef.current = new AbortController();
    const signal = controllerRef.current.signal;

    try {
      setLoading(true);
      setError(null);

      const params: ChartQueryParams = {
        symbol,
        from_date: initialRange?.from_date,
        to_date: initialRange?.to_date,
      };

      if (isDebug()) console.log('[useChartData] Fetching initial data for', symbol, params);

      const result = await fetchChartData(params, signal);

      // Prepare data with decimation if enabled
      const prepared = await prepareChartData(result, maxPoints, enableDecimation);

      setData(prepared);

      // Track fetched range
      if (result.length > 0) {
        fetchedRangeRef.current = {
          from: result[0].date,
          to: result[result.length - 1].date,
        };

        // Check if there's more historical data
        setHasMore(!initialRange?.from_date || result[0].date > initialRange.from_date);
      }

      if (isDebug()) console.log('[useChartData] Fetched', prepared.length, 'candles');
    } catch (err: any) {
      if (signal.aborted) {
        if (isDebug()) console.log('[useChartData] Fetch aborted');
        return;
      }

      if (isDebug()) console.error('[useChartData] Fetch failed:', err);
      setError(err.message || 'Failed to fetch chart data');
    } finally {
      if (!signal.aborted) {
        setLoading(false);
      }
    }
  }, [symbol, initialRange?.from_date, initialRange?.to_date, maxPoints, enableDecimation]);

  /**
   * Load more historical data (backward in time)
   */
  const loadMore = useCallback(async () => {
    if (!hasMore || loading) return;

    const currentFrom = fetchedRangeRef.current.from;
    if (!currentFrom) return;

    // Calculate previous chunk
    const startDate = new Date(currentFrom);
    startDate.setDate(startDate.getDate() - 1); // Start one day before

    const endDate = new Date(startDate);
    endDate.setDate(endDate.getDate() - chunkDays);

    if (endDate < new Date('2000-01-01')) {
      setHasMore(false);
      return;
    }

    if (isDebug()) console.log('[useChartData] Loading more historical data...', {
      from: endDate.toISOString().split('T')[0],
      to: startDate.toISOString().split('T')[0],
    });

    try {
      const chunks = calculateDateChunks(
        endDate.toISOString().split('T')[0],
        startDate.toISOString().split('T')[0],
        chunkDays
      );

      const historicalData = await fetchChartChunks(
        { symbol },
        chunks
      );

      if (historicalData.length === 0) {
        setHasMore(false);
        return;
      }

      // Merge with existing data
      setData(prevData => {
        if (!prevData) return historicalData;

        // Combine and deduplicate by date
        const merged = [...historicalData, ...prevData];
        const uniqueMap = new Map<string, CandleData>();

        merged.forEach(candle => {
          uniqueMap.set(candle.date, candle);
        });

        const unique = Array.from(uniqueMap.values()).sort(
          (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
        );

        // Apply decimation if needed
        if (enableDecimation && unique.length > maxPoints) {
          // Keep most recent data intact, decimate older portion
          const keepRecent = Math.min(Math.floor(maxPoints / 2), unique.length);
          const recentData = unique.slice(-keepRecent);
          const olderData = unique.slice(0, -keepRecent);

          // Decimate older portion more aggressively
          import('../utils/dataDecimator').then(({ decimateOHLCVData }) => {
            const decimatedOlder = decimateOHLCVData(olderData, Math.floor(maxPoints / 2));
            setData([...decimatedOlder, ...recentData]);
          });
          return unique;
        }

        return unique;
      });

      // Update fetched range
      fetchedRangeRef.current.from = historicalData[0].date;

      // Check if we've reached the beginning
      if (historicalData.length < chunkDays * 0.8) {
        setHasMore(false);
      }
    } catch (err: any) {
      if (isDebug()) console.error('[useChartData] Failed to load more:', err);
      setError(err.message || 'Failed to load historical data');
    }
  }, [hasMore, loading, symbol, chunkDays, enableDecimation, maxPoints]);

  /**
   * Manual refetch
   */
  const refetch = useCallback(async () => {
    // Clear cache for fresh data
    clearChartCache(symbol);
    await fetchData();
  }, [symbol, fetchData]);

  // Initial fetch on mount
  useEffect(() => {
    if (enabled && !pauseRefresh) {
      fetchData();
    }

    // Cleanup on unmount
    return () => {
      if (controllerRef.current) {
        controllerRef.current.abort();
      }
    };
  }, [enabled, pauseRefresh, fetchData]);

  return {
    data,
    loading,
    error,
    refetch,
    loadMore,
    hasMore,
    totalCount,
  };
}

export default useChartData;
