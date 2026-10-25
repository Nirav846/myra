import { useState } from 'react';
import { Info, Loader2, Zap, ShieldAlert, Square } from 'lucide-react';
import { useDataQuery } from '../hooks/useDataQuery';
import type { AdmissionGate } from '../lib/pipeline';

const API_BASE = import.meta.env.VITE_API_BASE || '';

export default function SchedulerGateIndicator() {
  const [expanded, setExpanded] = useState(false);

  const gateQ = useDataQuery<AdmissionGate>(
    'pipeline_gate',
    async () => {
      const res = await fetch(`${API_BASE}/api/pipeline/gate`);
      if (!res.ok) throw new Error(`gate HTTP ${res.status}`);
      return res.json();
    },
    { ttlMs: 30_000 }
  );

  const gate = gateQ.data;
  const fetchFailed = !gateQ.loading && !!gateQ.error && gateQ.data === null;

  // Nothing to show if the query hasn't loaded yet.
  if (gateQ.loading) return null;

  // Fetch error — backend up but gate endpoint broken.
  if (fetchFailed) {
    return (
      <div
        className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-300"
        title="Admission gate fetch failed"
      >
        <Info size={10} className="shrink-0" aria-hidden="true" />
        <span>Admission state unavailable</span>
      </div>
    );
  }

  // No data (null body) — treat as unavailable.
  if (!gate) {
    return (
      <div
        className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-300"
        title="Admission gate returned null"
      >
        <Info size={10} className="shrink-0" aria-hidden="true" />
        <span>Admission state unavailable</span>
      </div>
    );
  }

  // Scan owns the machine → blue chip, click to expand.
  if (gate.scan_active) {
    const scanners = gate.active_scanners.join(', ') || 'unknown';
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 px-2.5 py-1.5 text-xs text-blue-300 hover:bg-blue-500/20"
          title={`Heavy tasks paused: ${scanners}`}
        >
          <Zap size={10} className="shrink-0 animate-pulse" aria-hidden="true" />
          <span>Scan running — heavy jobs paused</span>
          <Square
            size={8}
            className={`shrink-0 ${expanded ? 'rotate-90' : ''}`}
            aria-hidden="true"
          />
        </button>
        {expanded && (
          <div className="absolute mt-1 ml-0 z-20 rounded border border-blue-500/30 bg-slate-950/95 p-2 text-[10px] text-blue-200 min-w-[200px]">
            <div className="font-mono mb-1">{scanners}</div>
            <div className="text-blue-400/70">
              All heavy background tasks defer while any interactive scan runs.
            </div>
          </div>
        )}
      </div>
    );
  }

  // Deferred tasks waiting on resources/slots → amber chip.
  const deferred = gate.deferred || {};
  const deferredCount = Object.keys(deferred).length;
  if (deferredCount > 0) {
    const reasons = Object.entries(deferred).map(
      ([name, d]) => `${name}: ${d.reason}`
    );
    return (
      <div className="flex items-center gap-2">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-300 hover:bg-amber-500/20"
          title={reasons.join('\n')}
        >
          <ShieldAlert size={10} className="shrink-0" aria-hidden="true" />
          <span>{deferredCount} task(s) waiting</span>
          <Square
            size={8}
            className={`shrink-0 ${expanded ? 'rotate-90' : ''}`}
            aria-hidden="true"
          />
        </button>
        {expanded && (
          <div className="absolute mt-1 ml-0 z-20 rounded border border-amber-500/30 bg-slate-950/95 p-2 text-[10px] text-amber-200 min-w-[260px]">
            {reasons.map((r) => (
              <div key={r} className="font-mono mb-0.5">{r}</div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Idle → muted green chip with slot count.
  return (
    <div
      className="flex items-center gap-1.5 rounded-lg border border-green-500/30 bg-green-500/10 px-2.5 py-1.5 text-xs text-green-400"
      title={`${gate.concurrency_limit} execution slot(s); ${gate.resource_keys.length} serialized resources`}
    >
      <Square size={8} className="shrink-0 fill-green-400" aria-hidden="true" />
      <span>Idle — {gate.concurrency_limit} slot(s) free</span>
    </div>
  );
}