# Indicator Visual Enhancements - Implementation Plan

## Overview
Systematic enhancement of chart indicator visuals for better clarity, aesthetics, and user experience.

---

## Phase V1: Core Line & Zone Improvements

### V1.1 - SMA/EMA Lines Enhancement
- [ ] Add dynamic line thickness based on timeframe (1px for <1h, 2px for daily+, 1.5px otherwise)
- [ ] Implement subtle glow effect using `shadowcolor` and `shadowblur` in Plotly
- [ ] Add gradient fills between price and MA lines (optional toggle)
- [ ] Improve label positioning with background boxes for better readability
- [ ] Color consistency: Use distinct but harmonious palette (blue, orange, green, purple, pink)
- **Priority**: High
- **Estimated Impact**: Medium visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/sma.ts`, `ema.ts`

### V1.2 - Swing Highs/Lows Markers
- [ ] Replace simple markers with triangular icons (▲ for swing high, ▼ for swing low)
- [ ] Add animation on appearance (fade-in + slight scale)
- [ ] Display price label with background box
- [ ] Color-code by strength (deeper color for stronger swings)
- [ ] Add tooltip showing swing date, price, and strength percentage
- **Priority**: High
- **Estimated Impact**: High visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/swings.ts`

### V1.3 - Fair Value Gaps (FVG) Zones
- [ ] Add gradient background fill (transparent to semi-transparent)
- [ ] Improve border styling (dashed for unmitigated, solid for mitigated)
- [ ] Visual state change when FVG is mitigated (opacity reduction + strikethrough)
- [ ] Add mitigation marker (small checkmark or X)
- [ ] Display FVG size in pips/percentage on hover
- **Priority**: High
- **Estimated Impact**: High visual improvement, medium performance cost
- **Files**: `myra_web/src/components/chart/indicators/fvg.ts`

### V1.4 - Liquidity Voids
- [ ] Enhanced zone visualization with gradient fills
- [ ] Better boundary lines (dotted style)
- [ ] Label showing void size and type (bullish/bearish)
- [ ] Color differentiation: Bullish voids (green tint), Bearish voids (red tint)
- **Priority**: Medium
- **Estimated Impact**: Medium visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/liquidityVoids.ts`

---

## Phase V2: Oscillator & Volume Enhancements

### V2.1 - RSI Indicator Panel
- [ ] Add overbought (70+) and oversold (30-) zone backgrounds
- [ ] Highlight centerline (50) with distinct color
- [ ] Color transition: Red when >70, Green when <30, Gray otherwise
- [ ] Add divergence markers (bullish/bearish divergence detection)
- [ ] Smooth line rendering with proper anti-aliasing
- **Priority**: High
- **Estimated Impact**: High visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/rsi.ts`

### V2.2 - Volume & Delivery OBV
- [ ] Gradient color for volume bars (green for up candles, red for down)
- [ ] Delivery OBV line with smooth curve interpolation
- [ ] Add moving average overlay on OBV (optional)
- [ ] Background grid improvements for better readability
- **Priority**: Medium
- **Estimated Impact**: Medium visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/deliveryObv.ts`

### V2.3 - VWAP Bands
- [ ] Gradient fill between upper and lower bands
- [ ] Center VWAP line with distinct styling (bold, different color)
- [ ] Band opacity based on distance from VWAP (closer = more opaque)
- [ ] Add standard deviation labels (±1σ, ±2σ, ±3σ)
- **Priority**: Medium
- **Estimated Impact**: Medium visual improvement, medium performance cost
- **Files**: `myra_web/src/components/chart/indicators/vwap.ts`

---

## Phase V3: Smart Money & Advanced Indicators

### V3.1 - Smart Money Prints (SMP)
- [ ] Enhanced marker icons (custom SVG for different SMP types)
- [ ] Animation on formation (pulse effect)
- [ ] Tooltip with detailed information (type, time, price, confirmation status)
- [ ] Color coding: Bullish SMP (green), Bearish SMP (red)
- **Priority**: Medium
- **Estimated Impact**: High visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/smp.ts`

