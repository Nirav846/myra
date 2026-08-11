import { useState, useEffect, useRef } from 'react';
import { Calendar, XCircle } from 'lucide-react';
import { API_BASE } from '../config';
import { Tooltip } from './Tooltip';

interface Props {
  selectedDate: string;
  onSelect: (date: string) => void;
}

export function HistoricalScanDatePicker({ selectedDate, onSelect }: Props) {
  const [latestTradingDay, setLatestTradingDay] = useState('');
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/latest-trading-day`)
      .then(r => r.json())
      .then(d => setLatestTradingDay(d.date || ''))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleScan = () => {
    if (draft) onSelect(draft);
    setOpen(false);
  };

  const handleClear = () => {
    onSelect('');
    setDraft('');
    setOpen(false);
  };

  return (
    <div className="flex items-center gap-1.5 relative">
      {selectedDate && (
        <div className="flex items-center gap-1 px-2 py-1 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[12px] font-mono text-[#ccc]">
          <Calendar size={11} className="text-violet-400" aria-hidden="true" />
          <span>{selectedDate}</span>
          <button
            onClick={() => onSelect('')}
            className="text-[#888] hover:text-[#aaa] transition-colors focus-visible:outline-none rounded"
            aria-label="Clear scan date"
          >
            <XCircle size={11} aria-hidden="true" />
          </button>
        </div>
      )}

      <Tooltip content="Time-travel the scan to any past trading day. Weekend/holidays auto-adjust to the nearest previous trading day.">
        <button
          onClick={() => setOpen(o => !o)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 ${
            selectedDate
              ? 'bg-violet-500/15 border-violet-500/30 text-violet-300'
              : open
              ? 'bg-[#1a1c24] border-violet-500/40 text-violet-300'
              : 'bg-[#1a1c24] border-[#ffffff1a] text-[#888] hover:text-violet-300 hover:border-violet-500/30'
          }`}
          aria-label="Toggle historical scan date picker"
          aria-expanded={open}
        >
          <Calendar size={12} aria-hidden="true" />
          History
        </button>
      </Tooltip>

      {open && (
        <div
          ref={popoverRef}
          className="absolute right-0 top-full mt-2 z-50 bg-[#1a1c24] border border-[#ffffff1a] rounded-lg shadow-xl p-3 flex flex-col gap-2 min-w-[220px]"
        >
          <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">
            Scan as of date
          </div>
          <input
            type="date"
            max={latestTradingDay}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            className="bg-[#0e1117] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#ccc] font-mono focus:border-violet-500 outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 w-full cursor-pointer"
            aria-label="Select historical scan date"
          />
          <div className="flex gap-2">
            <button
              onClick={handleScan}
              disabled={!draft}
              className="flex-1 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 disabled:opacity-40 text-white rounded text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/50"
            >
              Scan
            </button>
            <button
              onClick={handleClear}
              className="px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-[12px] text-[#aaa] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
            >
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}