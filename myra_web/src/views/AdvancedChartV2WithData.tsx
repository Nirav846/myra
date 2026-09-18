/**
 * AdvancedChartV2 wired to real data via useChartData hook.
 * Self-contained: includes symbol search and range selector.
 * Initial values come from globalSelectedTicker/settings.defaultChartRange,
 * but once on the page the user can change them independently.
 */

import { useState, useMemo } from 'react';
import { useChartData } from '../hooks/useChartData';
import { AdvancedChartV2 } from './AdvancedChartV2';
import { SymbolAutocomplete } from '../components/SymbolAutocomplete';
import { Button, ButtonGroup } from '../components/ui/Button';
import type { FixtureScenario } from '../components/chart/fixtures/chartFixtures';
import type { ChartRange } from '../lib/SettingsContext';

const RANGE_OPTIONS: ChartRange[] = ['1M', '3M', '6M', '1Y', 'All'];

function rangeToDateParams(range: ChartRange): { from_date?: string; to_date?: string } {
  const end = new Date();
  let start: Date;
  switch (range) {
    case '1M': start = new Date(); start.setMonth(start.getMonth() - 1); break;
    case '3M': start = new Date(); start.setMonth(start.getMonth() - 3); break;
    case '6M': start = new Date(); start.setMonth(start.getMonth() - 6); break;
    case '1Y': start = new Date(); start.setFullYear(start.getFullYear() - 1); break;
    case 'All': start = new Date('2015-01-01'); break;
    default: start = new Date(); start.setMonth(start.getMonth() - 1);
  }
  return {
    from_date: start.toISOString().split('T')[0],
    to_date: end.toISOString().split('T')[0],
  };
}

interface Props {
  symbol: string;
  range: ChartRange;
  debug?: boolean;
}

export function AdvancedChartV2WithData({ symbol: initialSymbol, range: initialRange, debug = false }: Props) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [range, setRange] = useState<ChartRange>(initialRange);

  const dateParams = useMemo(() => rangeToDateParams(range), [range]);

  const { data, loading, error } = useChartData(symbol, {
    enabled: true,
    enableDecimation: true,
    maxPoints: 2000,
    initialRange: dateParams,
  });

  const fixture: FixtureScenario = useMemo(() => ({
    name: symbol,
    description: `${symbol} — ${range} range, ${data?.length ?? 0} candles`,
    candles: data ?? [],
  }), [symbol, range, data]);

  return (
    <div className="flex flex-col h-full">
      {/* Controls bar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-[#ffffff1a] bg-[#0e1117] shrink-0">
        <SymbolAutocomplete
          value={symbol}
          onSelect={(sym) => { if (sym) setSymbol(sym); }}
          placeholder="Search symbol..."
          className="w-48"
        />
        <ButtonGroup orientation="horizontal">
          {RANGE_OPTIONS.map(r => (
            <Button
              key={r}
              onClick={() => setRange(r)}
              variant={range === r ? 'primary' : 'ghost'}
              size="sm"
              className={`font-mono text-[11px] ${range === r ? '' : 'text-[#888] hover:text-white'}`}
            >
              {r}
            </Button>
          ))}
        </ButtonGroup>
      </div>

      {/* Chart area */}
      <div className="flex-1 min-h-0">
        {loading && !data ? (
          <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex items-center justify-center h-[500px]">
            <div className="text-gray-400 text-sm">Loading {symbol}…</div>
          </div>
        ) : error ? (
          <div className="bg-[#1a1c24] border border-red-500/30 rounded flex items-center justify-center h-[500px]">
            <div className="text-red-400 text-sm">Error: {error}</div>
          </div>
        ) : !data || data.length === 0 ? (
          <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex items-center justify-center h-[500px]">
            <div className="text-gray-400 text-sm">No data for {symbol}</div>
          </div>
        ) : (
          <AdvancedChartV2 fixture={fixture} debug={debug} />
        )}
      </div>
    </div>
  );
}
