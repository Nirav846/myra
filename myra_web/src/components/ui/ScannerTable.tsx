import { ReactNode, useMemo } from 'react';
import { ArrowUpDown, ExternalLink, Star, Info } from 'lucide-react';

export interface TableColumn<T> {
  key: keyof T | null;
  label: string;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  className?: string;
  render?: (item: T, index: number) => ReactNode;
  headerClassName?: string;
  cellClassName?: string;
  conditionalFormat?: (value: any, item: T) => string;
}

export interface SortState<T> {
  sortKey: keyof T | null;
  sortAsc: boolean;
}

interface ScannerTableProps<T> {
  data: T[];
  columns: TableColumn<T>[];
  sortState: SortState<T>;
  onSort: (key: keyof T | null) => void;
  loading?: boolean;
  emptyMessage?: string;
  rowKey?: (item: T, index: number) => string;
  rowClassName?: (item: T, index: number) => string;
  enableHover?: boolean;
  enableStripes?: boolean;
  showQuickActions?: boolean;
  onQuickAction?: (action: string, item: T) => void;
}

export function ScannerTable<T extends Record<string, any>>({
  data,
  columns,
  sortState: { sortKey, sortAsc },
  onSort,
  loading = false,
  emptyMessage = 'No data available',
  rowKey,
  rowClassName,
  enableHover = true,
  enableStripes = true,
  showQuickActions = false,
  onQuickAction,
}: ScannerTableProps<T>) {
  const sortedData = useMemo(() => {
    if (!sortKey) return data;
    
    return [...data].sort((a, b) => {
      const va = a[sortKey] ?? -Infinity;
      const vb = b[sortKey] ?? -Infinity;
      
      if (typeof va === 'string' && typeof vb === 'string') {
        return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      
      return sortAsc ? va - vb : vb - va;
    });
  }, [data, sortKey, sortAsc]);

  const handleSort = (key: keyof T | null) => {
    onSort(key);
  };

  const renderSortIcon = (key: keyof T | null) => {
    if (!key || sortKey !== key) {
      return <ArrowUpDown size={10} className="text-text-tertiary ml-1" />;
    }
    return (
      <ArrowUpDown 
        size={10} 
        className={`ml-1 ${sortAsc ? 'text-success' : 'text-accent-indigo'}`} 
      />
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 gap-2 text-text-secondary">
        <div className="w-5 h-5 border-2 border-accent-indigo border-t-transparent rounded-full animate-spin" />
        Loading...
      </div>
    );
  }

  if (sortedData.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-text-tertiary flex-col gap-2">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto">
      <table className="table-modern w-full text-xs font-mono">
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={String(col.key ?? col.label)}
                className={`
                  px-3 py-3 cursor-pointer transition-colors
                  ${col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'}
                  ${col.headerClassName || ''}
                `}
                onClick={() => col.sortable !== false && handleSort(col.key)}
              >
                <span className={`flex items-center ${
                  col.align === 'right' ? 'justify-end' : col.align === 'center' ? 'justify-center' : 'justify-start'
                }`}>
                  {col.label}
                  {col.sortable !== false && renderSortIcon(col.key)}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedData.map((item, index) => {
            const key = rowKey ? rowKey(item, index) : String(index);
            const rowClass = rowClassName ? rowClassName(item, index) : '';
            const hoverClass = enableHover ? 'hover:bg-accent-indigo/8' : '';
            const stripeClass = enableStripes && index % 2 === 0 ? 'bg-white/2' : '';
            
            return (
              <tr
                key={key}
                className={`
                  border-b border-border-default
                  ${rowClass}
                  ${hoverClass}
                  ${stripeClass}
                  group
                `}
              >
                {columns.map((col) => {
                  const value = col.key ? item[col.key] : null;
                  const conditionalClass = col.conditionalFormat ? col.conditionalFormat(value, item) : '';
                  
                  return (
                    <td
                      key={`${key}-${String(col.key ?? col.label)}`}
                      className={`
                        px-3 py-2.5
                        ${col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'}
                        ${col.cellClassName || ''}
                        ${conditionalClass}
                        ${col.key === null ? '' : 'numeric'}
                      `}
                    >
                      {col.render 
                        ? col.render(item, index)
                        : col.key 
                          ? String(item[col.key] ?? '—')
                          : '—'
                      }
                    </td>
                  );
                })}
                {showQuickActions && (
                  <td className="px-3 py-2.5 text-right opacity-0 group-hover:opacity-100 transition-opacity">
                    <div className="flex items-center gap-1 justify-end">
                      <button
                        onClick={() => onQuickAction?.('chart', item)}
                        className="p-1 hover:bg-accent-indigo/20 rounded text-text-secondary hover:text-accent-indigo transition-colors"
                        title="View Chart"
                        aria-label={`View chart for ${item.symbol || 'item'}`}
                      >
                        <ExternalLink size={12} />
                      </button>
                      <button
                        onClick={() => onQuickAction?.('watchlist', item)}
                        className="p-1 hover:bg-yellow-500/20 rounded text-text-secondary hover:text-yellow-400 transition-colors"
                        title="Add to Watchlist"
                        aria-label={`Add ${item.symbol || 'item'} to watchlist`}
                      >
                        <Star size={12} />
                      </button>
                      <button
                        onClick={() => onQuickAction?.('info', item)}
                        className="p-1 hover:bg-blue-500/20 rounded text-text-secondary hover:text-blue-400 transition-colors"
                        title="More Info"
                        aria-label={`Show info for ${item.symbol || 'item'}`}
                      >
                        <Info size={12} />
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Helper components for common cell types
export function PositiveNegativeCell({ value, suffix = '' }: { value: number | null; suffix?: string }) {
  if (value === null || value === undefined) {
    return <span className="text-text-tertiary">—</span>;
  }
  
  const pct = Number(value) * 100;
  
  return (
    <span className={
      pct >= 50 ? 'text-success font-semibold' : 
      pct > 0 ? 'text-warning' : 
      'text-text-primary'
    }>
      {pct.toFixed(2)}{suffix}
    </span>
  );
}

export function ConditionalValue({ 
  value, 
  formatter,
  positiveThreshold,
  negativeThreshold,
  suffix = '' 
}: { 
  value: number | null | undefined; 
  formatter?: (v: number) => string;
  positiveThreshold?: number;
  negativeThreshold?: number;
  suffix?: string;
}) {
  if (value === null || value === undefined) {
    return <span className="text-text-tertiary">—</span>;
  }
  
  const numValue = Number(value);
  const displayValue = formatter ? formatter(numValue) : numValue.toString();
  
  let colorClass = 'text-text-primary';
  if (positiveThreshold !== undefined && numValue >= positiveThreshold) {
    colorClass = 'text-success font-semibold';
  } else if (negativeThreshold !== undefined && numValue <= negativeThreshold) {
    colorClass = 'text-error font-semibold';
  } else if (numValue > 0) {
    colorClass = 'text-warning';
  }
  
  return (
    <span className={colorClass}>
      {displayValue}{suffix}
    </span>
  );
}

export function PercentageCell({ 
  value, 
  showBar = false,
  positiveIsGood = true
}: { 
  value: number | null | undefined; 
  showBar?: boolean;
  positiveIsGood?: boolean;
}) {
  if (value === null || value === undefined) {
    return <span className="text-text-tertiary">—</span>;
  }
  
  const pct = Number(value);
  const isPositive = pct >= 0;
  const colorClass = isPositive 
    ? (positiveIsGood ? 'text-success' : 'text-error')
    : (positiveIsGood ? 'text-error' : 'text-success');
  
  return (
    <span className={`flex items-center gap-2 justify-end ${colorClass}`}>
      {showBar && (
        <span className="w-8 h-1 rounded-full bg-white/10 overflow-hidden shrink-0">
          <span 
            className={`block h-full rounded-full ${isPositive ? 'bg-success' : 'bg-error'}`} 
            style={{ width: `${Math.min(100, Math.abs(pct))}%` }} 
          />
        </span>
      )}
      <span className="font-mono">{pct.toFixed(2)}%</span>
    </span>
  );
}

export function SignalBadge({ signal, mapping }: { 
  signal: string | null; 
  mapping: Record<string, string>;
}) {
  if (!signal) {
    return <span className="text-text-disabled">—</span>;
  }
  
  const cls = mapping[signal] ?? 'text-text-tertiary bg-white/5 border-white/10';
  
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${cls}`}>
      {signal.replace(/_/g, ' ')}
    </span>
  );
}

export function MiniBarCell({ 
  value, 
  max = 100, 
  colorClass = 'bg-accent-indigo/60' 
}: { 
  value: number | null; 
  max?: number; 
  colorClass?: string;
}) {
  if (value === null || value === undefined) {
    return <span className="text-text-tertiary">—</span>;
  }
  
  const pct = Math.max(0, Math.min(100, (Number(value) / max) * 100));
  
  return (
    <span className="flex items-center gap-1.5 justify-end">
      <span className="w-10 h-1 rounded-full bg-white/10 overflow-hidden shrink-0">
        <span 
          className={`block h-full rounded-full ${colorClass}`} 
          style={{ width: `${pct}%` }} 
        />
      </span>
      <span className="text-text-primary">{pct.toFixed(0)}%</span>
    </span>
  );
}

export function FormatInt({ value }: { value: number | null | undefined }) {
  const DASH = '\u2014';
  if (value == null) return <span className="text-text-tertiary">{DASH}</span>;
  return <span>{Number(value).toLocaleString()}</span>;
}

export function FormatCurrency({ 
  value, 
  divisor = 1e7, 
  suffix = ' Cr',
  symbol = '₹'
}: { 
  value: number | null | undefined; 
  divisor?: number; 
  suffix?: string;
  symbol?: string;
}) {
  const DASH = '\u2014';
  if (value == null) return <span className="text-text-tertiary">{DASH}</span>;
  
  const cr = Number(value) / divisor;
  const formatted = cr >= 1e5 ? `${(cr / 1e3).toFixed(0)}K` : cr.toFixed(0);
  
  return <span className="whitespace-nowrap">{symbol}{formatted}{suffix}</span>;
}
