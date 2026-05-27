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
  return (
    <nav className="navbar" role="navigation" aria-label="Main navigation">
      <div className="menu-container" tabIndex={0}>
        <ul className="menu">
          {tabs.map((tab) => (
            <li key={tab.id}>
              <NavLink
                to={tab.path}
                className={({ isActive }) => isActive ? 'active' : ''}
              >
                <span className="nav-icon">
                  {typeof tab.icon === 'string' ? tab.icon : tab.icon}
                </span>
                <span className="nav-label">{tab.id}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
