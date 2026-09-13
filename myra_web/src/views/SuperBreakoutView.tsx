/**
 * Super Breakout(MSK) — scanner view.
 *
 * SMA-50 crossover with raw-delivery tie-break.  Two-phase exit lifecycle:
 * protective stop (close < SMA50) before activation, MA(50) trailing after.
 *
 * Regime caveat: validated on NSE large-caps, 2015-2026 (delivery-dependent
 * tie-break usable from Oct 2019); edge confirmed in both a 2015-2023
 * training period and a 2024-2026 out-of-sample holdout — not tested across
 * a sustained bear market or major sector rotation.
 */
import { useState, useEffect, useCallback } from 'react';
import SuperBreakoutNearTrigger from './SuperBreakoutNearTrigger';

const API_BASE = import.meta.env.VITE_API_BASE || '';

interface Candidate {
  symbol: string;
  phase: 'NEW' | 'PENDING' | 'TRAILING' | 'EXITED';
  entry_date?: string;
  entry_price?: number;
  current_price?: number;
  activation_target?: number;
  pnl_pct?: number;
  n_hold_days?: number;
  exit_date?: string;
  exit_price?: number;
  exit_reason?: string;
  delivery_value?: number;
  mcap_rank?: number;
  mcap_cr?: number;
  signal_date?: string;
  sma_50?: number;
  sma_200?: number;
  stop_level?: number;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  scanned_date: string | null;
}

const PHASE_COLORS: Record<string, string> = {
  NEW: 'text-green-400',
  PENDING: 'text-yellow-400',
  TRAILING: 'text-blue-400',
  EXITED: 'text-gray-500',
};

