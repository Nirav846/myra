/**
 * Chart Test Utilities
 * Helper functions for testing AdvancedChartV2 components
 */

import { CandleData, OHLCV } from '../types/chart';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * Generate consistent test data for visual regression tests
 */
export function generateTestCandleData(count: number = 100): CandleData[] {
  const data: CandleData[] = [];
  const baseDate = new Date('2024-01-01');
  let price = 100;

  for (let i = 0; i < count; i++) {
    const date = new Date(baseDate);
    date.setDate(date.getDate() + i);

    const volatility = 0.02 + Math.random() * 0.03;
    const change = price * volatility * (Math.random() - 0.5);
    const close = price + change;

    const high = Math.max(price, close) + Math.abs(change) * Math.random();
    const low = Math.min(price, close) - Math.abs(change) * Math.random();
    const open = price;

    const volume = Math.floor(1000000 + Math.random() * 5000000);
    const deliveryQty = Math.floor(volume * (0.3 + Math.random() * 0.5));

    data.push({
      date: date.toISOString().split('T')[0],
      time: date.getTime(),
      open,
      high,
      low,
      close,
      volume,
      deliveryQty,
      deliveryPct: (deliveryQty / volume) * 100,
    });

    price = close;
  }

  return data;
}

/**
 * Wait for chart to finish rendering all elements
 */
export async function waitForChartRender(container: HTMLElement) {
  await waitFor(() => {
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
  }, { timeout: 3000 });

  // Wait for candles to render
  await waitFor(() => {
    const candles = container.querySelectorAll('[data-testid="candle"]');
    expect(candles.length).toBeGreaterThan(0);
  }, { timeout: 3000 });
}

/**
 * Extract candle positions for visual comparison
 */
export function extractCandlePositions(container: HTMLElement) {
  const candles = container.querySelectorAll('[data-testid="candle"]');
  return Array.from(candles).map((candle) => {
    const rect = candle.getBoundingClientRect();
    return {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
    };
  });
}

/**
 * Compare two sets of candle positions within tolerance
 */
export function compareCandlePositions(
  positions1: ReturnType<typeof extractCandlePositions>,
  positions2: ReturnType<typeof extractCandlePositions>,
  tolerance: number = 1
): boolean {
  if (positions1.length !== positions2.length) {
    return false;
  }

  for (let i = 0; i < positions1.length; i++) {
    const p1 = positions1[i];
    const p2 = positions2[i];

    if (
      Math.abs(p1.x - p2.x) > tolerance ||
      Math.abs(p1.y - p2.y) > tolerance ||
      Math.abs(p1.width - p2.width) > tolerance ||
      Math.abs(p1.height - p2.height) > tolerance
    ) {
      return false;
    }
  }

  return true;
}

/**
 * Simulate chart interactions for testing
 */
export class ChartInteractionSimulator {
  private container: HTMLElement;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  async pan(distanceX: number, distanceY: number) {
    const svg = this.container.querySelector('svg');
    expect(svg).toBeInTheDocument();

    const user = userEvent.setup();
    await user.hover(svg!);
    await user.pointer([
      { keys: '[MouseLeft]', target: svg!, coords: { x: 100, y: 100 } },
      { coords: { x: 100 + distanceX, y: 100 + distanceY } },
      { keys: '[/MouseLeft]' },
    ]);
  }

  async zoom(direction: 'in' | 'out', factor: number = 1.2) {
    const svg = this.container.querySelector('svg');
    expect(svg).toBeInTheDocument();

    const user = userEvent.setup();
    await user.hover(svg!);

    // Simulate wheel event for zoom
    const wheelEvent = new WheelEvent('wheel', {
      deltaY: direction === 'in' ? -100 : 100,
      bubbles: true,
    });
    svg!.dispatchEvent(wheelEvent);
  }

  async clickCandle(index: number) {
    const candles = this.container.querySelectorAll('[data-testid="candle"]');
    if (candles[index]) {
      const user = userEvent.setup();
      await user.click(candles[index]);
    }
  }

  async hoverCandle(index: number) {
    const candles = this.container.querySelectorAll('[data-testid="candle"]');
    if (candles[index]) {
      const user = userEvent.setup();
      await user.hover(candles[index]);
    }
  }
}

/**
 * Performance measurement utilities
 */
export function measureRenderTime(callback: () => void): number {
  const start = performance.now();
  callback();
  const end = performance.now();
  return end - start;
}

export async function measureFrameRate(
  action: () => Promise<void>,
  duration: number = 1000
): Promise<number> {
  let frameCount = 0;
  let startTime: number | null = null;

  const measureFrame = (timestamp: number) => {
    if (startTime === null) {
      startTime = timestamp;
    }

    frameCount++;

    if (timestamp - startTime < duration) {
      requestAnimationFrame(measureFrame);
    }
  };

  requestAnimationFrame(measureFrame);
  await action();

  // Wait for duration to complete
  await new Promise((resolve) => setTimeout(resolve, duration));

  return (frameCount / duration) * 1000; // FPS
}

/**
 * Validate data integrity
 */
export function validateOHLCVData(data: OHLCV[]): string[] {
  const errors: string[] = [];

  for (let i = 0; i < data.length; i++) {
    const candle = data[i];

    // Check required fields
    if (!candle.date) errors.push(`Missing date at index ${i}`);
    if (typeof candle.open !== 'number') errors.push(`Invalid open at index ${i}`);
    if (typeof candle.high !== 'number') errors.push(`Invalid high at index ${i}`);
    if (typeof candle.low !== 'number') errors.push(`Invalid low at index ${i}`);
    if (typeof candle.close !== 'number') errors.push(`Invalid close at index ${i}`);
    if (typeof candle.volume !== 'number') errors.push(`Invalid volume at index ${i}`);

    // Check OHLC logic
    if (candle.high < candle.low) {
      errors.push(`High < Low at index ${i}`);
    }
    if (candle.high < candle.open || candle.high < candle.close) {
      errors.push(`High doesn't encompass O/C at index ${i}`);
    }
    if (candle.low > candle.open || candle.low > candle.close) {
      errors.push(`Low doesn't encompass O/C at index ${i}`);
    }

    // Check volume
    if (candle.volume < 0) {
      errors.push(`Negative volume at index ${i}`);
    }
  }

  return errors;
}

/**
 * Validate indicator calculations
 */
export function validateSMA(data: number[], period: number, result: number[]): string[] {
  const errors: string[] = [];

  if (result.length !== data.length) {
    errors.push(`SMA length mismatch: expected ${data.length}, got ${result.length}`);
  }

  for (let i = period - 1; i < data.length; i++) {
    const expected = data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period;
    const actual = result[i];

    if (Math.abs(expected - actual) > 0.01) {
      errors.push(`SMA mismatch at index ${i}: expected ${expected}, got ${actual}`);
    }
  }

  return errors;
}

export function validateVWAP(data: OHLCV[], result: number[]): string[] {
  const errors: string[] = [];
  let cumulativeTPV = 0;
  let cumulativeVolume = 0;

  for (let i = 0; i < data.length; i++) {
    const typicalPrice = (data[i].high + data[i].low + data[i].close) / 3;
    cumulativeTPV += typicalPrice * data[i].volume;
    cumulativeVolume += data[i].volume;

    const expected = cumulativeTPV / cumulativeVolume;
    const actual = result[i];

    if (Math.abs(expected - actual) > 0.01) {
      errors.push(`VWAP mismatch at index ${i}: expected ${expected}, got ${actual}`);
    }
  }

  return errors;
}
