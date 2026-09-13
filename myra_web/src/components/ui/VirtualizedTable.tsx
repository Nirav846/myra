import { useMemo, useState, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowUpDown, ExternalLink, Star, Info } from 'lucide-react';

export interface TableColumn<T> {
  key: keyof T | null;
  label: string;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  className?: string;
  render?: (item: T, index: number) => React.ReactNode;
  headerClassName?: string;
  cellClassName?: string;
  conditionalFormat?: (value: any, item: T) => string;
}

export interface SortState<T> {
  sortKey: keyof T | null;
  sortAsc: boolean;
}

interface VirtualizedTableProps<T> {
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
  estimatedRowHeight?: number;
  overscan?: number;
  containerHeight?: number;
}

export function VirtualizedTable<T extends Record<string, any>>({
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
  estimatedRowHeight = 40,
  overscan = 5,
  containerHeight = 500,
}: VirtualizedTableProps<T>) {
  const [parentRef, setParentRef] = useState<HTMLDivElement | null>(null);

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

  const virtualizer = useVirtualizer({
    count: sortedData.length,
    getScrollElement: () => parentRef,
    estimateSize: () => estimatedRowHeight,
    overscan,
  });

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
    <div 
      ref={setParentRef}
      className="overflow-auto"
      style={{ height: containerHeight }}
      role="grid"
      aria-label="Scanner results table"
    >
      <div 
        className="relative w-full"
        style={{ 
          height: `${virtualizer.getTotalSize()}px`,
          minWidth: 'fit-content'
        }}
      >
        <table className="table-modern w-full text-xs font-mono">
          <thead className="sticky top-0 bg-gradient-to-r from-[#161b22] to-[#1c2128] z-10">
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
                  role="columnheader"
                  aria-sort={sortKey === col.key ? (sortAsc ? 'ascending' : 'descending') : 'none'}
                >
                  <span className={`flex items-center ${
                    col.align === 'right' ? 'justify-end' : col.align === 'center' ? 'justify-center' : 'justify-start'
                  }`}>
                    {col.label}
                    {col.sortable !== false && renderSortIcon(col.key)}
                  </span>
                </th>
              ))}
              {showQuickActions && <th className="px-3 py-3 text-right">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const item = sortedData[virtualRow.index];
              const index = virtualRow.index;
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
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  role="row"
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
                        role="gridcell"
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
    </div>
  );
}

// Re-export helper components from ScannerTable
export { 
  PositiveNegativeCell, 
  ConditionalValue, 
  PercentageCell, 
  SignalBadge, 
  MiniBarCell, 
  FormatInt, 
  FormatCurrency 
} from './ScannerTable';
