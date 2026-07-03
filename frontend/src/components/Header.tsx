import { Link } from 'react-router-dom'

export default function Header() {
  return (
    <header className="flex items-center gap-4 border-b border-tf-border bg-tf-card px-6 py-3">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-base font-bold text-tf-text no-underline"
      >
        <span className="text-tf-link">&#9670;</span>
        Trust<span className="text-tf-link">Forge</span>
      </Link>
      <div className="flex-1" />
      <span className="hidden text-xs text-tf-muted sm:inline">React 前端（Phase 2a）</span>
    </header>
  )
}
