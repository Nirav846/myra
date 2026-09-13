import { useState, useEffect, useRef, useCallback } from 'react';

interface LiveRegionProps {
  message: string;
  priority?: 'polite' | 'assertive';
  onClear?: () => void;
}

/**
 * LiveRegion component for screen reader announcements
 * Implements ARIA live regions for dynamic content updates
 */
export function LiveRegion({ message, priority = 'polite', onClear }: LiveRegionProps) {
  const regionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (message && onClear) {
      // Clear the message after a delay for non-critical announcements
      const timer = setTimeout(() => {
        onClear();
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [message, onClear]);

  if (!message) return null;

  return (
    <div
      ref={regionRef}
      role="status"
      aria-live={priority}
      aria-atomic="true"
      className="sr-only"
      data-testid="live-region"
    >
      {message}
    </div>
  );
}

interface AnnouncementOptions {
  message: string;
  priority?: 'polite' | 'assertive';
  duration?: number;
}

/**
 * Hook for managing screen reader announcements
 * Returns announcement state and control functions
 */
export function useAnnouncer() {
  const [announcement, setAnnouncement] = useState<{ message: string; priority: 'polite' | 'assertive' } | null>(null);

  const announce = useCallback(({ message, priority = 'polite', duration = 5000 }: AnnouncementOptions) => {
    setAnnouncement({ message, priority });
    
    if (duration > 0) {
      setTimeout(() => {
        setAnnouncement(null);
      }, duration);
    }
  }, []);

  const clear = useCallback(() => {
    setAnnouncement(null);
  }, []);

  return { announcement, announce, clear };
}
