import { useState, useEffect, useRef, useMemo } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { createPortal } from 'react-dom';

interface Tab {
  id: string;
  path: string;
  icon: string | React.ReactNode;
  category?: string;
  group?: string;
}

interface NavbarProps {
  tabs: Tab[];
}

const CATEGORY_LABELS: Record<string, string> = {
  scanners: 'Scanners',
  analysis: 'Analysis',
  data: 'Data',
  experimental: 'Experimental',
};

const CATEGORY_ORDER = ['dashboard', 'scanners', 'analysis', 'data', 'experimental'];

const GROUP_ORDER = ['Overview', 'Price Action', 'Delivery / Volume', 'Institutional / Flow', 'ML / Momentum'];

export default function Navbar({ tabs }: NavbarProps) {
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [overflowCategoryKeys, setOverflowCategoryKeys] = useState<string[]>([]);
  const [dropdownFilter, setDropdownFilter] = useState('');

  const grouped = useMemo(() => {
    const map: Record<string, Tab[]> = {};
    for (const tab of tabs) {
      const cat = tab.category || 'other';
      if (!map[cat]) map[cat] = [];
      map[cat].push(tab);
    }
    return map;
  }, [tabs]);

  const activeCategory = useMemo(() => {
    for (const tab of tabs) {
      if (tab.path && location.pathname.startsWith(tab.path)) {
        return tab.category || 'other';
      }
    }
    return null;
  }, [tabs, location.pathname]);

  useEffect(() => {
    if (!openDropdown && !moreOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const inMenu = menuRef.current?.contains(target);
      const inDropdown = document.querySelector('[data-nav-dropdown]')?.contains(target);
      if (!inMenu && !inDropdown) {
        setOpenDropdown(null);
        setMoreOpen(false);
        setDropdownFilter('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [openDropdown, moreOpen]);

  useEffect(() => {
    if (!openDropdown && !moreOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpenDropdown(null);
        setMoreOpen(false);
        setDropdownFilter('');
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [openDropdown, moreOpen]);

  const categoryKeys = useMemo(
    () => CATEGORY_ORDER.filter(k => k === 'dashboard' || (grouped[k]?.length || 0) > 0),
    [grouped]
  );

  useEffect(() => {
    const el = menuRef.current;
    if (!el) return;

    const ro = new ResizeObserver(() => {
      const containerWidth = el.clientWidth;
      const children = Array.from(el.children);
      let totalWidth = 0;
      const hidden: string[] = [];
      let moreBtnSpace = 0;

      for (let i = 0; i < children.length; i++) {
        const child = children[i] as HTMLElement;
        if (child.dataset.moreBtn) {
          moreBtnSpace = child.offsetWidth + 8;
          continue;
        }
        if (child.dataset.cat) {
          const w = child.getBoundingClientRect().width;
          const remaining = children.slice(i + 1).filter(c => !(c as HTMLElement).dataset.moreBtn);
          const needsMore = remaining.length > 0;
          const needed = totalWidth + w + (needsMore ? moreBtnSpace : 0);
          if (needed <= containerWidth) {
            totalWidth += w;
          } else {
            hidden.push(child.dataset.cat);
          }
        }
      }
      setOverflowCategoryKeys(hidden);
    });

    ro.observe(el);
    return () => ro.disconnect();
  }, [categoryKeys]);

  const visibleKeys = categoryKeys.filter(k => !overflowCategoryKeys.includes(k));
  const hasOverflow = overflowCategoryKeys.length > 0;

  const overflowTabs = useMemo(() => {
    const result: Tab[] = [];
    for (const key of overflowCategoryKeys) {
      const catTabs = grouped[key] || [];
      result.push(...catTabs);
    }
    return result;
  }, [overflowCategoryKeys, grouped]);

  const renderCategoryContent = (catKey: string) => {
    if (catKey === 'dashboard') {
      const tabs = grouped['dashboard'] || [];
      if (tabs.length === 0) return null;
      return (
        <div key="dashboard" data-cat="dashboard" className="flex items-center gap-1">
          {tabs.map((tab) => (
            <NavLink
              key={tab.id}
              to={tab.path}
              className={({ isActive }) =>
                `px-2 py-1 text-[12px] font-mono whitespace-nowrap transition-colors inline-flex items-center gap-1 ${
                  isActive ? 'text-cyan-400' : 'text-[#aaa] hover:text-white'
                }`
              }
            >
              <span className="nav-icon">{typeof tab.icon === 'string' ? tab.icon : tab.icon}</span>
              <span className="nav-label">{tab.id}</span>
            </NavLink>
          ))}
        </div>
      );
    }

    const catTabs = grouped[catKey] || [];
    if (catTabs.length === 0) return null;
    const isActive = activeCategory === catKey;

    // Multi-column dropdown for big grouped categories (scanners): keep it short
    // enough to fit the viewport instead of overflowing the bottom edge.
    const isScannersGrouped = catTabs.some(t => t.group) && catTabs.length >= 8;
    const btnRect = buttonRefs.current[catKey]?.getBoundingClientRect();
    const dropdownStyle: React.CSSProperties = isScannersGrouped
      ? {
          position: 'fixed',
          left: Math.max(8, Math.min(btnRect?.left ?? 0, window.innerWidth - 560 - 8)),
          top: (btnRect?.bottom ?? 0) + 4,
          zIndex: 9999,
          width: 560,
        }
      : {
          position: 'fixed',
          left: btnRect?.left ?? 0,
          top: (btnRect?.bottom ?? 0) + 4,
          zIndex: 9999,
        };

    return (
      <div key={catKey} data-cat={catKey} className="relative">
        <button
          ref={el => { buttonRefs.current[catKey] = el; }}
          onClick={() => {
            const next = openDropdown === catKey ? null : catKey;
            setOpenDropdown(next);
            setDropdownFilter(next ? '' : '');
          }}
          aria-haspopup="true"
          aria-expanded={openDropdown === catKey}
          className={`px-2 py-1 text-[12px] font-mono whitespace-nowrap transition-colors flex items-center gap-1 ${
            isActive ? 'text-cyan-400' : 'text-[#aaa] hover:text-white'
          }`}
        >
          {CATEGORY_LABELS[catKey] || catKey} ▾
        </button>
        {openDropdown === catKey && createPortal(
          <div
            data-nav-dropdown={catKey}
            className={`bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl py-1 ${isScannersGrouped ? 'max-h-[70vh] overflow-y-auto' : 'min-w-[180px]'}`}
            style={dropdownStyle}
          >
            {catTabs.some(t => t.group) && catTabs.length >= 8 ? (
              <>
                <div className="px-2 pt-1 pb-1">
                  <input
                    type="text"
                    placeholder="Filter scanners…"
                    value={dropdownFilter}
                    onChange={e => setDropdownFilter(e.target.value)}
                    onMouseDown={e => e.stopPropagation()}
                    className="text-[12px] font-mono bg-[#12141a] border border-[#ffffff1a] rounded px-2 py-1 w-full focus:border-cyan-500/40 text-[#ccc] outline-none placeholder:text-[#888]"
                  />
                </div>
                {dropdownFilter.trim() ? (
                  (() => {
                    const q = dropdownFilter.trim().toLowerCase();
                    const matches = catTabs.filter(t => t.id.toLowerCase().includes(q));
                    return matches.length > 0 ? (
                      <div className="columns-2 min-[480px]:columns-3 gap-1 px-1">
                        {matches.map(tab => (
                          <NavLink
                            key={tab.id}
                            to={tab.path}
                            onClick={() => { setOpenDropdown(null); setDropdownFilter(''); }}
                            className={({ isActive }) =>
                              `block break-inside-avoid px-3 py-1.5 text-[12px] font-mono transition-colors flex items-center gap-2 ${
                                isActive
                                  ? 'text-cyan-400 bg-cyan-500/10'
                                  : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                              }`
                            }
                          >
                            <span className="w-4 text-center">{typeof tab.icon === 'string' ? tab.icon : null}</span>
                            {tab.id}
                          </NavLink>
                        ))}
                      </div>
                    ) : (
                      <div className="px-3 py-2 text-[12px] text-[#888]">No scanners match</div>
                    );
                  })()
                ) : (
                  (() => {
                    const groups = new Map<string, Tab[]>();
                    const other: Tab[] = [];
                    for (const tab of catTabs) {
                      if (tab.group && GROUP_ORDER.includes(tab.group)) {
                        if (!groups.has(tab.group)) groups.set(tab.group, []);
                        groups.get(tab.group)!.push(tab);
                      } else {
                        other.push(tab);
                      }
                    }
                    const elements: React.ReactNode[] = [];
                    for (const g of GROUP_ORDER) {
                      const items = groups.get(g);
                      if (!items || items.length === 0) continue;
                      elements.push(
                        <div key={`g-${g}`} className="break-inside-avoid mb-1">
                          <div className="px-3 pt-2 pb-1 text-[12px] uppercase tracking-wider text-[#888]">{g}</div>
                          {items.map(tab => (
                            <NavLink
                              key={tab.id}
                              to={tab.path}
                              onClick={() => { setOpenDropdown(null); setDropdownFilter(''); }}
                              className={({ isActive }) =>
                                `block px-3 py-1.5 text-[12px] font-mono transition-colors flex items-center gap-2 ${
                                  isActive
                                    ? 'text-cyan-400 bg-cyan-500/10'
                                    : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                                }`
                              }
                            >
                              <span className="w-4 text-center">{typeof tab.icon === 'string' ? tab.icon : null}</span>
                              {tab.id}
                            </NavLink>
                          ))}
                        </div>
                      );
                    }
                    if (other.length > 0) {
                      elements.push(
                        <div key="g-Other" className="break-inside-avoid mb-1">
                          <div className="px-3 pt-2 pb-1 text-[12px] uppercase tracking-wider text-[#888]">Other</div>
                          {other.map(tab => (
                            <NavLink
                              key={tab.id}
                              to={tab.path}
                              onClick={() => { setOpenDropdown(null); setDropdownFilter(''); }}
                              className={({ isActive }) =>
                                `block px-3 py-1.5 text-[12px] font-mono transition-colors flex items-center gap-2 ${
                                  isActive
                                    ? 'text-cyan-400 bg-cyan-500/10'
                                    : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                                }`
                              }
                            >
                              <span className="w-4 text-center">{typeof tab.icon === 'string' ? tab.icon : null}</span>
                              {tab.id}
                            </NavLink>
                          ))}
                        </div>
                      );
                    }
                    return (
                      <div className="columns-2 min-[480px]:columns-3 gap-2 px-1">
                        {elements}
                      </div>
                    );
                  })()
                )}
              </>
            ) : (
              catTabs.map(tab => (
                <NavLink
                  key={tab.id}
                  to={tab.path}
                  onClick={() => { setOpenDropdown(null); setDropdownFilter(''); }}
                  className={({ isActive }) =>
                    `block px-3 py-1.5 text-[12px] font-mono transition-colors flex items-center gap-2 ${
                      isActive
                        ? 'text-cyan-400 bg-cyan-500/10'
                        : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                    }`
                  }
                >
                  <span className="w-4 text-center">{typeof tab.icon === 'string' ? tab.icon : null}</span>
                  {tab.id}
                </NavLink>
              ))
            )}
          </div>,
          document.body
        )}
      </div>
    );
  };

  return (
    <nav className="navbar" role="navigation" aria-label="Main navigation">
      <div className="menu-container" tabIndex={0}>
        <div className="menu" ref={menuRef} style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
          {visibleKeys.map(renderCategoryContent)}

          {hasOverflow && (
            <div data-more-btn className="relative">
              <button
                ref={moreBtnRef}
                onClick={() => setMoreOpen(o => !o)}
                className="px-2 py-1 text-[12px] font-mono text-[#aaa] hover:text-white transition-colors whitespace-nowrap"
                title="More tabs"
              >
                More ▾
              </button>
              {moreOpen && createPortal(
                <div
                  data-nav-dropdown="more"
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl py-1 min-w-[140px]"
                  style={{
                    position: 'fixed',
                    left: (moreBtnRef.current?.getBoundingClientRect().right ?? 0) - 160,
                    top: (moreBtnRef.current?.getBoundingClientRect().bottom ?? 0) + 4,
                    zIndex: 9999,
                  }}
                >
                  {overflowTabs.map(tab => (
                    <NavLink
                      key={tab.id}
                      to={tab.path}
                      onClick={() => setMoreOpen(false)}
                      className={({ isActive }) =>
                        `block px-3 py-1.5 text-[12px] font-mono transition-colors ${
                          isActive
                            ? 'text-cyan-400 bg-cyan-500/10'
                            : 'text-[#888] hover:text-white hover:bg-[#ffffff0a]'
                        }`
                      }
                    >
                      {typeof tab.icon === 'string' ? tab.icon : null} {tab.id}
                    </NavLink>
                  ))}
                </div>,
                document.body
              )}
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
