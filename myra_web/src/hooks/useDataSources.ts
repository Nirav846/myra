/**
 * useDataSources — state + actions for the Data Sources panel.
 *
 * Owns the source list (loaded from /api/sources), a per-source busy flag, and
 * the latest diagnostic-test result. Patch/reset actions optimistically apply
 * the change and fall back to the server's view on failure.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  DataSource,
  SourceTestResult,
  SourceTuning,
  fetchSources,
  patchSource,
  resetSource,
  testSource,
} from '../lib/sources';

export interface UseDataSourcesResult {
  sources: DataSource[];
  loading: boolean;
  error: string | null;
  /** name -> in-flight action ('save' | 'test' | 'reset') or null. */
  busy: Record<string, string | null>;
  testResults: Record<string, { loading: boolean; result?: SourceTestResult }>;
  refresh: () => Promise<void>;
  applyPatch: (name: string, patch: SourceTuning) => Promise<boolean>;
  reset: (name: string) => Promise<boolean>;
  runTest: (name: string) => Promise<void>;
  clearError: () => void;
}

export function useDataSources(): UseDataSourcesResult {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, string | null>>({});
  const [testResults, setTestResults] = useState<
    Record<string, { loading: boolean; result?: SourceTestResult }>
  >({});

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchSources();
      if (mounted.current) {
        setSources(next);
        setLoading(false);
      }
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof Error ? e.message : 'Failed to load data sources');
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const upsert = useCallback((name: string, mutate: (s: DataSource) => DataSource) => {
    setSources((prev) => prev.map((s) => (s.name === name ? mutate(s) : s)));
  }, []);

  const clearError = useCallback(() => setError(null), []);

  const applyPatch = useCallback(
    async (name: string, patch: SourceTuning): Promise<boolean> => {
      setBusy((b) => ({ ...b, [name]: 'save' }));
      setError(null);
      // Optimistic enabled-flip so the toggle feels instant; full patches wait
      // for the server to confirm.
      if (patch.enabled !== undefined) {
        upsert(name, (s) => ({ ...s, enabled: !!patch.enabled }));
      }
      try {
        const updated = await patchSource(name, patch);
        // Keep the previous health snapshot if the write response omits it.
        upsert(name, (s) => ({ ...s, ...updated, health: updated.health ?? s.health }));
        return true;
      } catch (e) {
        await refresh(); // revert the optimistic flip to server truth
        if (mounted.current) {
          setError(e instanceof Error ? e.message : 'Failed to update source');
        }
        return false;
      } finally {
        if (mounted.current) setBusy((b) => ({ ...b, [name]: null }));
      }
    },
    [refresh, upsert],
  );

  const reset = useCallback(
    async (name: string): Promise<boolean> => {
      setBusy((b) => ({ ...b, [name]: 'reset' }));
      setError(null);
      try {
        const updated = await resetSource(name);
        upsert(name, (s) => ({ ...s, ...updated, health: updated.health ?? s.health }));
        return true;
      } catch (e) {
        if (mounted.current) {
          setError(e instanceof Error ? e.message : 'Failed to reset source');
        }
        return false;
      } finally {
        if (mounted.current) setBusy((b) => ({ ...b, [name]: null }));
      }
    },
    [upsert],
  );

  const runTest = useCallback(async (name: string) => {
    setBusy((b) => ({ ...b, [name]: 'test' }));
    setError(null);
    setTestResults((t) => ({ ...t, [name]: { loading: true } }));
    try {
      const result = await testSource(name);
      if (mounted.current) setTestResults((t) => ({ ...t, [name]: { loading: false, result } }));
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Test failed';
      if (mounted.current) {
        setTestResults((t) => ({
          ...t,
          [name]: {
            loading: false,
            result: { source: name, supported: false, ok: false, error: message },
          },
        }));
        setError(message);
      }
    } finally {
      if (mounted.current) setBusy((b) => ({ ...b, [name]: null }));
    }
  }, []);

  return { sources, loading, error, busy, testResults, refresh, applyPatch, reset, runTest, clearError };
}
