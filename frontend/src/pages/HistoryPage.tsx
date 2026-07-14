import { lazy, Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getHistory } from '../lib/endpoints'
import type { HistoryData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { ErrorState, LoadingState } from '../components/StatusStates'

// recharts 體積大，比照 `TrustRadarChart` 慣例 code-split 成獨立 chunk。
const TrustHistoryChart = lazy(() => import('../components/TrustHistoryChart'))

const DAY_OPTIONS = [7, 30, 90] as const

function paramsFromSearch(sp: URLSearchParams): { coin: string; days: number } {
  const coin = sp.get('coin') || COIN_POOL[0]
  const daysRaw = Number(sp.get('days'))
  const days = DAY_OPTIONS.includes(daysRaw as (typeof DAY_OPTIONS)[number]) ? daysRaw : 30
  return { coin, days }
}

export default function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { coin, days } = paramsFromSearch(searchParams)

  const [data, setData] = useState<HistoryData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getHistory({ coin, days }, controller.signal).then((res) => {
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
  }, [coin, days])

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,#0b1420 0%,#050810 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Point-in-time archive</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">歷史信任趨勢</h1>
        <p className="mt-1 text-sm text-tf-text2">
          每日快照的 point-in-time 序列，用來看資料充分度與信任分如何變化；不是預測價格的保證。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
        <div>
          <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="hist-coin">
            幣種
          </label>
          <select
            id="hist-coin"
            value={coin}
            onChange={(e) => setSearchParams({ coin: e.target.value, days: String(days) })}
            className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
          >
            {COIN_POOL.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="hist-days">
            區間
          </label>
          <select
            id="hist-days"
            value={days}
            onChange={(e) => setSearchParams({ coin, days: e.target.value })}
            className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text"
          >
            {DAY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                近 {d} 天
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading && <LoadingState label={`${coin} 歷史資料載入中…`} />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && data && data.history.length === 0 && (
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-6 text-center text-sm text-tf-muted">
          {coin} 歷史累積中——排程尚未寫入任何按日快照，稍後再回來看看。
        </div>
      )}
      {!loading && !error && data && data.history.length > 0 && data.history.length < 3 && (
        <div className="rounded-lg border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_8%,transparent)] p-3 text-xs text-tf-warn" role="status">
          目前僅累積 {data.history.length} 筆資料點，趨勢線尚不具代表性，持續累積中。
        </div>
      )}
      {!loading && !error && data && data.history.length > 0 && (
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
          <Suspense fallback={<LoadingState label="趨勢圖載入中…" />}>
            <TrustHistoryChart history={data.history} />
          </Suspense>
        </div>
      )}
    </main>
  )
}
