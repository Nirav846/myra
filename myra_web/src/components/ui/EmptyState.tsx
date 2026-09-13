import React from 'react';
import { AlertCircle, Inbox, Search, FileX, HelpCircle } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';

export interface EmptyStateProps {
  /** The type of empty state to display */
  variant?: 'noData' | 'noResults' | 'noAccess' | 'error' | 'custom';
  
  /** Custom icon (overrides the default icon for the variant) */
  icon?: React.ReactNode;
  
  /** Main heading text */
  title?: string;
  
  /** Description text explaining the empty state */
  description?: string;
  
  /** Optional action button configuration */
  action?: {
    label: string;
    onClick: () => void;
    variant?: 'primary' | 'secondary' | 'outline' | 'ghost';
    icon?: React.ReactNode;
  };
  
  /** Additional content to display below the description */
  children?: React.ReactNode;
  
  /** Custom class name for styling */
  className?: string;
}

const defaultContent: Record<string, { title: string; description: string; icon: React.ReactNode }> = {
  noData: {
    title: 'No Data Available',
    description: 'There is no data to display at this time. Please check back later or adjust your filters.',
    icon: <Inbox className="w-12 h-12" />,
  },
  noResults: {
    title: 'No Results Found',
    description: 'We couldn\'t find any matches for your search. Try adjusting your search terms or filters.',
    icon: <Search className="w-12 h-12" />,
  },
  noAccess: {
    title: 'Access Restricted',
    description: 'You don\'t have permission to view this content. Please contact your administrator if you believe this is an error.',
    icon: <FileX className="w-12 h-12" />,
  },
  error: {
    title: 'Something Went Wrong',
    description: 'We encountered an error while loading this content. Please try again later.',
    icon: <AlertCircle className="w-12 h-12" />,
  },
  custom: {
    title: '',
    description: '',
    icon: <HelpCircle className="w-12 h-12" />,
  },
};

export const EmptyState: React.FC<EmptyStateProps> = ({
  variant = 'noData',
  icon,
  title,
  description,
  action,
  children,
  className = '',
}) => {
  const content = defaultContent[variant];
  
  const displayIcon = icon || content.icon;
  const displayTitle = title !== undefined ? title : content.title;
  const displayDescription = description !== undefined ? description : content.description;
  
  return (
    <Card 
      variant="outlined" 
      className={`flex flex-col items-center justify-center p-8 text-center ${className}`}
      role="status"
      aria-live="polite"
    >
      <CardContent className="flex flex-col items-center gap-4 max-w-md">
        {/* Icon Container */}
        <div 
          className="flex items-center justify-center w-20 h-20 rounded-full bg-surface-2 text-text-secondary"
          aria-hidden="true"
        >
          {displayIcon}
        </div>
        
        {/* Text Content */}
        {displayTitle && (
          <h3 className="text-xl font-semibold text-text-primary">
            {displayTitle}
          </h3>
        )}
        
        {displayDescription && (
          <p className="text-text-secondary leading-relaxed">
            {displayDescription}
          </p>
        )}
        
        {/* Additional Content */}
        {children && (
          <div className="w-full mt-2">
            {children}
          </div>
        )}
        
        {/* Action Button */}
        {action && (
          <Button
            variant={action.variant || 'primary'}
            onClick={action.onClick}
            leftIcon={action.icon}
            className="mt-2"
          >
            {action.label}
          </Button>
        )}
      </CardContent>
    </Card>
  );
};

export default EmptyState;
