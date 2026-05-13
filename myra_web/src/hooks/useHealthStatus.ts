import { useState, useEffect } from 'react';
import { getLibrarian } from '../lib/Librarian';
import { useSettings } from '../lib/SettingsContext';

export function useHealthStatus() {
  const lib = getLibrarian();
  const { settings } = useSettings();
  const [health, setHealth] = useState(lib.health);
  const [isConnected, setIsConnected] = useState(lib.isConnectedToLocalRepo);

  useEffect(() => {
    let interval: any;
    
    const checkState = () => {
        setHealth({...lib.health});
        setIsConnected(lib.isConnectedToLocalRepo);
    };

    const pingHealth = () => {
        const baseUrl = lib.apiUrl.endsWith('/api') ? lib.apiUrl.slice(0, -4) : lib.apiUrl;
        return fetch(`${baseUrl}/api/health`).then(res => {
            if (res.ok) return res.json();
            throw new Error("unhealthy");
        }).then(data => {
            lib.health = data.health || lib.health;
            lib.isConnectedToLocalRepo = true;
            checkState();
        }).catch(() => {
            lib.isConnectedToLocalRepo = false;
            checkState();
        });
    };

    // Initial check
    pingHealth();

    if (settings.autoRefreshInterval !== 'Off') {
        const msMap: Record<string, number> = {
          '10s': 10000,
          '30s': 30000,
          '1min': 60000,
          '5min': 300000
        };
        const intervalTime = msMap[settings.autoRefreshInterval] || 10000;
        
        interval = setInterval(() => {
           if (document.hidden) return;
           pingHealth();
        }, intervalTime);
    }
    
    return () => {
       if (interval) clearInterval(interval);
    };
  }, [settings.autoRefreshInterval]);

  return { health, isConnected };
}
