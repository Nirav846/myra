import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { AlertTriangle, Info, RefreshCw, XCircle } from 'lucide-react';
import { API_BASE } from '../config';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';
import ScrollableTable from '../components/ScrollableTable';
import NearTriggerWatchlist from './NearTriggerWatchlist';
import MyPositionsWithAlerts from './MyPositionsWithAlerts';

// ── Validated defaults (mirror the scanner's backtest-validated config) ────
const VALIDATED_PROFIT_TARGETS = [5, 7.5, 10]; // % — all three were backtested
const VALIDATED_TOP_N = 500; // market-cap-ranked universe size that was backtested

interface Settings {
  profitTarget: number; // exit-target annotation %, applied as target_price column
  averaging: boolean;   // on = NEW + ADD (3-tranche cap); off = NEW only (BASE)
  topN: number;         // universe size sent to the backend
  sortBy: 'overshoot' | 'mcap' | 'close' | 'symbol';
  showDeliveryRef: boolean; // display-only column (reference, never ranking — D5)
}

const DEFAULT_SETTINGS: Settings = {
  profitTarget: 7.5,
  averaging: true,
  topN: VALIDATED_TOP_N,
  sortBy: 'overshoot',
  showDeliveryRef: false,
};

const STORAGE_KEY = 'recovery_ladder_settings_v1';

// Mirrors bottom_hunter_m1_scanner.VALIDATION_CAVEAT — kept here as a display
// string so the page always shows it without depending on the backend.
const REGIME_CAVEAT =
  'Validated edge is specific to the 2024-2026 holdout (large-cap dip-recovery regime). NOT validated across a genuine bear market or a small-cap-led regime. Not all-weather validated.';

interface Candidate {
  symbol: string;
  signal_date: string;
  signal_type: 'NEW' | 'ADD';
  at_tranche_cap: boolean;
  close: number;
  year_low: number;
  recovery_line: number;
  overshoot_pct: number;
  mcap_rank: number;
  mcap_cr: number;
  delivery_pct: number | null;
  n_tranches: number | null;
  blended_basis: number | null;
  last_tranche_date: string | null;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  scanned_date?: string | null;
}

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) };
  } catch {
    /* corrupted local settings — fall back to defaults */
  }
  return DEFAULT_SETTINGS;
}

function fmtCr(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;
}

const VALIDATED_PT_SET = new Set(VALIDATED_PROFIT_TARGETS);

