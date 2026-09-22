import { TrendingUp, TrendingDown, Minus, Zap } from 'lucide-react';

export type SignalBand = 'strong' | 'positive' | 'neutral' | 'negative';

export interface SignalBadgeProps {
  /** The score/signal value to render, e.g. "87.3". */
  value: string | number;
  /** Which tone to apply. Drives both color (token) and icon so it is never color-only. */
  band: SignalBand;
  /** Short descriptor, e.g. "Score" or "Del %". */
  label?: string;
  className?: string;
}

const BAND_META: Record<
  SignalBand,
  { textClass: string; icon: typeof TrendingUp; word: string }
> = {
  strong: { textClass: 'text-signal-strong', icon: Zap, word: 'strong' },
  positive: { textClass: 'text-signal-positive', icon: TrendingUp, word: 'positive' },
  neutral: { textClass: 'text-signal-neutral', icon: Minus, word: 'neutral' },
  negative: { textClass: 'text-signal-negative', icon: TrendingDown, word: 'negative' },
};

/**
 * SignalBadge - score/signal rendered with color AND text + icon, so the value
 * is never conveyed by color alone (a11y gap flagged in the audit).
 */
export function SignalBadge({
  value,
  band,
  label,
  className = '',
}: SignalBadgeProps) {
  const meta = BAND_META[band];
  const Icon = meta.icon;
  const ariaLabel = label ? `${label}: ${value} (${meta.word})` : `${value} (${meta.word})`;

  return (
    <span
      className={`inline-flex items-center justify-end gap-1 font-semibold ${meta.textClass} ${className}`}
      role="img"
      aria-label={ariaLabel}
    >
      <Icon size={12} aria-hidden="true" />
      {label && (
        <span className="text-[10px] uppercase tracking-wider opacity-80 font-mono">{label}</span>
      )}
      <span className="font-mono">{value}</span>
    </span>
  );
}

export default SignalBadge;