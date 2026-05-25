import { useState, useEffect, useCallback, useRef } from 'react';

interface LazyWidgetOptions {
  autoRefreshInterval?: number;
}

export function useLazyWidgetData<T>(
  widgetId: string,
  fetcher: () => Promise<T>,
  options?: LazyWidgetOptions,
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefreshState] = useState(() => {
    const stored = localStorage.getItem(`myra_autorefresh_${widgetId}`);
    return stored === 'true';
  });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const intervalMs = options?.autoRefreshInterval ?? 300_000;

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcherRef.current();
      setData(result);
    } catch (e: any) {
      setError(e.message || 'An error occurred');
    } finally {
      setLoading(false);
    }
  }, []);

  const setAutoRefresh = useCallback((on: boolean) => {
    setAutoRefreshState(on);
    localStorage.setItem(`myra_autorefresh_${widgetId}`, String(on));
    if (on) fetchData();
  }, [widgetId, fetchData]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (document.hidden) return;
      fetchData();
    }, intervalMs);
    return () => clearInterval(id);
  }, [autoRefresh, intervalMs, fetchData]);

  return { data, loading, error, fetchData, autoRefresh, setAutoRefresh };
}