### V3.2 - ATR Trailing Stop
- [ ] Dynamic line that changes color when trend changes
- [ ] Dot markers at reversal points
- [ ] Fill area between price and ATR line (semi-transparent)
- [ ] Label showing current ATR value and stop price
- **Priority**: Low
- **Estimated Impact**: Medium visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/atr.ts`

### V3.3 - DI+ / DI- Lines
- [ ] Distinct colors for DI+ (green) and DI- (red)
- [ ] ADX line overlay (optional, different style)
- [ ] Crossover markers with labels
- [ ] Background zones for trend strength (weak/moderate/strong)
- **Priority**: Low
- **Estimated Impact**: Medium visual improvement, low performance cost
- **Files**: `myra_web/src/components/chart/indicators/di.ts`

---

## Phase V4: Global Enhancements & UX

### V4.1 - Unified Legend System
- [ ] Floating legend box showing all active indicators
- [ ] Click-to-toggle visibility from legend
- [ ] Drag-to-reposition legend
- [ ] Auto-hide after 3 seconds of inactivity
- **Priority**: Medium
- **Estimated Impact**: High UX improvement, low performance cost
- **Files**: `myra_web/src/components/chart/ChartLegend.tsx` (new)

### V4.2 - Indicator Settings Panel
- [ ] Inline parameter editing (click indicator → edit settings)
- [ ] Preset templates (Conservative, Aggressive, Balanced)
- [ ] Save custom presets per user
- [ ] Reset to defaults button
- **Priority**: Medium
- **Estimated Impact**: High UX improvement, medium development effort
- **Files**: `myra_web/src/components/chart/IndicatorSettings.tsx` (new)

### V4.3 - Responsive Design
- [ ] Adjust indicator density based on chart size
- [ ] Hide labels on small screens, show on hover only
- [ ] Touch-friendly marker sizes for mobile
- [ ] Optimize for different aspect ratios
- **Priority**: High
- **Estimated Impact**: High UX improvement, medium development effort
- **Files**: Multiple indicator files

### V4.4 - Performance Optimizations
- [ ] Lazy render indicators outside viewport
- [ ] Cache rendered shapes for static indicators
- [ ] Throttle updates during rapid zoom/pan
- [ ] Reduce precision for distant data points
- **Priority**: High
- **Estimated Impact**: High performance improvement, medium development effort
- **Files**: Multiple indicator files

---

## Implementation Guidelines

### General Principles
1. **Non-breaking changes**: All enhancements must be backward compatible
2. **Performance first**: No visual enhancement should degrade performance
3. **User control**: All new features should be togglable via settings
4. **Consistency**: Maintain consistent design language across all indicators
5. **Accessibility**: Ensure color choices are colorblind-friendly

### Technical Standards
- Use Plotly's native styling where possible
- Implement CSS variables for theme colors
- Add comprehensive TypeScript types
- Include unit tests for visual regression
- Document all new parameters

### Testing Checklist
- [ ] Visual regression testing across browsers
- [ ] Performance benchmarking (FPS during interactions)
- [ ] Mobile responsiveness testing
- [ ] Dark/Light theme compatibility
- [ ] Accessibility audit (WCAG 2.1)

---

## Priority Matrix

| Priority | Task ID | Description | Effort | Impact |
|----------|---------|-------------|--------|--------|
| 🔴 High | V1.1 | SMA/EMA Enhancement | Low | Medium |
| 🔴 High | V1.2 | Swing Markers | Low | High |
| 🔴 High | V1.3 | FVG Zones | Medium | High |
| 🔴 High | V2.1 | RSI Panel | Low | High |
| 🔴 High | V4.3 | Responsive Design | Medium | High |
| 🟡 Medium | V1.4 | Liquidity Voids | Low | Medium |
| 🟡 Medium | V2.2 | Volume/OBV | Low | Medium |
| 🟡 Medium | V2.3 | VWAP Bands | Medium | Medium |
| 🟡 Medium | V3.1 | Smart Money Prints | Low | High |
| 🟡 Medium | V4.1 | Legend System | Medium | High |
| 🟡 Medium | V4.2 | Settings Panel | High | High |
| 🟢 Low | V3.2 | ATR Trailing Stop | Low | Medium |
| 🟢 Low | V3.3 | DI+/DI- Lines | Low | Medium |
| 🟢 Low | V4.4 | Performance Opt | High | High |

---

## Next Steps

1. **Review & Prioritize**: Confirm which tasks to implement first
2. **Setup**: Create feature branch `feature/indicator-visuals`
3. **Implement V1**: Start with highest priority items (V1.1, V1.2, V1.3)
4. **Test**: Visual regression testing after each implementation
5. **Deploy**: Roll out incrementally with feature flags if needed

---

## Notes

- All color values should use CSS variables for theming support
- Consider adding a "Performance Mode" that disables expensive visuals
- Document breaking changes in migration guide
- Gather user feedback after V1 deployment to adjust priorities
