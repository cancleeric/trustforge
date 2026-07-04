import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getComparison } from '../lib/endpoints'
import type { ComparisonParams } from '../lib/endpoints'
import type { ComparisonAnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import AnalysisReportView from '../components/AnalysisReportView'
import { ErrorState, LoadingState } from '../components/StatusStates'

function defaultQuery(coin: string, coin2: string): string {
  return `比較${coin}與${coin2}近期市場狀況，整合多源資料`
}

function paramsFromSearch(sp: URLSearchParams): ComparisonParams {
  const coin = sp.get('coin') || COIN_POOL[0]
  const coin2raw = sp.get('coin2') || COIN_POOL.find((c) => c !== coin) || COIN_POOL[1]
  const coin2 = coin2raw === coin ? COIN_POOL.find((c) => c !== coin) || coin2raw : coin2raw
  const q = sp.get('q') || defaultQuery(coin, coin2)
  return { coin, coin2, q }
}

interface FormState {
  coin: string
  coin2: string
  q: string
}

function CompareForm({ initial, onSubmit }: { initial: FormState; onSubmit: (values: FormState) => void }) {
  const [coin, setCoin] = useState(initial.coin)
  const [coin2, setCoin2] = useState(initial.coin2)
  const [q, setQ] = useState(initial.q)
  // 使用者是否手動編輯過問題欄——一旦編輯過，換幣就不再覆蓋掉他打的字；
  // 這個旗標會在下方「URL 變了就整個表單重新對齊」的 effect 裡重置，
  // 代表「送出/瀏覽器上下頁」都算開啟一輪新的表單狀態。
  const [queryEdited, setQueryEdited] = useState(false)
  const sameCoin = coin === coin2

  // 瀏覽器上下頁（或外部導覽帶新的 coin/coin2/q 進來）：URL 是唯一事實來源，
  // 把整個表單（含 queryEdited 旗標）重新對齊，避免可見控件跟網址/報告脫節。
  useEffect(() => {
    setCoin(initial.coin)
    setCoin2(initial.coin2)
    setQ(initial.q)
    setQueryEdited(false)
  }, [initial.coin, initial.coin2, initial.q])

  function handleCoinChange(next: string) {
    setCoin(next)
    if (!queryEdited) setQ(defaultQuery(next, coin2))
  }

  function handleCoin2Change(next: string) {
    setCoin2(next)
    if (!queryEdited) setQ(defaultQuery(coin, next))
  }

  function handleQChange(next: string) {
    setQ(next)
    setQueryEdited(true)
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (sameCoin) return
        onSubmit({ coin, coin2, q })
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="cmp-coin">
            幣種 A
          </label>
          <select
            id="cmp-coin"
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
          <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="cmp-coin2">
            幣種 B
          </label>
          <select
            id="cmp-coin2"
            value={coin2}
            onChange={(e) => handleCoin2Change(e.target.value)}
            className="w-full rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
          >
            {COIN_POOL.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
      </div>
      {sameCoin && (
        <p className="text-xs text-tf-bad" role="alert">
          幣種 A、B 須不同才能比較。
        </p>
      )}
      <div>
        <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="cmp-q">
          問題
        </label>
        <textarea
          id="cmp-q"
          value={q}
          onChange={(e) => handleQChange(e.target.value)}
          rows={3}
          className="w-full resize-none rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
        />
      </div>
      <button
        type="submit"
        disabled={sameCoin}
        className="rounded-md bg-tf-accent px-3 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        比較分析 <span className="tf-num opacity-70">&#8629;</span>
      </button>
    </form>
  )
}

export default function ComparePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<ComparisonAnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getComparison(params, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        setData(null)
        setError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.coin, params.coin2, params.q])

  const handleSubmit = (values: FormState) => {
    setSearchParams({ coin: values.coin, coin2: values.coin2, q: values.q })
  }

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div>
        <h1 className="text-lg font-semibold text-tf-text">雙幣比較</h1>
        <p className="mt-1 text-xs text-tf-muted">
          兩份獨立分析並列呈現，各自的信任分/信心/證據互不干擾，供相對強弱比較。
        </p>
      </div>

      <CompareForm initial={{ coin: params.coin, coin2: params.coin2, q: params.q }} onSubmit={handleSubmit} />

      {loading && <LoadingState label={`比較 ${params.coin} / ${params.coin2} 中…`} />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && data && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <AnalysisReportView
            heading={`幣種 A · ${data.report_a.coin}`}
            data={{
              version: data.version,
              report: data.report_a,
              evidence: data.evidence_a,
              trust_radar: data.trust_radar_a,
              trust_components_aggregate: data.trust_components_aggregate_a,
              price_provenance: data.price_provenance_a,
              execution_log: data.execution_log,
            }}
          />
          <AnalysisReportView
            heading={`幣種 B · ${data.report_b.coin}`}
            data={{
              version: data.version,
              report: data.report_b,
              evidence: data.evidence_b,
              trust_radar: data.trust_radar_b,
              trust_components_aggregate: data.trust_components_aggregate_b,
              price_provenance: data.price_provenance_b,
              execution_log: data.execution_log,
            }}
          />
        </div>
      )}
    </main>
  )
}
