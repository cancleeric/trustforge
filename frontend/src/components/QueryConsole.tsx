import { useEffect, useState } from 'react'
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

// 單幣分析的預設問題文字——跟 `AnalyzePage.tsx::paramsFromSearch` 是同一份
// 字面字串（刻意不跨檔 export：避免這個元件檔案混雜 component 以外的 value
// export 而失去 react-refresh 資格，比照 `ComparePage.tsx::defaultQuery`
// 同樣選擇就地各自一份、非共用 import）。
function defaultAnalyzeQuery(coin: string): string {
  return `分析${coin}近期市場狀況，整合多源資料`
}

export default function QueryConsole({ initial, onSubmit }: Props) {
  const [coin, setCoin] = useState(initial.coin)
  const [type, setType] = useState(initial.type)
  const [q, setQ] = useState(initial.q)
  // 使用者是否手動編輯過問題欄——一旦編輯過，換幣就不再覆蓋掉他打的字；
  // 「送出/瀏覽器上下頁」會在下方 effect 裡重置，視為開啟一輪新的表單狀態。
  const [queryEdited, setQueryEdited] = useState(false)

  // 瀏覽器上下頁（或外部導覽帶新的 coin/type/q 進來）：URL 是唯一事實來源，
  // 把整個表單（含 queryEdited 旗標）重新對齊，避免可見控件跟網址/報告脫節。
  useEffect(() => {
    setCoin(initial.coin)
    setType(initial.type)
    setQ(initial.q)
    setQueryEdited(false)
  }, [initial.coin, initial.type, initial.q])

  function handleCoinChange(next: string) {
    setCoin(next)
    if (!queryEdited) setQ(defaultAnalyzeQuery(next))
  }

  return (
    <form
      className="hermes-clip flex flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit({ coin, type, q })
      }}
    >
      <div className="border-b border-tf-border pb-3">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Run configuration</p>
        <h2 className="mt-1 text-sm font-semibold text-tf-text">設定本次分析</h2>
        <p className="mt-1 text-xs text-tf-muted">送出後會建立獨立 run，來源與執行紀錄不會覆蓋既有結果。</p>
      </div>
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="qc-coin">
          幣種
        </label>
        <select
          id="qc-coin"
          value={coin}
          onChange={(e) => handleCoinChange(e.target.value)}
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
          分析模式
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
          onChange={(e) => {
            setQ(e.target.value)
            setQueryEdited(true)
          }}
          rows={3}
          className="w-full resize-none rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        />
      </div>
      <button
        type="submit"
        className="rounded-md bg-tf-accent px-3 py-2 text-sm font-semibold text-white hover:opacity-90"
      >
        執行分析 <span className="tf-num opacity-70">&#8594;</span>
      </button>
    </form>
  )
}
