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
    
    const check = () => {
        setHealth({...lib.health});
        setIsConnected(lib.isConnectedToLocalRepo);
    };

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
           
           // A hacky way to force check: access private method or just rely on librarian's executing queries keeping health fresh 
           // Usually Librarian updates health over time if requests fail, but we could explicitly ping:
           fetch(`${lib.apiUrl}/health`).then(res => {
               if (res.ok) return res.json();
               throw new Error("unhealthy");
           }).then(data => {
               lib.health = data.health || lib.health;
               lib.isConnectedToLocalRepo = true;
               check();
           }).catch(() => {
               lib.isConnectedToLocalRepo = false;
               check();
           });
           
        }, intervalTime);
    }
    
    // Initial check
    check();

    return () => {
       if (interval) clearInterval(interval);
    };
  }, [settings.autoRefreshInterval]);

  return { health, isConnected };
}
