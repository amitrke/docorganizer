import { Link } from 'react-router-dom'

const NAV_ITEMS = [
  { key: 'home', to: '/', label: 'Browse' },
  { key: 'settings', to: '/settings', label: 'AI Settings' },
  { key: 'categories', to: '/categories', label: 'Categories' },
]

export function Hero({ title, subtitle, active }: { title: string; subtitle: string; active: string }) {
  return (
    <div className="topbar">
      <div>
        <div className="brand">docorganizer</div>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      <nav className="nav">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.key}
            to={item.to}
            className={`nav-link${item.key === active ? ' current' : ''}`}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  )
}
