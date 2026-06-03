import { useState, useEffect, useRef } from 'react';
import { NavLink } from 'react-router-dom';

interface Tab {
  id: string;
  path: string;
  icon: string | React.ReactNode;
}

interface NavbarProps {
  tabs: Tab[];
}

export default function Navbar({ tabs }: NavbarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [overflowTabs, setOverflowTabs] = useState<string[]>([]);
  const [moreOpen, setMoreOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver(() => {
      const containerWidth = el.clientWidth;
      const children = el.children;
      let totalWidth = 0;
      const visible: string[] = [];
      const hidden: string[] = [];
      let moreWidth = 0;

      for (let i = 0; i < children.length; i++) {
        const child = children[i] as HTMLElement;
        if (child.dataset.more) {
          moreWidth = child.offsetWidth + 8;
          continue;
        }
        const w = child.getBoundingClientRect().width;
        if (totalWidth + w + moreWidth <= containerWidth) {
          totalWidth += w;
          visible.push(tabs[i]?.id || '');
        } else {
          hidden.push(tabs[i]?.id || '');
        }
      }
      setOverflowTabs(hidden);
    });

    ro.observe(el);
    return () => ro.disconnect();
  }, [tabs]);

  useEffect(() => {
    if (!moreOpen) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [moreOpen]);

  return (
    <nav className="navbar" role="navigation" aria-label="Main navigation">
      <div className="menu-container" tabIndex={0}>
        <div className="menu" ref={containerRef} style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
          {tabs.map((tab) => {
            const hidden = overflowTabs.includes(tab.id);
            return (
              <div
                key={tab.id}
                style={{ display: hidden ? 'none' : 'flex' }}
              >
                <NavLink
                  to={tab.path}
                  className={({ isActive }) => isActive ? 'active' : ''}
                >
                  <span className="nav-icon">
                    {typeof tab.icon === 'string' ? tab.icon : tab.icon}
                  </span>
                  <span className="nav-label">{tab.id}</span>
                </NavLink>
              </div>
            );
          })}
          {overflowTabs.length > 0 && (
            <div data-more className="relative" ref={dropdownRef}>
              <button
                onClick={() => setMoreOpen(o => !o)}
                className="px-2 py-1 text-[11px] font-mono text-[#888] hover:text-white transition-colors whitespace-nowrap"
                title="More tabs"
              >
                More ▾
              </button>
              {moreOpen && (
                <div className="absolute right-0 top-full mt-1 z-50 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl py-1 min-w-[140px]">
                  {overflowTabs.map(id => {
                    const tab = tabs.find(t => t.id === id);
                    if (!tab) return null;
                    return (
                      <NavLink
                        key={tab.id}
                        to={tab.path}
                        onClick={() => setMoreOpen(false)}
                        className={({ isActive }) =>
                          `block px-3 py-1.5 text-[11px] font-mono transition-colors ${
                            isActive
                              ? 'text-cyan-400 bg-cyan-500/10'
                              : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                          }`
                        }
                      >
                        {typeof tab.icon === 'string' ? tab.icon : null} {tab.id}
                      </NavLink>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
