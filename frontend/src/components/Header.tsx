import { Link, NavLink } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'

const NAV_ITEMS = [
  { to: '/analyze', label: '分析' },
  { to: '/compare', label: '比較' },
  { to: '/history', label: '歷史趨勢' },
  { to: '/status', label: '狀態' },
  { to: '/costs', label: '成本' },
]

export default function Header() {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-tf-border bg-tf-card px-4 py-3 sm:px-6">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-base font-bold text-tf-text no-underline"
      >
        <span className="text-tf-link">&#9670;</span>
        Trust<span className="text-tf-link">Forge</span>
      </Link>

      <nav aria-label="主導覽" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `whitespace-nowrap rounded-md px-2 py-1 text-sm no-underline transition ${
                isActive ? 'bg-tf-accent/20 font-semibold text-tf-link' : 'text-tf-text2 hover:text-tf-link'
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <span className="hidden rounded-md border border-tf-muted/40 px-2 py-0.5 text-xs text-tf-muted sm:inline">v0.6.5</span>
      <ThemeToggle />
    </header>
  )
}
