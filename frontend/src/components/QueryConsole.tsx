import { useState } from 'react'
import { Link } from 'react-router-dom'
import { COIN_POOL, QUESTION_TYPES } from '../lib/constants'

export interface QueryValues {
  coin: string
  type: string
  q: string
}

interface Props {
  initial: QueryValues
  onSubmit: (values: QueryValues) => void
}

export default function QueryConsole({ initial, onSubmit }: Props) {
  const [coin, setCoin] = useState(initial.coin)
  const [type, setType] = useState(initial.type)
  const [q, setQ] = useState(initial.q)

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit({ coin, type, q })
      }}
    >
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-coin">
          幣種
        </label>
        <select
          id="qc-coin"
          value={coin}
          onChange={(e) => setCoin(e.target.value)}
          className="w-full rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        >
          {COIN_POOL.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-type">
          題型
        </label>
        <select
          id="qc-type"
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="w-full rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        >
          {QUESTION_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[0.68rem] text-tf-muted">
          雙幣比較請至
          <Link to="/compare" className="text-tf-link underline">
            比較頁
          </Link>
          。
        </p>
      </div>
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-q">
          問題
        </label>
        <textarea
          id="qc-q"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          rows={3}
          className="w-full resize-none rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        />
      </div>
      <button
        type="submit"
        className="rounded-md bg-tf-accent px-3 py-2 text-sm font-semibold text-white hover:opacity-90"
      >
        Run analysis <span className="tf-num opacity-70">&#8629;</span>
      </button>
    </form>
  )
}
