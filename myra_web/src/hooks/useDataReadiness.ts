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
  /** What kind of failure this is, so the UI can say something actionable. */
  errorKind: ReadinessErrorKind | null;
  /** Epoch ms of the last successful fetch — lets the UI show staleness honestly. */
  lastUpdated: number | null;
  /** Manual re-read (used by the panel's Refresh button). */
  refresh: () => Promise<void>;
}

/**
 * Phase 11: why the last fetch failed.
 *
 * The browser collapses every network-level problem — connection refused, DNS,
 * CORS, TLS — into the single string `Failed to fetch`. Showing that verbatim
 * told the operator nothing actionable while concealing which of the very
 * different remedies applied. These kinds keep the failure distinct without
 * inventing a cause we cannot actually observe.
 */
export type ReadinessErrorKind =
  /** Backend unreachable: connection refused, DNS, CORS block, TLS. */
  | 'unreachable'
  /** Backend answered, but with a non-2xx status. */
  | 'http'
  /** Backend answered 2xx but the payload was not the contract we expect. */
  | 'malformed';

/** Human-facing text. Never says "ready" and never claims a cause we cannot see. */
export function describeReadinessError(kind: ReadinessErrorKind, detail: string): string {
  switch (kind) {
    case 'unreachable':
      return `backend unreachable (${trim(detail)}) — start it with: python run_fastapi.py`;
    case 'http': {
      // In dev the request goes through the Vite proxy, which answers 502/503/504
      // when the API it fronts is not running. That is the common case and it
      // has a concrete remedy, so say so instead of "did not answer normally".
      const status = /^HTTP\s+(\d{3})$/.exec(detail)?.[1];
      if (status === '502' || status === '503' || status === '504') {
        return `API not responding (HTTP ${status} via proxy) — start it with: python run_fastapi.py`;
      }
      return `backend returned ${trim(detail)} — the readiness API did not answer normally`;
    }
    case 'malformed':
      return `backend returned an unreadable readiness payload (${trim(detail)})`;
    default:
      return detail;
  }
}

/** Drop a trailing period so composed sentences do not read "..". */
function trim(detail: string): string {
  return detail.replace(/\.+$/, '');
}

/** Parse + validate the readiness contract. Throws with a reason we can surface. */
function parseReadiness(body: unknown): ReadinessResponse {
  if (typeof body !== 'object' || body === null) {
    throw new Error('response was not a JSON object');
  }
  const r = body as Record<string, unknown>;
  if (typeof r.changed !== 'boolean') {
    throw new Error('missing "changed" flag');
  }
  // An unchanged answer legitimately carries no payload; anything else must.
  if (r.changed === true && (typeof r.readiness !== 'object' || r.readiness === null)) {
    throw new Error('changed verdict carried no "readiness" payload');
  }
  return body as ReadinessResponse;
}

export function useDataReadiness(busy: boolean): UseDataReadinessResult {
  const [readiness, setReadiness] = useState<DataReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<ReadinessErrorKind | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const mounted = useRef(true);
  const fingerprint = useRef<string | null>(null);
  const inFlight = useRef(false);
  const failures = useRef(0);
  /**
   * Epoch ms of the last time we *told React anything*. An unchanged poll
   * refreshes this ref rather than state, so a steady 30 s poll causes zero
   * re-renders. Previously `setLastUpdated` fired on every unchanged poll,
   * which contradicted this hook's own documented behaviour and re-rendered the
   * panel forever for a value that never changed.
   */
  const lastSuccessRef = useRef<number | null>(null);

  const fetchOnce = useCallback(async () => {
    // Never overlap requests: a slow response must not queue a second poll.
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const since = fingerprint.current;
      const url =
        `${API_BASE}/pipeline/readiness?changed_only=1` +
        (since ? `&since=${encodeURIComponent(since)}` : '');
      let res: Response;
      try {
        res = await fetch(url);
      } catch (netErr) {
        // Connection refused / DNS / CORS / TLS. The browser deliberately
        // collapses these into one message and withholds the real cause, so we
        // report it as "unreachable" and do not guess beyond that.
        throw Object.assign(new Error(netErr instanceof Error ? netErr.message : 'network error'), {
          kind: 'unreachable' as const,
        });
      }
      if (!res.ok) {
        throw Object.assign(new Error(`HTTP ${res.status}`), { kind: 'http' as const });
      }
      let raw: unknown;
      try {
        raw = await res.json();
      } catch (parseErr) {
        throw Object.assign(
          new Error(parseErr instanceof Error ? parseErr.message : 'invalid JSON'),
          { kind: 'malformed' as const },
        );
      }
      const body = parseReadiness(raw);
      if (!mounted.current) return;
      if (body.fingerprint) fingerprint.current = body.fingerprint;
      failures.current = 0;
      setError(null);
      setErrorKind(null);
      lastSuccessRef.current = Date.now();
      if (body.changed && body.readiness) {
        // Only a genuinely changed verdict updates state — an unchanged poll is
        // a no-op for React, so the panel does not re-render.
        setReadiness(body.readiness);
        setLastUpdated(Date.now());
      }
      // `!body.changed` intentionally sets no state. The transport is alive, so
      // no error is shown and no re-render is caused.
    } catch (e) {
      if (mounted.current) {
        failures.current += 1;
        const kind = (e as { kind?: ReadinessErrorKind }).kind ?? 'unreachable';
        const detail = e instanceof Error ? e.message : 'network error';
        setError(describeReadinessError(kind, detail));
        setErrorKind(kind);
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

  // `lastSuccessRef` records transport liveness without re-rendering; the
  // rendered "Assessed at" time stays in `lastUpdated` state, which only moves
  // when the verdict actually changes.
  void lastSuccessRef;

  return { readiness, loading, error, errorKind, lastUpdated, refresh };
}
