import { useEffect } from 'react';
import { useSettings } from './SettingsContext';

type AlertLevel = 'info' | 'warning' | 'critical';

export interface AlertEvent {
  title: string;
  message: string;
  level: AlertLevel;
  source: string; // e.g. "FVG Scanner", "Reversion Engine"
}

// Simple event bus implementation
type AlertListener = (event: AlertEvent) => void;
class EventBus {
  private listeners: AlertListener[] = [];

  subscribe(listener: AlertListener) {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  emit(event: AlertEvent) {
    this.listeners.forEach(l => l(event));
  }
}

export const alertBus = new EventBus();

export function AlertManager() {
  const { settings } = useSettings();

  useEffect(() => {
    // We only create the audio context when needed to respect browser policies
    let audioContext: AudioContext | null = null;
    
    const playTone = (level: AlertLevel) => {
        if (!settings.enableSoundAlerts || settings.alertVolume === 0) return;
        
        try {
            if (!audioContext) audioContext = new AudioContext();
            if (audioContext.state === 'suspended') audioContext.resume();
            
            const osc = audioContext.createOscillator();
            const gain = audioContext.createGain();
            
            osc.connect(gain);
            gain.connect(audioContext.destination);
            
            // Set base frequency
            let freq = 440;
            if (level === 'warning') freq = 600;
            else if (level === 'critical') freq = 880;
            
            osc.frequency.setValueAtTime(freq, audioContext.currentTime);
            if (level === 'critical') {
               // Add wobble for critical
               osc.frequency.linearRampToValueAtTime(freq * 1.5, audioContext.currentTime + 0.1);
               osc.frequency.linearRampToValueAtTime(freq, audioContext.currentTime + 0.2);
            }
            
            osc.type = level === 'critical' ? 'sawtooth' : 'sine';
            
            // Set volume and envelope
            const vol = settings.alertVolume / 100;
            gain.gain.setValueAtTime(0, audioContext.currentTime);
            gain.gain.linearRampToValueAtTime(vol, audioContext.currentTime + 0.05);
            gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.5);
            
            osc.start(audioContext.currentTime);
            osc.stop(audioContext.currentTime + 0.5);
        } catch(e) {
            console.error("Audio playback failed", e);
        }
    };

    const maybeShowDesktopNotification = (event: AlertEvent) => {
        if (!settings.browserNotifications) return;
        if (!document.hidden && event.level !== 'critical') return; // Only notify if background or critical
        
        if (Notification.permission === 'granted') {
           new Notification(`MYRA ${event.level.toUpperCase()}: ${event.title}`, {
               body: event.message,
               icon: '/favicon.ico'
           });
        }
    };

    const unsubscribe = alertBus.subscribe((event) => {
        playTone(event.level);
        maybeShowDesktopNotification(event);
    });

    return () => {
        unsubscribe();
        if (audioContext) audioContext.close();
    };
  }, [settings.enableSoundAlerts, settings.alertVolume, settings.browserNotifications]);

  return null; // Invisible manager component
}
