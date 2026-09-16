/**
 * AdvancedChartV2 wired to real data via useChartData hook.
 * Converts CandleData[] from the API to FixtureScenario format for V2 rendering.
 */

import { useMemo } from 'react';
import { useChartData } from '../hooks/useChartData';
import { AdvancedChartV2 } from './AdvancedChartV2';
import type { FixtureScenario } from '../components/chart/fixtures/chartFixtures';
import type { ChartRange } from '../lib/SettingsContext';

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

export function AdvancedChartV2WithData({ symbol, range, debug = false }: Props) {
  const initialRange = useMemo(() => rangeToDateParams(range), [range]);

  const { data, loading, error } = useChartData(symbol, {
    enabled: true,
    enableDecimation: true,
    maxPoints: 2000,
    initialRange,
  });

  const fixture: FixtureScenario = useMemo(() => ({
    name: symbol,
    description: `${symbol} — ${range} range, ${data?.length ?? 0} candles`,
    candles: data ?? [],
  }), [symbol, range, data]);

  if (loading && !data) {
    return (
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex items-center justify-center h-[500px]">
        <div className="text-gray-400 text-sm">Loading {symbol}…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-[#1a1c24] border border-red-500/30 rounded flex items-center justify-center h-[500px]">
        <div className="text-red-400 text-sm">Error: {error}</div>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded flex items-center justify-center h-[500px]">
        <div className="text-gray-400 text-sm">No data for {symbol}</div>
      </div>
    );
  }

  return <AdvancedChartV2 fixture={fixture} debug={debug} />;
}
