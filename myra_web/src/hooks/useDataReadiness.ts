/**
 * useDataReadiness — one shared, change-aware data-readiness feed.
 *
 * Why polling (and not a second SSE stream)
 * ----------------------------------------
 * Mission Control already owns the live *execution* stream
 * (`usePipeline` → `/api/pipeline/events`). Readiness is a different shape of
 * fact: a slow-changing verdict computed from the database and the EOD folder,
 * not a stream of task transitions. The directive's own preference order puts
 * "reuse what exists" first and warns against a second transport purely to look
 * live, so this hook deliberately does **not** open another EventSource. It
 * polls one small endpoint, and only when the answer could have changed.
 *
 * Cost control (the point of the exercise)
 * -----------------------------------------
 * * `changed_only=1&since=<fingerprint>` — an unchanged verdict returns
 *   `{"changed": false}` and the hook does not update React state at all, so
 *   there is no re-render and no payload beyond ~40 bytes.
 * * 30 s while idle; the verdict only moves when a session is ingested, so a
 *   faster idle poll would be pure waste.
 * * 8 s while a run is active (the `busy` flag), so an operator watching a
 *   pipeline see the date move as it happens.
 * * Polling stops entirely while `document.hidden`.
 * * Exponential backoff on repeated errors (5 s → 60 s), and it stops
 *   escalating once a request succeeds.
 * * Exactly one interval — the hook is mounted by one component at a time.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '../config';
import type { DataReadiness, ReadinessResponse } from '../lib/readiness';

/** Idle interval: a verdict changes at most once per trading session. */
const IDLE_MS = 30_000;
/** Interval while a pipeline run is active. */
const ACTIVE_MS = 8_000;
/** Error backoff bounds. */
const BACKOFF_MIN_MS = 5_000;
const BACKOFF_MAX_MS = 60_000;

export interface UseDataReadinessResult {
  readiness: DataReadiness | null;
  /** True until the first answer arrives. */
  loading: boolean;
  /** Last transport error, or null. Not cleared by a successful poll of a stale view. */
  error: string | null;
  /** Epoch ms of the last successful fetch — lets the UI show staleness honestly. */
  lastUpdated: number | null;
  /** Manual re-read (used by the panel's Refresh button). */
  refresh: () => Promise<void>;
}

export function useDataReadiness(busy: boolean): UseDataReadinessResult {
  const [readiness, setReadiness] = useState<DataReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const mounted = useRef(true);
  const fingerprint = useRef<string | null>(null);
  const inFlight = useRef(false);
  const failures = useRef(0);

  const fetchOnce = useCallback(async () => {
    // Never overlap requests: a slow response must not queue a second poll.
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const since = fingerprint.current;
      const url =
        `${API_BASE}/pipeline/readiness?changed_only=1` +
        (since ? `&since=${encodeURIComponent(since)}` : '');
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as ReadinessResponse;
      if (!mounted.current) return;
      if (body.fingerprint) fingerprint.current = body.fingerprint;
      failures.current = 0;
      setError(null);
      if (body.changed && body.readiness) {
        // Only a genuinely changed verdict updates state — an unchanged poll is
        // a no-op for React, so the panel does not re-render.
        setReadiness(body.readiness);
        setLastUpdated(Date.now());
      } else if (!body.changed) {
        // Keep the transport "fresh" for the staleness display without
        // touching the rendered data.
        setLastUpdated(Date.now());
      }
    } catch (e) {
      if (mounted.current) {
        failures.current += 1;
        setError(e instanceof Error ? e.message : 'Network error');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    // A manual refresh must bypass the fingerprint so the user always gets a
    // full payload, even if nothing changed.
    fingerprint.current = null;
    await fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    mounted.current = true;
    void fetchOnce();

    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    const schedule = () => {
      if (cancelled || !mounted.current) return;
      let delay: number;
      if (failures.current > 0) {
        // Exponential backoff, capped.
        delay = Math.min(
          BACKOFF_MAX_MS,
          BACKOFF_MIN_MS * Math.pow(2, Math.min(failures.current - 1, 4)),
        );
      } else {
        delay = busy ? ACTIVE_MS : IDLE_MS;
      }
      timer = setTimeout(async () => {
        if (document.hidden) {
          schedule();
          return;
        }
        await fetchOnce();
        schedule();
      }, delay);
    };

    schedule();

    return () => {
      cancelled = true;
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [busy, fetchOnce]);

  return { readiness, loading, error, lastUpdated, refresh };
}
