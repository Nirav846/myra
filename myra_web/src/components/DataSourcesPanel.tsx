/**
 * DataSourcesPanel — collapsible management surface for the fundamentals data
 * sources, mounted on Mission Control between the task list and execution
 * history. Collapsed by default to keep the page scannable.
 *
 * Turns an operator switch into a persisted config change (backend
 * /api/sources) that every fetch path honours: the cost-aware resolver, the
 * DataFetcher fallback chain and the bulk syncs.
 */
import { useState, ReactElement } from 'react';
import {
  Database, RefreshCw, ChevronDown, ChevronUp, AlertTriangle,
} from 'lucide-react';
import { useDataSources } from '../hooks/useDataSources';
import DataSourceCard from './DataSourceCard';

export default function DataSourcesPanel() {
  const {
    sources, loading, error, busy, testResults, refresh, applyPatch, reset, runTest, clearError,
  } = useDataSources();

  // Collapsed by default — the panel is a control surface, not a dashboard.
  const [expanded, setExpanded] = useState(false);

  const enabled = sources.filter((s) => s.enabled).length;
  const disabled = sources.length - enabled;
  const unsupported = sources.filter((s) => !s.implemented).length;

  let summary: ReactElement;
  if (loading && sources.length === 0) {
    summary = <span className="text-[#888]">Loading…</span>;
  } else {
    summary = (
      <span className={disabled > 0 ? 'text-orange-300' : 'text-[#888]'}>
        {enabled} of {sources.length} enabled
        {disabled > 0 ? ` · ${disabled} off` : ''}
        {unsupported > 0 ? ` · ${unsupported} not wired` : ''}
      </span>
    );
  }

  const headerIcon: ReactElement =
    disabled > 0 ? (
      <AlertTriangle size={14} className="text-orange-400" aria-hidden="true" />
    ) : (
      <Database size={14} className="text-blue-400" aria-hidden="true" />
    );

  return (
    <section
      aria-label="Data sources"
      className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-3"
    >
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
        className="flex items-center justify-between gap-2 w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 rounded cursor-pointer"
        aria-expanded={expanded}
        aria-controls="data-sources-detail"
      >
        <div className="flex items-center gap-2 flex-wrap">
          {headerIcon}
          <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] font-mono">
            Data Sources
          </h3>
          <span className="text-[11px] font-mono">— {summary}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              refresh();
            }}
            className="text-[#888] hover:text-[#fafafa] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 rounded p-0.5"
            title="Refresh data sources"
            aria-label="Refresh data sources"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          </button>
          {expanded ? (
            <ChevronUp size={14} className="text-[#888]" aria-hidden="true" />
          ) : (
            <ChevronDown size={14} className="text-[#888]" aria-hidden="true" />
          )}
        </div>
      </div>

      {expanded && (
        <div id="data-sources-detail" className="flex flex-col gap-3 mt-1">
          {error && (
            <div className="flex items-start gap-2 text-[11px] font-mono text-red-300 bg-red-950/30 border border-red-500/30 rounded p-2">
              <span className="flex-1">{error}</span>
              <button
                type="button"
                onClick={clearError}
                className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-red-500/40 hover:bg-red-500/10 transition-colors"
                title="Dismiss"
              >
                Dismiss
              </button>
            </div>
          )}

          {(loading && sources.length === 0) ? (
            <p className="text-xs font-mono text-[#888]">Connecting to the backend…</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3" role="list" aria-label="Fundamentals data sources">
              {sources.map((source) => (
                <div key={source.name} role="listitem">
                  <DataSourceCard
                    source={source}
                    busy={busy[source.name] ?? null}
                    test={testResults[source.name]}
                    onToggle={(enabledFlag) => applyPatch(source.name, { enabled: enabledFlag })}
                    onSave={(patch) => applyPatch(source.name, patch)}
                    onReset={() => reset(source.name)}
                    onTest={() => runTest(source.name)}
                  />
                </div>
              ))}
            </div>
          )}

          <p className="text-[10px] font-mono text-[#555]">
            Changes are persisted in the metadata sidecar and take effect from the
            next fetch. Screener.in is the only ROE / ROCE source — disabling it
            leaves those metrics stale.
          </p>
        </div>
      )}
    </section>
  );
}
