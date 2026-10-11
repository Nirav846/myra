/**
 * DataSourceCard — one row in the Data Sources panel.
 *
 * Shows what a source is, its live health, the enabled switch (with a warning
 * when disabling it is risky), the tuning controls (rate, TTL, timeout,
 * priority), a reset-to-defaults action and a one-shot live test.
 */
import { useEffect, useState, ReactElement } from 'react';
import {
  CheckCircle2, XCircle, AlertTriangle, Loader2, RotateCcw, FlaskConical,
} from 'lucide-react';
import type { DataSource, SourceTestResult, SourceTuning } from '../lib/sources';

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const diff = Date.now() - d.getTime();
  if (diff < 0) return 'just now';
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const TIER_BADGE: Record<string, string> = {
  primary: 'text-blue-300 border-blue-500/40 bg-blue-500/10',
  fallback: 'text-[#aaa] border-[#ffffff22] bg-white/5',
  bulk: 'text-purple-300 border-purple-500/40 bg-purple-500/10',
};

const COST_BADGE: Record<string, string> = {
  fast: 'text-green-400 border-green-500/40 bg-green-500/10',
  medium: 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10',
  slow: 'text-orange-400 border-orange-500/40 bg-orange-500/10',
};

function healthElement(source: DataSource): ReactElement {
  const h = source.health;
  if (!h) {
    return (
      <span className="flex items-center gap-1 text-[#777]" title="No health snapshot available">
        <AlertTriangle size={12} aria-hidden="true" />
        no health yet
      </span>
    );
  }
  if (h.cooling_down) {
    return (
      <span className="flex items-center gap-1 text-orange-300" title={`Cooling down for ${h.cooldown_remaining_s}s`}>
        <AlertTriangle size={12} aria-hidden="true" />
        Cooling down ({Math.ceil(h.cooldown_remaining_s / 60)}m)
      </span>
    );
  }
  if (h.fail_streak > 0 || h.empty_streak > 0) {
    return (
      <span className="flex items-center gap-1 text-yellow-400">
        <XCircle size={12} aria-hidden="true" />
        {h.fail_streak} fail{h.fail_streak === 1 ? '' : 's'} · last ok {timeAgo(h.last_ok)}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-green-400">
      <CheckCircle2 size={12} aria-hidden="true" />
      OK{h.last_ok ? ` · ${timeAgo(h.last_ok)}` : ''}
    </span>
  );
}

function testElement(result?: SourceTestResult): ReactElement | null {
  if (!result) return null;
  if (result.ok) {
    const keys = Object.keys(result.values ?? {});
    const values = keys.length
      ? ` · ${keys.slice(0, 4).map((k) => `${k}=${result.values?.[k]}`).join(', ')}${keys.length > 4 ? '…' : ''}`
      : '';
    return (
      <span className="flex items-center gap-1 text-green-400">
        <CheckCircle2 size={12} aria-hidden="true" />
        {result.source} answered in {result.elapsed_s?.toFixed(1)}s{values}
      </span>
    );
  }
  const reason = result.detail || result.error || 'no data';
  return (
    <span className="flex items-center gap-1 text-red-400">
      <XCircle size={12} aria-hidden="true" />
      {reason}
    </span>
  );
}

interface Props {
  source: DataSource;
  busy: string | null;
  test?: { loading: boolean; result?: SourceTestResult };
  onToggle: (enabled: boolean) => void;
  onSave: (patch: SourceTuning) => void;
  onReset: () => void;
  onTest: () => void;
}

export default function DataSourceCard({ source, busy, test, onToggle, onSave, onReset, onTest }: Props) {
  const [draft, setDraft] = useState({
    rate_per_sec: String(source.rate_per_sec),
    ttl_s: String(source.ttl_s),
    timeout_s: String(source.timeout_s),
    priority: String(source.priority),
  });

  // Re-sync the inputs whenever the server's values change (Apply success or
  // Reset), so a reset visibly snaps the fields back to defaults.
  useEffect(() => {
    setDraft({
      rate_per_sec: String(source.rate_per_sec),
      ttl_s: String(source.ttl_s),
      timeout_s: String(source.timeout_s),
      priority: String(source.priority),
    });
  }, [source.rate_per_sec, source.ttl_s, source.timeout_s, source.priority]);

  const saves = busy === 'save';
  const toggling = busy === 'save' && source.enabled !== undefined;

  const handleToggle = (enabled: boolean) => {
    // Surface the risk *before* flipping a risky source off.
    if (!enabled && source.disable_warning && !window.confirm(source.disable_warning)) {
      return;
    }
    onToggle(enabled);
  };

  const handleSave = () => {
    const patch: SourceTuning = {};
    const rate = parseFloat(draft.rate_per_sec);
    const ttl = parseInt(draft.ttl_s, 10);
    const timeout = parseFloat(draft.timeout_s);
    const priority = parseInt(draft.priority, 10);
    if (!isNaN(rate) && rate !== source.rate_per_sec) patch.rate_per_sec = rate;
    if (!isNaN(ttl) && ttl !== source.ttl_s) patch.ttl_s = ttl;
    if (!isNaN(timeout) && timeout !== source.timeout_s) patch.timeout_s = timeout;
    if (!isNaN(priority) && priority !== source.priority) patch.priority = priority;
    onSave(patch);
  };

  const dirty =
    draft.rate_per_sec !== String(source.rate_per_sec) ||
    draft.ttl_s !== String(source.ttl_s) ||
    draft.timeout_s !== String(source.timeout_s) ||
    draft.priority !== String(source.priority);

  const numberField = (key: 'rate_per_sec' | 'ttl_s' | 'timeout_s' | 'priority', label: string, title: string) => (
    <label className="flex flex-col gap-0.5 text-[10px] font-mono uppercase tracking-wider text-[#777]" title={title}>
      {label}
      <input
        type="number"
        value={draft[key]}
        onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
        step={key === 'ttl_s' || key === 'priority' ? 1 : 'any'}
        min={0}
        disabled={saves}
        className="w-20 bg-[#12141a] border border-[#ffffff1f] rounded px-1.5 py-0.5 text-[11px] font-mono text-[#ddd] focus:outline-none focus:border-yellow-500/50"
      />
    </label>
  );

  return (
    <article
      data-source={source.name}
      className="flex flex-col gap-2 bg-[#14161d] border border-[#ffffff14] rounded-lg p-3"
    >
      {/* Header: label + badges + enabled switch */}
      <div className="flex items-center gap-2 flex-wrap">
        <h4 className="text-xs font-mono text-[#fafafa] font-semibold">{source.label}</h4>
        <code className="text-[10px] font-mono text-[#666]">{source.name}</code>
        <span className={`text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border ${TIER_BADGE[source.tier] ?? TIER_BADGE.fallback}`}>
          {source.tier === 'bulk' ? 'bulk' : source.tier}
        </span>
        <span className={`text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border ${COST_BADGE[source.cost] ?? COST_BADGE.medium}`}>
          {source.cost}
        </span>
        {!source.implemented && (
          <span className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border border-[#ffffff22] text-[#888]">
            not wired up
          </span>
        )}
        <div className="flex-1" />
        <label className="flex items-center gap-1.5 text-[10px] font-mono text-[#888] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={source.enabled}
            disabled={!source.implemented || toggling || saves}
            onChange={(e) => handleToggle(e.target.checked)}
            className="accent-yellow-500"
            title={
              !source.implemented
                ? 'Not wired up yet — this switch has no effect'
                : source.enabled
                  ? 'Disable this source'
                  : 'Enable this source'
            }
          />
          {source.enabled ? 'On' : 'Off'}
        </label>
      </div>

      <p className="text-[11px] font-mono leading-relaxed text-[#999]">{source.description}</p>

      {/* Health + test */}
      <div className="flex items-center gap-3 flex-wrap text-[11px] font-mono">
        {healthElement(source)}
        {testElement(test?.result)}
        {test?.loading && (
          <span className="flex items-center gap-1 text-blue-300">
            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            testing…
          </span>
        )}
      </div>

      {/* Tuning */}
      <div className="flex items-end gap-3 flex-wrap">
        {numberField('rate_per_sec', 'req/s', 'Requests per second — how often this source is called')}
        {numberField('ttl_s', 'TTL (s)', 'Cache lifetime in seconds')}
        {numberField('timeout_s', 'timeout (s)', 'Per-request timeout in seconds')}
        {numberField('priority', 'priority', 'Lower is tried first within a cost tier')}
        <div className="flex items-center gap-1.5 ml-auto">
          <button
            type="button"
            disabled={saves || !dirty}
            onClick={handleSave}
            className={`flex items-center gap-1 text-[10px] font-mono px-2 py-1 rounded border transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
              dirty
                ? 'border-yellow-500/40 text-yellow-300 hover:bg-yellow-500/10'
                : 'border-[#ffffff1a] text-[#666]'
            }`}
          >
            {saves ? <Loader2 size={11} className="animate-spin" aria-hidden="true" /> : null}
            Apply
          </button>
          <button
            type="button"
            disabled={busy === 'reset'}
            onClick={onReset}
            className="flex items-center gap-1 text-[10px] font-mono px-2 py-1 rounded border border-[#ffffff1a] text-[#888] hover:text-[#fafafa] hover:bg-white/5 transition-colors disabled:opacity-40"
            title="Reset this source to its defaults"
          >
            {busy === 'reset' ? <Loader2 size={11} className="animate-spin" aria-hidden="true" /> : <RotateCcw size={11} aria-hidden="true" />}
            Reset
          </button>
          <button
            type="button"
            disabled={busy === 'test'}
            onClick={onTest}
            className="flex items-center gap-1 text-[10px] font-mono px-2 py-1 rounded border border-[#ffffff1a] text-[#aaa] hover:text-white hover:bg-white/5 transition-colors disabled:opacity-40"
            title="Fetch RELIANCE once and report"
          >
            {busy === 'test' ? <Loader2 size={11} className="animate-spin" aria-hidden="true" /> : <FlaskConical size={11} aria-hidden="true" />}
            Test
          </button>
        </div>
      </div>

      {!source.enabled && source.disable_warning && (
        <p className="text-[10px] font-mono text-orange-300/90 bg-orange-950/20 border border-orange-500/20 rounded px-2 py-1">
          {source.disable_warning}
        </p>
      )}
    </article>
  );
}
