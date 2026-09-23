import { Suspense, lazy, ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

interface PageLoaderProps {
  message?: string;
}

export function PageLoader({ message = 'Loading...' }: PageLoaderProps) {
  return (
    <div 
      className="flex flex-col items-center justify-center min-h-[400px] gap-4"
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-12 w-12 animate-spin text-indigo-500" />
      <p className="text-sm text-text-secondary">{message}</p>
    </div>
  );
}

interface LazyLoadViewProps {
  children: ReactNode;
  fallbackMessage?: string;
}

export function LazyLoadView({ children, fallbackMessage = 'Loading view...' }: LazyLoadViewProps) {
  return (
    <Suspense fallback={<PageLoader message={fallbackMessage} />}>
      {children}
    </Suspense>
  );
}

// Helper to create lazy-loaded components with proper error handling
export function createLazyComponent<T extends { default: React.ComponentType<any> }>(
  importFn: () => Promise<T>,
  componentName: string
) {
  return lazy<T['default']>(() =>
    importFn().catch((error): T => {
      console.error(`Failed to load component ${componentName}:`, error);
      // Return a fallback component
      return {
        default: () => (
          <div className="p-8 text-center text-error">
            <h3 className="text-lg font-semibold mb-2">Failed to load {componentName}</h3>
            <p className="text-sm text-text-secondary">Please refresh the page or try again later.</p>
          </div>
        )
      } as unknown as T;
    })
  );
}
