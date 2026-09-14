import { CandleData } from '../../types';

export interface OrderBlock {
  type: 'bullish' | 'bearish';
  open: number;
  high: number;
  low: number;
  close: number;
  index: number;
  mitigated: boolean;
  mitigationIndex?: number;
}

export interface OrderBlockResult {
  bullishBlocks: OrderBlock[];
  bearishBlocks: OrderBlock[];
}

/**
 * Detects Order Blocks - institutional accumulation/distribution zones
 * Bullish OB: Last down candle before strong upward move
 * Bearish OB: Last up candle before strong downward move
 */
export function detectOrderBlocks(data: CandleData[], threshold: number = 1.5): OrderBlockResult {
  const bullishBlocks: OrderBlock[] = [];
  const bearishBlocks: OrderBlock[] = [];

  if (data.length < 3) {
    return { bullishBlocks, bearishBlocks };
  }

  for (let i = 1; i < data.length - 1; i++) {
    const prevCandle = data[i - 1];
    const currentCandle = data[i];
    const nextCandle = data[i + 1];

    // Calculate body sizes
    const prevBody = Math.abs(prevCandle.close - prevCandle.open);
    const currBody = Math.abs(currentCandle.close - currentCandle.open);
    const nextBody = Math.abs(nextCandle.close - nextCandle.open);

    // Average body size for context
    const avgBody = (prevBody + currBody + nextBody) / 3;

    // Bullish Order Block: Down candle followed by strong upward move
    if (currentCandle.close < currentCandle.open) { // Current is bearish
      const moveUp = nextCandle.close - currentCandle.low;
      const moveStrength = moveUp / avgBody;

      if (moveStrength >= threshold && nextCandle.close > nextCandle.open) {
        // Check if this is the last down candle before the move
        const isLastDownBeforeMove = prevCandle.close >= prevCandle.open;
        
        if (isLastDownBeforeMove || i === 1) {
          const block: OrderBlock = {
            type: 'bullish',
            open: currentCandle.open,
            high: currentCandle.high,
            low: currentCandle.low,
            close: currentCandle.close,
            index: i,
            mitigated: false,
          };

          // Check for mitigation (price returned to OB zone)
          for (let j = i + 1; j < data.length; j++) {
            if (data[j].low <= block.high && data[j].high >= block.low) {
              block.mitigated = true;
              block.mitigationIndex = j;
              break;
            }
          }

          bullishBlocks.push(block);
        }
      }
    }

    // Bearish Order Block: Up candle followed by strong downward move
    if (currentCandle.close > currentCandle.open) { // Current is bullish
      const moveDown = currentCandle.high - nextCandle.close;
      const moveStrength = moveDown / avgBody;

      if (moveStrength >= threshold && nextCandle.close < nextCandle.open) {
        // Check if this is the last up candle before the move
        const isLastUpBeforeMove = prevCandle.close <= prevCandle.open;
        
        if (isLastUpBeforeMove || i === 1) {
          const block: OrderBlock = {
            type: 'bearish',
            open: currentCandle.open,
            high: currentCandle.high,
            low: currentCandle.low,
            close: currentCandle.close,
            index: i,
            mitigated: false,
          };

          // Check for mitigation (price returned to OB zone)
          for (let j = i + 1; j < data.length; j++) {
            if (data[j].high >= block.low && data[j].low <= block.high) {
              block.mitigated = true;
              block.mitigationIndex = j;
              break;
            }
          }

          bearishBlocks.push(block);
        }
      }
    }
  }

  // Limit to most recent 5 blocks of each type to avoid clutter
  return {
    bullishBlocks: bullishBlocks.slice(-5),
    bearishBlocks: bearishBlocks.slice(-5),
  };
}
