/**
 * Chart Comparison Dev View
 *
 * Temporary dev-only route for side-by-side visual comparison
 * of old AdvancedChart.tsx and new AdvancedChartV2.tsx.
 *
 * Purpose: Catch visual drift early during development.
 */

import { useState, useMemo } from 'react';
import { AdvancedChartV2 } from './AdvancedChartV2';
// Import the existing chart component (will be wrapped to match V2 interface)
import { defaultFixture, getAllFixtures, FixtureScenario } from '../components/chart/fixtures/chartFixtures';
import { Candle } from '../core/technical-analysis/types';

/**
 * Legacy chart wrapper - adapts old AdvancedChart to accept fixture data
 * Note: This is a simplified mock since the real AdvancedChart expects API data
 */
function LegacyChartWrapper({ data, debug }: { data: Candle[]; debug?: boolean }) {
  // For Phase 1 comparison, we'll use a placeholder that shows what the old chart would render
  // In practice, you'd pass this data to the actual AdvancedChart component

  return (
    <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
      {/* Header matching V2 style */}
      <div className="h-10 bg-[#2a2c34]/50 border-b border-[#ffffff1a] flex items-center px-4 justify-between">
        <div className="flex gap-2 items-center">
          <span className="font-semibold text-white">Legacy Chart</span>
          <span className="text-xs text-gray-400">{data.length} candles</span>
        </div>
        <div className="text-xs text-gray-500">
          {data[0]?.date} → {data[data.length - 1]?.date}
        </div>
      </div>

      {/* Placeholder - in real comparison, this would render AdvancedChart */}
      <div className="h-[440px] flex items-center justify-center text-gray-500">
        <div className="text-center">
          <div className="text-lg mb-2">📊 Legacy Chart Placeholder</div>
          <div className="text-sm">
            AdvancedChart.tsx would render here<br/>
            (requires full data fetch setup)
          </div>
          {debug && (
            <div className="mt-4 text-xs font-mono text-left bg-black/50 p-2 rounded">
              <div>Data points: {data.length}</div>
              <div>Date range: {data[0]?.date} - {data[data.length - 1]?.date}</div>
              <div>Price range: {Math.min(...data.map(d => d.low)).toFixed(2)} - {Math.max(...data.map(d => d.high)).toFixed(2)}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Visual comparison checklist item
 */
interface ChecklistItem {
  id: string;
  label: string;
  passed: boolean;
  notes?: string;
}

/**
 * Dev-only comparison view for visual regression testing
 */
export function ChartComparisonDev() {
  const [selectedFixture, setSelectedFixture] = useState<string>('Bullish Trend');
  const [debugMode, setDebugMode] = useState(true);
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});

  // Get current fixture data
  const fixture = useMemo(() => {
    const found = getAllFixtures().find(f => f.name === selectedFixture);
    return found || defaultFixture;
  }, [selectedFixture]);

  // Calculate visual metrics for comparison
  const metrics = useMemo(() => {
    const candles = fixture.candles;
    const prices = candles.flatMap(c => [c.open, c.high, c.low, c.close]);
    const volumes = candles.map(c => c.volume);

    return {
      candleCount: candles.length,
      dateRange: `${candles[0]?.date} → ${candles[candles.length - 1]?.date}`,
      priceRange: `${Math.min(...prices).toFixed(2)} - ${Math.max(...prices).toFixed(2)}`,
      avgVolume: Math.round(volumes.reduce((a, b) => a + b, 0) / volumes.length).toLocaleString(),
      avgDeliveryPct: Math.round(candles.reduce((sum, c) => sum + (c.delivery_pct || 0), 0) / candles.length),
    };
  }, [fixture]);

  // Toggle checklist item
  const toggleChecklist = (id: string) => {
    setChecklist(prev => ({ ...prev, [id]: !prev[id] }));
  };

  // Calculate pass rate
  const checklistItems: ChecklistItem[] = [
    { id: 'candle-width', label: 'Candle widths match', passed: !!checklist['candle-width'] },
    { id: 'candle-colors', label: 'Candle colors identical', passed: !!checklist['candle-colors'] },
    { id: 'axis-labels', label: 'X/Y axis labels aligned', passed: !!checklist['axis-labels'] },
    { id: 'gridlines', label: 'Gridline positions match', passed: !!checklist['gridlines'] },
    { id: 'hover-tooltip', label: 'Hover tooltip format correct', passed: !!checklist['hover-tooltip'] },
    { id: 'zoom-behavior', label: 'Zoom/pan behavior consistent', passed: !!checklist['zoom-behavior'] },
  ];

  const passCount = checklistItems.filter(i => i.passed).length;
  const totalPassRate = Math.round((passCount / checklistItems.length) * 100);

  return (
    <div className="min-h-screen bg-[#0f1115] text-white p-6">
      {/* Header */}
      <div className="max-w-[1800px] mx-auto mb-6">
        <h1 className="text-2xl font-bold mb-2">🔍 Chart Visual Comparison (Dev Only)</h1>
        <p className="text-gray-400 text-sm">
          Side-by-side comparison of legacy vs. new chart implementation. Use this to catch visual drift during Phase 1 development.
        </p>
      </div>

      {/* Controls */}
      <div className="max-w-[1800px] mx-auto mb-6 flex gap-4 items-center flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-400">Fixture:</label>
          <select
            value={selectedFixture}
            onChange={(e) => setSelectedFixture(e.target.value)}
            className="bg-[#2a2c34] border border-[#ffffff1a] rounded px-3 py-1.5 text-sm"
          >
            {getAllFixtures().map(f => (
              <option key={f.name} value={f.name}>{f.name}</option>
            ))}
          </select>
        </div>

        <button
          onClick={() => setDebugMode(!debugMode)}
          className={`px-3 py-1.5 rounded text-sm ${debugMode ? 'bg-green-600' : 'bg-[#2a2c34]'}`}
        >
          Debug: {debugMode ? 'ON' : 'OFF'}
        </button>

        <div className="ml-auto flex items-center gap-4 text-sm">
          <span className="text-gray-400">Visual Drift Check: <span className={totalPassRate >= 80 ? 'text-green-400' : 'text-yellow-400'}>{totalPassRate}%</span></span>
        </div>
      </div>

      {/* Metrics Bar */}
      <div className="max-w-[1800px] mx-auto mb-6 grid grid-cols-5 gap-4">
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
          <div className="text-xs text-gray-500">Candles</div>
          <div className="text-lg font-semibold">{metrics.candleCount}</div>
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
          <div className="text-xs text-gray-500">Date Range</div>
          <div className="text-sm font-semibold truncate">{metrics.dateRange}</div>
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
          <div className="text-xs text-gray-500">Price Range</div>
          <div className="text-sm font-semibold">{metrics.priceRange}</div>
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
          <div className="text-xs text-gray-500">Avg Volume</div>
          <div className="text-sm font-semibold">{metrics.avgVolume}</div>
        </div>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
          <div className="text-xs text-gray-500">Avg Delivery %</div>
          <div className="text-sm font-semibold">{metrics.avgDeliveryPct}%</div>
        </div>
      </div>

      {/* Side-by-Side Charts */}
      <div className="max-w-[1800px] mx-auto grid grid-cols-2 gap-6 mb-6">
        {/* Left: Legacy Chart */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="font-semibold text-gray-300">Legacy (AdvancedChart.tsx)</h3>
            <span className="text-xs text-gray-500">Reference</span>
          </div>
          <LegacyChartWrapper data={fixture.candles} debug={debugMode} />
        </div>

        {/* Right: New V2 Chart */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="font-semibold text-gray-300">New (AdvancedChartV2.tsx)</h3>
            <span className="text-xs text-green-500">Phase 1</span>
          </div>
          <AdvancedChartV2 fixture={fixture} debug={debugMode} />
        </div>
      </div>

      {/* Visual Drift Checklist */}
      <div className="max-w-[1800px] mx-auto mb-6">
        <h3 className="font-semibold text-gray-300 mb-3">✅ Visual Drift Checklist</h3>
        <div className="grid grid-cols-3 gap-3">
          {checklistItems.map(item => (
            <button
              key={item.id}
              onClick={() => toggleChecklist(item.id)}
              className={`p-3 rounded border text-left transition-all ${
                item.passed
                  ? 'bg-green-900/30 border-green-600 text-green-400'
                  : 'bg-[#1a1c24] border-[#ffffff1a] text-gray-400 hover:border-gray-500'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-lg">{item.passed ? '✓' : '○'}</span>
                <span className="text-sm">{item.label}</span>
              </div>
            </button>
          ))}
        </div>

        {passCount === checklistItems.length && (
          <div className="mt-4 p-3 bg-green-900/30 border border-green-600 rounded text-green-400 text-sm">
            ✓ All visual checks passed! Ready to proceed to Phase 2.
          </div>
        )}
      </div>

      {/* Fixture Description */}
      <div className="max-w-[1800px] mx-auto">
        <h3 className="font-semibold text-gray-300 mb-2">📋 Current Fixture</h3>
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
          <div className="text-sm text-gray-400">{fixture.description}</div>
        </div>
      </div>
    </div>
  );
}

export default ChartComparisonDev;
