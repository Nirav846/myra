import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';
import { SettingsProvider } from './lib/SettingsContext.tsx';
import { WatchlistProvider } from './lib/WatchlistContext.tsx';
import { ToastProvider } from './components/ui/Toast.tsx';
import { HashRouter } from 'react-router-dom';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SettingsProvider>
      <WatchlistProvider>
        <ToastProvider>
          <HashRouter>
            <App />
          </HashRouter>
        </ToastProvider>
      </WatchlistProvider>
    </SettingsProvider>
  </StrictMode>,
);
