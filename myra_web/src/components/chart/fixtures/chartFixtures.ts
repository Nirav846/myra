/**
 * Chart Fixture Data Generator
 *
 * Provides consistent mock OHLCV+Delivery data for testing and visual comparison.
 * Generates realistic price action patterns (trending, ranging, volatile).
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface FixtureScenario {
  name: string;
  description: string;
  candles: Candle[];
}

/**
 * Generate realistic OHLCV data using geometric Brownian motion
 * with configurable drift and volatility.
 */
function generatePriceSeries(
  startPrice: number,
  days: number,
  drift: number = 0.0005,
  volatility: number = 0.02,
  seed: number = 42
): Candle[] {
  const candles: Candle[] = [];
  let price = startPrice;

  // Simple pseudo-random number generator for reproducibility
  let randomState = seed;
  const random = () => {
    randomState = (randomState * 1103515245 + 12345) & 0x7fffffff;
    return randomState / 0x7fffffff;
  };

  const startDate = new Date('2024-01-02');

  for (let i = 0; i < days; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);

    // Skip weekends
    if (date.getDay() === 0 || date.getDay() === 6) continue;

    const dateString = date.toISOString().split('T')[0];

    // Generate daily return
    const u1 = random();
    const u2 = random();
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const dailyReturn = drift + volatility * z;

    // Calculate OHLC
    const open = price;
    const close = price * (1 + dailyReturn);
    const high = Math.max(open, close) * (1 + Math.abs(z) * 0.3);
    const low = Math.min(open, close) * (1 - Math.abs(z) * 0.3);

    // Generate volume (higher on volatile days)
    const baseVolume = 1_000_000;
    const volume = Math.round(baseVolume * (1 + Math.abs(z) * 2) * (0.8 + random() * 0.4));

    // Generate delivery quantity (correlated with volume)
    const deliveryRatio = 0.3 + random() * 0.5; // 30-80%
    const delivery = Math.round(volume * deliveryRatio);

    // Generate trades count
    const trades = Math.round(volume / (1000 + random() * 500));

    // Calculate VWAP approximation
    const typicalPrice = (high + low + close) / 3;
    const vwap = typicalPrice * (0.995 + random() * 0.01);

    candles.push({
      date: dateString,
      open: Math.round(open * 100) / 100,
      high: Math.round(high * 100) / 100,
      low: Math.round(low * 100) / 100,
      close: Math.round(close * 100) / 100,
      volume,
      volume_final: volume,
      delivery,
      delivery_final: delivery,
      delivery_pct: Math.round(deliveryRatio * 100),
      trades,
      vwap: Math.round(vwap * 100) / 100,
    });

    price = close;
  }

  return candles;
}

/**
 * Trending market scenario (strong upward movement)
 */
export function createBullishTrendFixture(days: number = 120): FixtureScenario {
  const candles = generatePriceSeries(100, days, 0.002, 0.015);

  return {
    name: 'Bullish Trend',
    description: 'Strong upward trend with consistent higher highs and higher lows',
    candles,
  };
}

/**
 * Bearish trend scenario (strong downward movement)
 */
export function createBearishTrendFixture(days: number = 120): FixtureScenario {
  const candles = generatePriceSeries(200, days, -0.002, 0.018);

  return {
    name: 'Bearish Trend',
    description: 'Strong downward trend with consistent lower lows and lower highs',
    candles,
  };
}

/**
 * Ranging market scenario (sideways consolidation)
 */
export function createRangingFixture(days: number = 120): FixtureScenario {
  const candles = generatePriceSeries(150, days, 0.0, 0.012);

  return {
    name: 'Ranging Market',
    description: 'Sideways consolidation with no clear directional bias',
    candles,
  };
}

/**
 * Volatile breakout scenario (low vol → high vol expansion)
 */
export function createVolatileBreakoutFixture(days: number = 120): FixtureScenario {
  const firstHalf = generatePriceSeries(100, days / 2, 0.0005, 0.008);
  const lastPrice = firstHalf[firstHalf.length - 1].close;
  const secondHalf = generatePriceSeries(lastPrice, days / 2, 0.003, 0.035);

  const candles = [...firstHalf, ...secondHalf.slice(firstHalf.length)];

  return {
    name: 'Volatile Breakout',
    description: 'Low volatility consolidation followed by high volatility breakout',
    candles,
  };
}

/**
 * Get all available fixtures
 */
export function getAllFixtures(): FixtureScenario[] {
  return [
    createBullishTrendFixture(),
    createBearishTrendFixture(),
    createRangingFixture(),
    createVolatileBreakoutFixture(),
  ];
}

/**
 * Get a specific fixture by name
 */
export function getFixtureByName(name: string): FixtureScenario | null {
  const fixtures = getAllFixtures();
  return fixtures.find(f => f.name === name) || null;
}

/**
 * Default fixture for quick testing
 */
export const defaultFixture: FixtureScenario = createBullishTrendFixture(120);
