import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main className="mx-auto flex max-w-xl flex-col items-center gap-3 px-4 py-16 text-center">
      <h1 className="text-2xl font-bold text-tf-text">404 — 頁面不存在</h1>
      <p className="text-sm text-tf-muted">你要找的頁面不存在，可能連結有誤或已移除。</p>
      <Link to="/" className="text-tf-link underline">
        回首頁
      </Link>
    </main>
  )
}
