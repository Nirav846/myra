import { TrendingUp } from 'lucide-react';

interface FundTractionButtonProps {
  symbols: string[];
  disabled?: boolean;
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

  return (
    <button
      onClick={handleClick}
      disabled={disabled || symbols.length === 0}
      className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40"
      title={`Open Fund Traction Report for ${symbols.length} symbols`}
    >
      <TrendingUp size={12} /> MF Report
    </button>
  );
}
