import { ReactNode, HTMLAttributes } from 'react';

/**
 * Card Component - Modern, versatile card container
 * Front-end only update - No backend changes required
 * 
 * @param variant - Visual style variant (default, elevated, outlined, glass)
 * @param padding - Internal spacing (none, sm, md, lg)
 * @param hover - Enable hover lift effect
 * @param className - Additional CSS classes
 */
interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'elevated' | 'outlined' | 'glass';
  padding?: 'none' | 'sm' | 'md' | 'lg';
  hover?: boolean;
  className?: string;
}

export function Card({ 
  children, 
  variant = 'default', 
  padding = 'md',
  hover = false,
  className = '',
  ...props 
}: CardProps) {
  const baseStyles = "rounded-xl transition-all duration-200";
  
  const variants = {
    default: "bg-[#21262d]/80 border border-[rgba(148,163,184,0.12)]",
    elevated: "bg-[#2d333b] border border-[rgba(148,163,184,0.15)] shadow-lg",
    outlined: "bg-transparent border-2 border-[rgba(148,163,184,0.2)]",
    glass: "bg-[rgba(33,38,45,0.6)] backdrop-blur-xl border border-[rgba(148,163,184,0.1)]"
  };
  
  const paddings = {
    none: "",
    sm: "p-3",
    md: "p-5",
    lg: "p-6"
  };
  
  const hoverStyles = hover 
    ? "hover:border-[rgba(148,163,184,0.3)] hover:shadow-xl hover:shadow-black/20 hover:-translate-y-0.5 cursor-pointer" 
    : "";
  
  return (
    <div 
      className={`${baseStyles} ${variants[variant]} ${paddings[padding]} ${hoverStyles} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * CardHeader - Header section for cards with optional border
 */
interface CardHeaderProps {
  children: ReactNode;
  className?: string;
}

export function CardHeader({ children, className = "" }: CardHeaderProps) {
  return (
    <div className={`flex items-center justify-between mb-4 pb-3 border-b border-[rgba(148,163,184,0.12)] ${className}`}>
      {children}
    </div>
  );
}

/**
 * CardTitle - Title text for card headers
 */
interface CardTitleProps {
  children: ReactNode;
  className?: string;
}

export function CardTitle({ children, className = "" }: CardTitleProps) {
  return (
    <h3 className={`text-base font-semibold text-[#f0f6fc] ${className}`}>
      {children}
    </h3>
  );
}

/**
 * CardContent - Content area for cards
 */
interface CardContentProps {
  children: ReactNode;
  className?: string;
}

export function CardContent({ children, className = "" }: CardContentProps) {
  return (
    <div className={className}>
      {children}
    </div>
  );
}

/**
 * CardFooter - Footer section for cards with optional border
 */
interface CardFooterProps {
  children: ReactNode;
  className?: string;
}

export function CardFooter({ children, className = "" }: CardFooterProps) {
  return (
    <div className={`flex items-center justify-between mt-4 pt-3 border-t border-[rgba(148,163,184,0.12)] ${className}`}>
      {children}
    </div>
  );
}