export default function SuperBreakoutView() {
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [scanning, setScanning] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/super-breakout/status`);
      if (res.ok) setStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  useEffect(() => {
    if (!scanning) return;
    const iv = setInterval(fetchStatus, 2000);
    return () => clearInterval(iv);
  }, [scanning, fetchStatus]);

  const triggerScan = async () => {
    setScanning(true);
    try {
      await fetch(`${API_BASE}/api/super-breakout/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
    } catch { /* ignore */ }
  };

  const candidates = status?.candidates || [];
  const active = candidates.filter(c => c.phase === 'PENDING' || c.phase === 'TRAILING');
  const newSignals = candidates.filter(c => c.phase === 'NEW');
  const exited = candidates.filter(c => c.phase === 'EXITED');

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-white">Super Breakout(MSK)</h1>
          <p className="text-xs text-gray-400 mt-1">
            SMA-50 crossover · raw delivery tie-break · MA(50) trailing exit
          </p>
        </div>
        <button
          onClick={triggerScan}
          disabled={scanning && status?.scan_status === 'scanning'}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 text-white rounded text-sm font-medium"
        >
          {scanning && status?.scan_status === 'scanning' ? 'Scanning…' : 'Scan'}
        </button>
      </div>

      {status?.scanned_date && (
        <p className="text-xs text-gray-500 mb-3">Last scan: {status.scanned_date}</p>
      )}

      <SuperBreakoutNearTrigger />

      {active.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-white mb-2">Active Positions</h2>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left py-1 px-2">Symbol</th>
                <th className="text-left py-1 px-2">Phase</th>
                <th className="text-right py-1 px-2">Entry</th>
                <th className="text-right py-1 px-2">Current</th>
                <th className="text-right py-1 px-2">Stop</th>
                <th className="text-right py-1 px-2">SMA(50)</th>
                <th className="text-right py-1 px-2">SMA(200)</th>
                <th className="text-right py-1 px-2">Target</th>
                <th className="text-right py-1 px-2">P&L%</th>
                <th className="text-right py-1 px-2">Days</th>
                <th className="text-right py-1 px-2">MCap Cr</th>
              </tr>
            </thead>
            <tbody>
              {active.map(c => (
                <tr key={c.symbol} className="border-b border-gray-800 hover:bg-gray-800/50">
                  <td className="py-1 px-2 text-white font-semibold">{c.symbol}</td>
                  <td className={`py-1 px-2 ${PHASE_COLORS[c.phase]}`}>{c.phase}</td>
                  <td className="py-1 px-2 text-right">{c.entry_price?.toFixed(2)}</td>
                  <td className="py-1 px-2 text-right">{c.current_price?.toFixed(2)}</td>
                  <td className={`py-1 px-2 text-right ${
                    c.current_price && c.stop_level
                      ? (c.current_price - c.stop_level) / c.stop_level < 0.02
                        ? 'text-red-400 font-bold'
                        : 'text-orange-400'
                      : 'text-gray-500'
                  }`}>{c.stop_level?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-400">{c.sma_50?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.sma_200?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.activation_target?.toFixed(2)}</td>
                  <td className={`py-1 px-2 text-right ${(c.pnl_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {c.pnl_pct != null ? `${c.pnl_pct >= 0 ? '+' : ''}${c.pnl_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-1 px-2 text-right text-gray-400">{c.n_hold_days ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.mcap_cr?.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {newSignals.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-white mb-2">New Signals</h2>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left py-1 px-2">Symbol</th>
                <th className="text-left py-1 px-2">Signal Date</th>
                <th className="text-right py-1 px-2">Entry Price</th>
                <th className="text-right py-1 px-2">SMA(50)</th>
                <th className="text-right py-1 px-2">SMA(200)</th>
                <th className="text-right py-1 px-2">Activation</th>
                <th className="text-right py-1 px-2">Delivery</th>
                <th className="text-right py-1 px-2">MCap Cr</th>
              </tr>
            </thead>
            <tbody>
              {newSignals.map(c => (
                <tr key={c.symbol} className="border-b border-gray-800 hover:bg-gray-800/50">
                  <td className="py-1 px-2 text-green-400 font-semibold">{c.symbol}</td>
                  <td className="py-1 px-2">{c.signal_date}</td>
                  <td className="py-1 px-2 text-right">{c.entry_price?.toFixed(2)}</td>
                  <td className="py-1 px-2 text-right text-gray-400">{c.sma_50?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.sma_200?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.activation_target?.toFixed(2)}</td>
                  <td className="py-1 px-2 text-right">{c.delivery_value?.toLocaleString()}</td>
                  <td className="py-1 px-2 text-right text-gray-500">{c.mcap_cr?.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {exited.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-gray-400 mb-2">Exited (gap replay)</h2>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left py-1 px-2">Symbol</th>
                <th className="text-left py-1 px-2">Entry</th>
                <th className="text-left py-1 px-2">Exit</th>
                <th className="text-left py-1 px-2">Reason</th>
                <th className="text-right py-1 px-2">P&L%</th>
                <th className="text-right py-1 px-2">Days</th>
              </tr>
            </thead>
            <tbody>
              {exited.map(c => (
                <tr key={c.symbol} className="border-b border-gray-800 text-gray-500">
                  <td className="py-1 px-2">{c.symbol}</td>
                  <td className="py-1 px-2">{c.entry_date}</td>
                  <td className="py-1 px-2">{c.exit_date}</td>
                  <td className="py-1 px-2">{c.exit_reason}</td>
                  <td className={`py-1 px-2 text-right ${(c.pnl_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {c.pnl_pct != null ? `${c.pnl_pct >= 0 ? '+' : ''}${c.pnl_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-1 px-2 text-right">{c.n_hold_days ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {candidates.length === 0 && status?.scan_status === 'completed' && (
        <p className="text-gray-500 text-sm">No candidates found. Run enrichment pipeline first if SMA columns are missing.</p>
      )}

      <p className="text-[10px] text-gray-600 mt-6">
        CAVEAT: validated on NSE large-caps, 2015-2026 (delivery-dependent tie-break usable from Oct 2019). Edge confirmed in a 2015-2023 training period and a 2024-2026 out-of-sample holdout. Not tested across a sustained bear market or major sector rotation.
      </p>
    </div>
  );
}
