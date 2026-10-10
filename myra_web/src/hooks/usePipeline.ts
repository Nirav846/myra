/**
 * usePipeline — the single client-side state path for MYRA pipeline execution.
 *
 * Mission Control (overview + manual control) and Data Sync (diagnostics +
 * scheduling) both mount this hook, so there is exactly one SSE connection
 * manager and one polling fallback in the app. Neither page runs tasks itself.
 *
 * Transport:
 *   - primary:  SSE on /api/pipeline/events (initial snapshot + live events)
 *   - fallback: 5s polling of /api/pipeline/status, only while SSE is down and
 *               only while the tab is visible.
 *
 * Contract: myra_web/routes/pipeline.py — see src/lib/pipeline.ts.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '../config';
import type { PipelineEvent, PipelineStatus } from '../lib/pipeline';

const POLL_MS = 5000;
const MAX_EVENTS = 200;

export interface UsePipelineResult {
  status: PipelineStatus | null;
  events: PipelineEvent[];
  connected: boolean;
  loading: boolean;
  error: string | null;
  /** True while the run is genuinely executing (not merely 'cancelling'). */
  busy: boolean;
  startTask: (task: string, stopOnFail?: boolean) => Promise<boolean>;
  cancel: () => Promise<void>;
  refresh: () => Promise<void>;
}

export function usePipeline(): UsePipelineResult {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const mounted = useRef(true);
  const requesting = useRef(false);
  const esRef = useRef<EventSource | null>(null);

  const applySnapshot = useCallback((next: PipelineStatus) => {
    if (!mounted.current) return;
    setStatus(next);
    if (Array.isArray(next.events) && next.events.length) {
      setEvents(next.events.slice(-MAX_EVENTS).reverse());
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!mounted.current) return;
    try {
      const res = await fetch(`${API_BASE}/pipeline/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: PipelineStatus = await res.json();
      applySnapshot(json);
      // Deliberately does NOT clear `error`. This runs on every poll and after
      // every task event, so clearing here wiped operator-visible failures
      // (e.g. an HTTP 409 conflict) within ~1s of them being shown. Errors are
      // now cleared only when the operator starts a new action.
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : 'Network error');
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [applySnapshot]);

  // --- SSE with exponential backoff ---------------------------------------
  useEffect(() => {
    mounted.current = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;

    const connect = () => {
      if (!mounted.current) return;
      const es = new EventSource(`${API_BASE}/pipeline/events`);
      esRef.current = es;

      es.onopen = () => {
        delay = 1000;
        if (mounted.current) setConnected(true);
      };

      es.onmessage = (message) => {
        if (!mounted.current) return;
        let data: any;
        try {
          data = JSON.parse(message.data);
        } catch {
          return;
        }

        if (data.type === 'connected' && data.state) {
          applySnapshot(data.state as PipelineStatus);
          // No setError(null) here: a (re)connect snapshot must not wipe an
          // operator-visible error that is still current. Errors are cleared
          // only when a new action is started.
          return;
        }

        if (data.type === 'state_change' && data.state) {
          applySnapshot(data.state as PipelineStatus);
          return;
        }

        const event: PipelineEvent = { ...data, time: data.time ?? new Date().toISOString() };
        setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));

        // Terminal task events carry the authoritative outcome; pull the full
        // status so per-task state is never inferred from a partial payload.
        if (data.type === 'task_started' || data.type === 'task_completed' ||
            data.type === 'run_finished' || data.type === 'tasks_skipped' ||
            data.type === 'all_stopped' || data.type === 'cancellation_requested') {
          refresh();
        }
      };

      es.onerror = () => {
        es.close();
        esRef.current = null;
        if (!mounted.current) return;
        setConnected(false);
        delay = Math.min(delay * 2, 30000);
        retry = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      mounted.current = false;
      esRef.current?.close();
      if (retry) clearTimeout(retry);
    };
  }, [applySnapshot, refresh]);

  // --- Modest polling fallback, only while SSE is down ---------------------
  useEffect(() => {
    if (connected) return;
    const id = setInterval(() => {
      if (document.hidden) return;
      refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [connected, refresh]);

  useEffect(() => { refresh(); }, [refresh]);

  // --- Controls ------------------------------------------------------------
  const startTask = useCallback(async (task: string, stopOnFail = true) => {
    if (requesting.current) return false;
    requesting.current = true;
    // Clear any previous message here, at the start of a deliberate action,
    // rather than on every background poll.
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, stop_on_fail: stopOnFail }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (mounted.current) setError(body.detail ?? `HTTP ${res.status}`);
        return false;
      }
      await refresh();
      return true;
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : 'Network error');
      return false;
    } finally {
      requesting.current = false;
    }
  }, [refresh]);

  const cancel = useCallback(async () => {
    setError(null);
    try {
      await fetch(`${API_BASE}/pipeline/cancel`, { method: 'POST' });
      await refresh();
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : 'Network error');
    }
  }, [refresh]);

  const busy = status?.overall?.busy === true ||
    status?.overall?.status === 'running' ||
    status?.overall?.status === 'cancelling';

  return { status, events, connected, loading, error, busy, startTask, cancel, refresh };
}