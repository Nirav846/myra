import { ReactNode, ButtonHTMLAttributes, CSSProperties } from 'react';
import { Loader2 } from 'lucide-react';

/**
 * Button Component - Modern, accessible button with multiple variants
 * Front-end only update - No backend changes required
 * 
 * @param variant - Visual style (primary, secondary, outline, ghost, danger)
 * @param size - Button size (sm, md, lg)
 * @param loading - Show loading spinner
 * @param leftIcon - Icon to display before text
 * @param rightIcon - Icon to display after text
 * @param fullWidth - Make button span full width
 */
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  fullWidth?: boolean;
}

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  loading = false,
  leftIcon,
  rightIcon,
  fullWidth = false,
  disabled,
  className = '',
  style,
  ...props
}: ButtonProps) {
  const baseStyles = `
    relative inline-flex items-center justify-center font-medium rounded-lg
    transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 
    focus-visible:ring-indigo-500/50 focus-visible:ring-offset-2 
    focus-visible:ring-offset-[#0e1117] disabled:opacity-50 
    disabled:cursor-not-allowed disabled:pointer-events-none
  `;
  
  const variants = {
    primary: 'bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white shadow-lg shadow-indigo-500/25 border border-indigo-400/30',
    secondary: 'bg-[#2d333b] hover:bg-[#373f47] text-[#f0f6fc] border border-[rgba(148,163,184,0.2)]',
    outline: 'bg-transparent hover:bg-[rgba(148,163,184,0.1)] text-[#f0f6fc] border-2 border-[rgba(148,163,184,0.3)]',
    ghost: 'bg-transparent hover:bg-[rgba(148,163,184,0.1)] text-[#f0f6fc]',
    danger: 'bg-gradient-to-r from-red-600 to-red-500 hover:from-red-500 hover:to-red-400 text-white shadow-lg shadow-red-500/25'
  };
  
  const sizes = {
    sm: 'px-3 py-1.5 text-xs gap-1.5',
    md: 'px-4 py-2 text-sm gap-2',
    lg: 'px-6 py-3 text-base gap-2.5'
  };
  
  return (
    <button
      className={`
        ${baseStyles}
        ${variants[variant]}
        ${sizes[size]}
        ${fullWidth ? 'w-full' : ''}
        ${className}
      `}
      disabled={disabled || loading}
      style={style}
      {...props}
    >
      {loading && (
        <Loader2 
          className="animate-spin absolute" 
          size={size === 'sm' ? 14 : size === 'lg' ? 20 : 16} 
        />
      )}
      <span className={loading ? 'invisible' : 'flex items-center gap-inherit'}>
        {leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
        {children}
        {rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
      </span>
    </button>
  );
}

/**
 * IconButton - Compact button for icon-only actions
 */
interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
}

export function IconButton({
  children,
  variant = 'default',
  size = 'md',
  loading = false,
  disabled,
  className = '',
  ...props
}: IconButtonProps) {
  const baseStyles = `
    inline-flex items-center justify-center rounded-lg
    transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 
    focus-visible:ring-indigo-500/50 focus-visible:ring-offset-2 
    focus-visible:ring-offset-[#0e1117] disabled:opacity-50 
    disabled:cursor-not-allowed disabled:pointer-events-none
  `;
  
  const variants = {
    default: 'bg-[#2d333b] hover:bg-[#373f47] text-[#f0f6fc] border border-[rgba(148,163,184,0.2)]',
    ghost: 'bg-transparent hover:bg-[rgba(148,163,184,0.1)] text-[#f0f6fc]',
    danger: 'bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30'
  };
  
  const sizes = {
    sm: 'p-1.5',
    md: 'p-2',
    lg: 'p-2.5'
  };
  
  return (
    <button
      className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <Loader2 className="animate-spin" size={size === 'sm' ? 14 : size === 'lg' ? 20 : 16} />
      ) : (
        children
      )}
    </button>
  );
}

/**
 * ButtonGroup - Container for grouped buttons
 */
interface ButtonGroupProps {
  children: ReactNode;
  className?: string;
  orientation?: 'horizontal' | 'vertical';
}

export function ButtonGroup({ 
  children, 
  className = '', 
  orientation = 'horizontal' 
}: ButtonGroupProps) {
  return (
    <div 
      className={`
        inline-flex ${orientation === 'horizontal' ? 'flex-row' : 'flex-col'} 
        gap-1 ${className}
      `}
    >
      {children}
    </div>
  );
}
