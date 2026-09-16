/**
 * Indicator Registry
 *
 * Central registry for managing chart indicators with typed configuration.
 * Each indicator has a minimal config field for future extensibility.
 */

export interface IndicatorConfig {
  id: string;
  type: 'line' | 'area' | 'highlight' | 'shape';
  visible: boolean;
  color: string;
  zIndex: number;
  settings?: Record<string, any>; // Placeholder for future per-indicator settings
}

export interface IndicatorModule<TConfig extends IndicatorConfig = IndicatorConfig> {
  id: string;
  config: TConfig;
  render: (data: any[], config: TConfig) => any[]; // Returns Plotly traces
}

/**
 * Registry map for active indicators
 */
class IndicatorRegistryImpl {
  private indicators: Map<string, IndicatorModule> = new Map();

  /**
   * Register an indicator module
   */
  register<TConfig extends IndicatorConfig>(module: IndicatorModule<TConfig>): void {
    this.indicators.set(module.id, module as IndicatorModule);
  }

  /**
   * Unregister an indicator module
   */
  unregister(id: string): void {
    this.indicators.delete(id);
  }

  /**
   * Get an indicator module by ID
   */
  get<TConfig extends IndicatorConfig>(id: string): IndicatorModule<TConfig> | undefined {
    return this.indicators.get(id) as IndicatorModule<TConfig> | undefined;
  }

  /**
   * Get all registered indicators
   */
  getAll(): IndicatorModule[] {
    return Array.from(this.indicators.values());
  }

  /**
   * Check if an indicator is registered
   */
  has(id: string): boolean {
    return this.indicators.has(id);
  }

  /**
   * Update indicator visibility
   */
  setVisibility(id: string, visible: boolean): void {
    const indicator = this.indicators.get(id);
    if (indicator) {
      indicator.config.visible = visible;
    }
  }

  /**
   * Update indicator config
   */
  updateConfig<TConfig extends IndicatorConfig>(
    id: string,
    updates: Partial<TConfig>
  ): void {
    const indicator = this.indicators.get(id) as IndicatorModule<TConfig> | undefined;
    if (indicator) {
      indicator.config = { ...indicator.config, ...updates };
    }
  }

  /**
   * Render all visible indicators
   */
  renderAll(data: any[]): any[] {
    const traces: any[] = [];

    // Sort by zIndex to ensure correct rendering order
    const sortedIndicators = Array.from(this.indicators.values())
      .filter(ind => ind.config.visible)
      .sort((a, b) => a.config.zIndex - b.config.zIndex);

    for (const indicator of sortedIndicators) {
      const tracesToAdd = indicator.render(data, indicator.config);
      traces.push(...tracesToAdd);
    }

    return traces;
  }

  /**
   * Clear all registered indicators
   */
  clear(): void {
    this.indicators.clear();
  }
}

// Singleton instance
export const IndicatorRegistry = new IndicatorRegistryImpl();

export default IndicatorRegistry;