export default function RecoveryLadder() {
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [scanDate, setScanDate] = useState('');
  const mountedRef = useRef(true);
  const pollTimerRef = useRef<number | null>(null);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter-m1/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setStatus(data);
        setError(null);
        // The status loop is owned entirely by fetchScanStatus: it keeps
        // polling while the backend reports 'scanning' and self-clears when
        // the scan completes or errors. Nothing else creates intervals, so
        // there are no orphaned or perpetual timers.
        if (data.scan_status === 'completed' || data.scan_status === 'error') {
          clearPolling();
          setIsScanning(false);
        } else if (data.scan_status === 'scanning' && !pollTimerRef.current) {
          pollTimerRef.current = window.setInterval(fetchScanStatus, 2000);
          setIsScanning(true);
        }
      }
    } catch (e: unknown) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : 'Error connecting to backend');
      }
    }
  }, [clearPolling]);

  const startScan = useCallback(async () => {
    if (!mountedRef.current) return;
    setIsScanning(true);
    setError(null);
    clearPolling();
    try {
      const res = await fetch(`${API_BASE}/bottom-hunter-m1/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          top_n: settings.topN,
          ...(scanDate.trim() ? { scan_date: scanDate.trim() } : {}),
        }),
      });
      if (!mountedRef.current) return;
      if (res.ok) {
        // POST /scan returns after the backend has marked the state as
        // 'scanning'. Poll the status once, then fetchScanStatus's own loop
        // takes over until completion/error — no extra interval here.
        await fetchScanStatus();
      } else {
        const err = (await res.json().catch(() => ({ detail: 'Failed to start scan' }))) as { detail?: string };
        setError(err.detail || 'Failed to start scan');
        setIsScanning(false);
      }
    } catch (e: unknown) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : 'Error connecting to backend');
        setIsScanning(false);
      }
    }
  }, [fetchScanStatus, clearPolling, scanDate, settings.topN]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* storage unavailable — settings stay session-local */
    }
  }, [settings]);

  const profitTargetUntested = !VALIDATED_PT_SET.has(settings.profitTarget);
  const topNUntested = settings.topN !== VALIDATED_TOP_N;
  const nonValidated = profitTargetUntested || topNUntested;

  // Averaging OFF is a validated alternative (BASE-only backtest), so it does
  // NOT flag the results — it only changes which rows are shown.
  const visibleCandidates = useMemo(() => {
    let rows = (status?.candidates ?? []).slice();
    if (!settings.averaging) rows = rows.filter((r) => r.signal_type === 'NEW');
    const key = settings.sortBy;
    rows.sort((a, b) => {
      if (key === 'overshoot') return a.overshoot_pct - b.overshoot_pct;
      if (key === 'mcap') return b.mcap_cr - a.mcap_cr;
      if (key === 'close') return b.close - a.close;
      return a.symbol.localeCompare(b.symbol);
    });
    return rows;
  }, [status, settings]);

  const targetCol = `Target ${settings.profitTarget}%`;
  // Days since signal for triggered-status view.
  const enrichedCandidates = useMemo(() => {
    return visibleCandidates.map((c) => {
      let daysSinceSignal = 0;
      if (c.signal_date) {
        try {
          const sigDt = new Date(c.signal_date);
          const now = new Date();
          daysSinceSignal = Math.floor((now.getTime() - sigDt.getTime()) / 86400000);
        } catch { /* ignore */ }
      }
      // Distance to profit target from current price.
      const targetPrice = c.blended_basis
        ? c.blended_basis * (1 + settings.profitTarget / 100)
        : c.close * (1 + settings.profitTarget / 100);
      const pctToTarget = ((c.close / (c.blended_basis || c.close)) - 1) * 100;
      return { ...c, daysSinceSignal, targetPrice, pctToTarget };
    });
  }, [visibleCandidates, settings.profitTarget]);
  // The backend serves idle + cached candidates from the persisted cache —
  // results must render whenever candidates exist, regardless of scan status.
  const noResultsYet =
    !status || (status.scan_status === 'idle' && (status.candidates?.length ?? 0) === 0);

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Recovery Ladder">
      {/* Persistent regime caveat — not a tooltip, always visible */}
      <div className="bg-blue-500/10 border border-blue-500/30 rounded px-4 py-3 flex items-start gap-2" role="note">
        <Info size={16} className="text-blue-400 shrink-0 mt-0.5" aria-hidden="true" />
        <div className="text-xs font-mono text-blue-300/90">
          <p className="mb-1">{REGIME_CAVEAT}</p>
          <p>
            Suggests entries when a stock recovering from a 52-week low reclaims
            1.2× its rolling year-low. NOT position advice.
          </p>
        </div>
      </div>

      {/* Persistent warning when scanning outside validated territory */}
      {nonValidated && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-start gap-2" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0 mt-0.5" aria-hidden="true" />
          <span className="text-xs font-mono text-amber-300/90">
            Non-default strategy settings in effect — the current configuration has
            {' '}NOT been validated by the backtest (see Strategy Parameters below).
          </span>
        </div>
      )}

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-violet-500/20 p-2 rounded" aria-hidden="true">
            <span className="text-xl leading-none" role="img" aria-label="Ladder">🪜</span>
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Recovery Ladder</h1>
            <p className="text-xs font-mono text-[#888]">52-Week-Low Breakout Recovery</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning…</>
            ) : (
              <>Scan</>
            )}
          </button>
          <button
            onClick={() => {
              fetch(`${API_BASE}/cache/bottom-hunter-m1`, { method: 'DELETE' })
                .then(() => fetchScanStatus())
                .catch(() => {});
            }}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {/* Settings — two clearly separated sections */}
      <section className="grid md:grid-cols-2 gap-4" aria-label="Recovery Ladder settings">
        {/* Scan Options — safe to change freely */}
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col gap-3">
          <h2 className="text-[12px] font-mono font-semibold uppercase tracking-wider text-violet-300">
            Scan Options
          </h2>
          <p className="text-[11px] font-mono text-[#666] -mt-2">
            Display and time-travel controls — safe to change freely.
          </p>
          <div className="flex flex-col gap-1">
            <span className="text-[11px] font-mono text-[#aaa]">Scan date (blank = latest trading day)</span>
            <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-mono text-[#aaa]">Sort results by</span>
            <select
              value={settings.sortBy}
              onChange={(e) => setSettings((s) => ({ ...s, sortBy: e.target.value as Settings['sortBy'] }))}
              className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
            >
              <option value="overshoot">Overshoot (lowest first — matches backtest ranking)</option>
              <option value="mcap">Market cap (largest first)</option>
              <option value="close">Close price (highest first)</option>
              <option value="symbol">Symbol (A → Z)</option>
            </select>
          </label>
          <label className="flex items-center justify-between gap-2 cursor-pointer">
            <span className="text-[11px] font-mono text-[#aaa]">Show delivery reference column</span>
            <input
              type="checkbox"
              checked={settings.showDeliveryRef}
              onChange={(e) => setSettings((s) => ({ ...s, showDeliveryRef: e.target.checked }))}
              className="accent-violet-500 w-4 h-4"
            />
          </label>
        </div>

        {/* Strategy Parameters — validated defaults, warnings when deviating */}
        <div className="bg-[#1a1c24] border border-amber-500/20 rounded p-4 flex flex-col gap-3">
          <h2 className="text-[12px] font-mono font-semibold uppercase tracking-wider text-amber-300">
            Strategy Parameters
          </h2>
          <p className="text-[11px] font-mono text-[#666] -mt-2">
            Validated defaults only — deviating means leaving tested territory.
          </p>

          <div className="flex flex-col gap-1">
            <span className="text-[11px] font-mono text-[#aaa]">Profit target (%)</span>
            <div className="flex items-center gap-1.5">
              {VALIDATED_PROFIT_TARGETS.map((pt) => (
                <button
                  key={pt}
                  onClick={() => setSettings((s) => ({ ...s, profitTarget: pt }))}
                  className={`px-2.5 py-1 rounded border text-[12px] font-mono transition-colors ${
                    settings.profitTarget === pt
                      ? 'bg-violet-500/20 border-violet-500/40 text-violet-300'
                      : 'bg-[#0e1117] border-[#ffffff1a] text-[#888] hover:text-violet-300 hover:border-violet-500/30'
                  }`}
                >
                  {pt}%
                </button>
              ))}
              <input
                type="number"
                min={0.5}
                max={100}
                step={0.1}
                value={settings.profitTarget}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setSettings((s) => ({ ...s, profitTarget: Number.isNaN(v) ? s.profitTarget : v }));
                }}
                className="w-20 bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
                aria-label="Custom profit target percent"
              />
            </div>
            {profitTargetUntested && (
              <p className="text-[11px] font-mono text-amber-400" role="alert">
                ⚠ Untested value — only {VALIDATED_PROFIT_TARGETS.join('%, ')}% were validated in the backtest.
              </p>
            )}
          </div>

          <label className="flex items-center justify-between gap-2 cursor-pointer">
            <span className="text-[11px] font-mono text-[#aaa]">Averaging (3-tranche cap)</span>
            <input
              type="checkbox"
              checked={settings.averaging}
              onChange={(e) => setSettings((s) => ({ ...s, averaging: e.target.checked }))}
              className="accent-violet-500 w-4 h-4"
            />
          </label>
          <p className="text-[11px] font-mono text-[#666] -mt-2">
            {settings.averaging
              ? 'On — multi-tranche add-ons allowed (validated avg-in, capped at 3).'
              : 'Off — single-entry only, matching the BASE-only backtest results.'}
          </p>

          <div className="flex flex-col gap-1">
            <span className="text-[11px] font-mono text-[#aaa]">Universe size (top N by market cap)</span>
            <input
              type="number"
              min={50}
              max={3000}
              step={1}
              value={settings.topN}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                setSettings((s) => ({ ...s, topN: Number.isNaN(v) ? s.topN : v }));
              }}
              className="w-32 bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
              aria-label="Universe size top N"
            />
            {topNUntested && (
              <p className="text-[11px] font-mono text-amber-400" role="alert">
                ⚠ Untested universe size — only the top {VALIDATED_TOP_N} (market-cap ranked) was validated.
              </p>
            )}
          </div>
        </div>
      </section>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-red-400 shrink-0" aria-hidden="true" />
          <span className="text-red-300/90">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto text-red-500/50 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50 rounded" aria-label="Dismiss error">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Results */}
      <section className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden flex flex-col min-h-0" aria-label="Recovery Ladder results">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#ffffff1a] flex-wrap">
          <span className="text-xs font-mono text-[#ccc]">
            {isScanning ? `Scanning… ${Math.round(status?.progress ?? 0)}%` : `${enrichedCandidates.length} candidate${enrichedCandidates.length === 1 ? '' : 's'}`}
          </span>
          {status?.scanned_date && (
            <span className="text-[11px] font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded px-2 py-0.5">
              as-of {status.scanned_date}
            </span>
          )}
          {!settings.averaging && (
            <span className="text-[11px] font-mono text-[#888]">
              Single-entry mode (NEW signals only) — BASE validated
            </span>
          )}
          {nonValidated && (
            <span className="ml-auto text-[11px] font-mono text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-2 py-0.5" role="alert">
              ⚠ Results with non-default settings — not validated by backtest
            </span>
          )}
        </div>

        {noResultsYet ? (
          <div className="px-4 py-10 text-center text-xs font-mono text-[#666]">
            {status?.message || 'Idle — click Scan to start.'}
          </div>
) : isScanning ? (
          <div className="px-4 py-10 text-center text-xs font-mono text-[#666]">
            Scanning… {Math.round(status?.progress ?? 0)}% — rechecking shortly.
          </div>
        ) : !settings.averaging && enrichedCandidates.length === 0 && (status?.candidates?.length ?? 0) > 0 ? (
          <div className="px-4 py-10 text-center text-xs font-mono text-[#666]">
            Every signal on {status?.scanned_date || 'the scan date'} is an ADD (tranche add-on) — the scan
            {' '}did find {status?.candidates?.length} candidate{status?.candidates?.length === 1 ? '' : 's'}, but
            {' '}single-entry mode shows NEW signals only. Switch Averaging ON to view them.
          </div>
        ) : enrichedCandidates.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs font-mono text-[#666]">
            No crossings on {status?.scanned_date || 'the scan date'} — check back after the next trading day.
          </div>
        ) : (
          <ScrollableTable>
            <table className="w-full text-left text-[12px] font-mono whitespace-nowrap">
              <thead>
                <tr className="border-b border-[#ffffff1a] text-[#888] uppercase text-[10px] tracking-wider">
                  <th className="px-3 py-2">Symbol</th>
                  <th className="px-3 py-2">Signal</th>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2">Days Since</th>
                  <th className="px-3 py-2">Close</th>
                  <th className="px-3 py-2">{targetCol}</th>
                  <th className="px-3 py-2">To Target</th>
                  <th className="px-3 py-2">Year Low</th>
                  <th className="px-3 py-2">Recovery Line</th>
                  <th className="px-3 py-2">Overshoot %</th>
                  <th className="px-3 py-2">Mkt Cap ₹</th>
                  <th className="px-3 py-2">Rank</th>
                  {settings.showDeliveryRef && <th className="px-3 py-2">Delivery %</th>}
                  <th className="px-3 py-2">Blended Basis</th>
                  <th className="px-3 py-2">Last Tranche</th>
                  <th className="px-3 py-2">Tranches</th>
                </tr>
              </thead>
              <tbody>
              {enrichedCandidates.map((c) => (
                  <tr key={c.symbol} className="border-b border-[#ffffff0d] hover:bg-[#ffffff08]">
                    <td className="px-3 py-2 text-[#fafafa] font-semibold">{c.symbol}</td>
                    <td className="px-3 py-2">
                      {c.signal_type === 'NEW' ? (
                        <span className="bg-green-500/15 border border-green-500/30 text-green-400 rounded px-1.5 py-0.5 text-[10px]">NEW</span>
                      ) : (
                        <span className="bg-amber-500/15 border border-amber-500/30 text-amber-400 rounded px-1.5 py-0.5 text-[10px]">
                          ADD{c.at_tranche_cap ? ' ·cap' : ''}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-[#ccc]">{c.signal_date}</td>
                    <td className="px-3 py-2 text-[#ccc]">{c.daysSinceSignal}d</td>
                    <td className="px-3 py-2 text-[#fafafa]">₹ {c.close.toFixed(2)}</td>
                    <td className="px-3 py-2 text-violet-300">
                      ₹ {c.targetPrice.toFixed(2)}
                    </td>
                    <td className="px-3 py-2">
                      {c.blended_basis != null ? (
                        <span className={c.pctToTarget >= settings.profitTarget ? 'text-green-400' : 'text-[#ccc]'}>
                          {c.pctToTarget >= 0 ? '+' : ''}{c.pctToTarget.toFixed(1)}%
                        </span>
                      ) : '—'}
                    </td>
                    <td className="px-3 py-2 text-[#ccc]">₹ {c.year_low.toFixed(2)}</td>
                    <td className="px-3 py-2 text-[#ccc]">₹ {c.recovery_line.toFixed(2)}</td>
                    <td className="px-3 py-2 text-[#fafafa]">{c.overshoot_pct.toFixed(2)}%</td>
                    <td className="px-3 py-2 text-[#ccc]">{fmtCr(c.mcap_cr)}</td>
                    <td className="px-3 py-2 text-[#666]">#{c.mcap_rank}</td>
                    {settings.showDeliveryRef && (
                      <td className="px-3 py-2 text-[#888]">{c.delivery_pct != null ? `${c.delivery_pct}%` : '—'}</td>
                    )}
                    <td className="px-3 py-2 text-[#888]">
                      {c.blended_basis != null ? `₹ ${c.blended_basis.toFixed(2)}` : '—'}
                    </td>
                    <td className="px-3 py-2 text-[#888]">{c.last_tranche_date ?? '—'}</td>
                    <td className="px-3 py-2 text-[#888]">
                      {c.signal_type === 'ADD' && c.n_tranches != null
                        ? `${c.n_tranches}/3`
                        : c.signal_type === 'ADD'
                        ? '—'
                        : '1st'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollableTable>
        )}
      </section>

      {/* Near-Trigger Watchlist */}
      <NearTriggerWatchlist scannedDate={status?.scanned_date ?? null} />

      {/* My Positions with Alerts */}
      <MyPositionsWithAlerts />
    </main>
  );
}
