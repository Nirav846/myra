import { TrendingUp } from 'lucide-react';

interface FundTractionButtonProps {
  symbols: string[];
  disabled?: boolean;
  size?: string;
}

/**
 * Universal Fund Traction Report button.
 * Place this next to the Export CSV button in any scanner view.
 * Opens /fund-traction-report?symbols=... in a new tab.
 */
export default function FundTractionButton({ symbols, disabled }: FundTractionButtonProps) {
  const handleClick = () => {
    if (!symbols.length) return;
    const url = `/#/fund-traction-report?symbols=${symbols.join(',')}`;
    window.open(url, '_blank');
  };

  const isDisabled = disabled || symbols.length === 0;

  return (
    <button
      onClick={handleClick}
      disabled={isDisabled}
      className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      title={isDisabled ? "Select symbols to view Fund Traction Report" : `Open Fund Traction Report for ${symbols.length} symbols`}
      aria-label={isDisabled ? "Select symbols to view Fund Traction Report" : `Open Fund Traction Report for ${symbols.length} symbols`}
    >
      <TrendingUp size={12} aria-hidden="true" /> MF Report
    </button>
  );
}
