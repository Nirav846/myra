import { useState, useEffect, useRef } from "react";
import { useSettings } from "../lib/SettingsContext";

const globalCache = new Map<string, { timestamp: number; data: any }>();

interface QueryOptions {
  ttlMs?: number;
  pauseRefresh?: boolean;
}

export function useDataQuery<T>(
  queryKey: string | readonly unknown[],
  queryFn: () => Promise<T>,
  options?: QueryOptions,
) {
  const { settings } = useSettings();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const serializedKey = JSON.stringify(queryKey);
  const queryFnRef = useRef(queryFn);
  queryFnRef.current = queryFn;

  const execute = async (
    signal?: AbortSignal,
    forceRefetch: boolean = false,
  ) => {
    try {
      if (signal?.aborted) return;

      const cached = globalCache.get(serializedKey);
      const ttl = options?.ttlMs || 60000;

      if (!forceRefetch && cached && Date.now() - cached.timestamp < ttl) {
        if (!data) {
          // only set if we don't already have it displayed
          setData(cached.data);
          setLoading(false);
        }
        return;
      }

      if (!data) setLoading(true);
      setError(null);

      // Simulate mock data if needed? It's better if `queryFn` internally handles mockDataMode
      // but let's just let it run.

      const result = await queryFnRef.current();
      if (signal?.aborted) return;

      globalCache.set(serializedKey, { timestamp: Date.now(), data: result });
      setData(result);
    } catch (e: any) {
      if (signal?.aborted) return;
      setError(e.message || "An error occurred");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();

    // Initial fetch
    execute(controller.signal);

    // Setup auto-refresh if interval is set
    let interval: any;
    if (settings.autoRefreshInterval !== "Off" && !options?.pauseRefresh) {
      const msMap: Record<string, number> = {
        "10s": 10000,
        "30s": 30000,
        "1min": 60000,
        "5min": 300000,
      };
      const intervalTime = msMap[settings.autoRefreshInterval] || 10000;

      interval = setInterval(() => {
        if (document.hidden) return; // Pause refresh when tab is not visible
        execute(controller.signal, true);
      }, intervalTime);
    }

    return () => {
      controller.abort();
      if (interval) clearInterval(interval);
    };
  }, [serializedKey, settings.autoRefreshInterval, options?.pauseRefresh]);

  return { data, loading, error, refetch: () => execute(undefined, true) };
}
