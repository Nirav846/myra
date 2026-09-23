/**
 * Toast Notification Component
 * Front-end only - No backend changes required
 * 
 * Features:
 * - Success, error, info, warning variants
 * - Auto-dismiss with manual override
 * - Stacked notifications
 * - Accessible with ARIA live regions
 * - Smooth animations
 */

import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
}

interface ToastContextType {
  addToast: (message: string, type?: ToastType, duration?: number) => void;
  removeToast: (id: string) => void;
  success: (message: string, duration?: number) => void;
  error: (message: string, duration?: number) => void;
  info: (message: string, duration?: number) => void;
  warning: (message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

// Default durations
const DEFAULT_DURATION = 5000;
const SUCCESS_DURATION = 3000;
const ERROR_DURATION = 8000;
const INFO_DURATION = 5000;
const WARNING_DURATION = 6000;

// Icon mapping
const icons: Record<ToastType, React.ReactNode> = {
  success: <CheckCircle className="w-5 h-5 text-success" />,
  error: <AlertCircle className="w-5 h-5 text-error" />,
  info: <Info className="w-5 h-5 text-info" />,
  warning: <AlertTriangle className="w-5 h-5 text-warning" />
};

// Background colors
const bgColors: Record<ToastType, string> = {
  success: 'bg-success-bg',
  error: 'bg-error-bg',
  info: 'bg-info-bg',
  warning: 'bg-warning-bg'
};

// Border colors
const borderColors: Record<ToastType, string> = {
  success: 'border-success',
  error: 'border-error',
  info: 'border-info',
  warning: 'border-warning'
};

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context;
};

interface ToastProviderProps {
  children: React.ReactNode;
  maxToasts?: number;
}

export const ToastProvider: React.FC<ToastProviderProps> = ({ 
  children, 
  maxToasts = 5 
}) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timeoutRefs = useRef<Map<string, NodeJS.Timeout>>(new Map());

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
    const timeout = timeoutRefs.current.get(id);
    if (timeout) {
      clearTimeout(timeout);
      timeoutRefs.current.delete(id);
    }
  }, []);

  const addToast = useCallback((
    message: string,
    type: ToastType = 'info',
    duration: number = DEFAULT_DURATION
  ) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    
    setToasts(prev => {
      const newToasts = [...prev, { id, message, type, duration }];
      // Keep only the most recent toasts if exceeding max
      return newToasts.slice(-maxToasts);
    });

    // Auto-dismiss
    const timeout = setTimeout(() => {
      removeToast(id);
    }, duration);
    
    timeoutRefs.current.set(id, timeout);
  }, [maxToasts, removeToast]);

  // Convenience methods
  const success = useCallback((message: string, duration?: number) => {
    addToast(message, 'success', duration ?? SUCCESS_DURATION);
  }, [addToast]);

  const error = useCallback((message: string, duration?: number) => {
    addToast(message, 'error', duration ?? ERROR_DURATION);
  }, [addToast]);

  const info = useCallback((message: string, duration?: number) => {
    addToast(message, 'info', duration ?? INFO_DURATION);
  }, [addToast]);

  const warning = useCallback((message: string, duration?: number) => {
    addToast(message, 'warning', duration ?? WARNING_DURATION);
  }, [addToast]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      timeoutRefs.current.forEach(timeout => clearTimeout(timeout));
    };
  }, []);

  return (
    <ToastContext.Provider value={{ addToast, removeToast, success, error, info, warning }}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  );
};

interface ToastContainerProps {
  toasts: Toast[];
  removeToast: (id: string) => void;
}

const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, removeToast }) => {
  if (toasts.length === 0) return null;

  return (
    <div 
      className="toast-container"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      aria-atomic="true"
    >
      {toasts.map((toast) => (
        <ToastItem 
          key={toast.id} 
          toast={toast} 
          onDismiss={() => removeToast(toast.id)}
        />
      ))}
    </div>
  );
};

interface ToastItemProps {
  toast: Toast;
  onDismiss: () => void;
}

const ToastItem: React.FC<ToastItemProps> = ({ toast, onDismiss }) => {
  const [isExiting, setIsExiting] = useState(false);
  const [isProgressing, setIsProgressing] = useState(true);

  const handleDismiss = useCallback(() => {
    setIsExiting(true);
    setTimeout(onDismiss, 200);
  }, [onDismiss]);

  // Pause progress on hover
  const handleMouseEnter = useCallback(() => {
    setIsProgressing(false);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setIsProgressing(true);
  }, []);

  return (
    <div
      className={`toast toast-${toast.type} ${isExiting ? 'toast-exit' : 'toast-enter'}`}
      role="alert"
      aria-live="assertive"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div className="toast-icon">
        {icons[toast.type]}
      </div>
      
      <div className="toast-content">
        <span className="toast-message">{toast.message}</span>
      </div>

      <button
        className="toast-dismiss"
        onClick={handleDismiss}
        aria-label="Dismiss notification"
        type="button"
      >
        <X className="w-4 h-4" />
      </button>

      {/* Progress bar */}
      {toast.duration && toast.duration > 0 && (
        <div 
          className={`toast-progress toast-progress-${toast.type}`}
          style={{ 
            animationDuration: `${toast.duration}ms`,
            animationPlayState: isProgressing ? 'running' : 'paused'
          }}
        />
      )}
    </div>
  );
};

export default ToastProvider;
