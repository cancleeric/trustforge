import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getHistory } from '../lib/endpoints'
import type { HistoryData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'
import CoinSelect from '../components/CoinSelect'
import GlossaryTerm from '../components/GlossaryTerm'

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
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams, setSearchParams] = useSearchParams()
  const { coin, days } = paramsFromSearch(searchParams)
  const updateParams = (nextCoin: string, nextDays: string | number) => {
    const next = new URLSearchParams()
    const workspace = searchParams.get('workspace')
    if (workspace) next.set('workspace', workspace)
    next.set('coin', nextCoin)
    next.set('days', String(nextDays))
    setSearchParams(next)
  }

  const [data, setData] = useState<HistoryData | null>(null)
  const dataRef = useRef<HistoryData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [retryNonce, setRetryNonce] = useState(0)

  useEffect(() => {
    dataRef.current = data
    setHologramData(data ? {
      primaryLabel: data.coin,
      total: data.history.length,
      points: data.history.map((entry) => entry.trust_score),
      primaryValue: data.history.at(-1)?.trust_score,
      trustScore: data.history.at(-1)?.trust_score,
    } : null)
    return () => setHologramData(null)
  }, [data, setHologramData])

  useEffect(() => {
    const controller = new AbortController()
    // Keep the previous complete snapshot mounted during the atomic swap. Label
    // it explicitly so it cannot be mistaken for the newly selected coin.
    const previous = dataRef.current
    if (previous) setHologramData({
      primaryLabel: previous.coin,
      total: previous.history.length,
      points: previous.history.map((entry) => entry.trust_score),
      primaryValue: previous.history.at(-1)?.trust_score,
      trustScore: previous.history.at(-1)?.trust_score,
      status: `切換至 ${coin} 中 · 顯示上一快照`,
    })
    setLoading(true)
    setError(null)
    getHistory({ coin, days }, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        setError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
  }, [coin, days, retryNonce, setHologramData])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setRetryNonce((value) => value + 1), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Point-in-time archive</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">歷史信任趨勢</h1>
        <p className="mt-1 text-sm text-tf-text2">
          每日快照的 point-in-time 序列，用來看<GlossaryTerm term="dataSufficiency" label="資料充分度" compact />與<GlossaryTerm term="trustScore" label="信任分" compact />如何變化；不是預測價格的保證。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
        <CoinSelect id="hist-coin" value={coin} onChange={(next) => updateParams(next, days)} />
        <div>
          <label className="mb-1 block text-xs font-semibold text-tf-muted" htmlFor="hist-days">
            區間
          </label>
          <select
            id="hist-days"
            value={days}
            onChange={(e) => updateParams(coin, e.target.value)}
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

      {loading && null}
      {!loading && error && !data && <ErrorState code={error.code} message={error.message} />}
      {error && data && (
        <ErrorState code={error.code} message={error.message} note="目前保留上一個完整快照" />
      )}
      {data && data.history.length === 0 && (
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-6 text-center text-sm text-tf-muted">
          <p className="font-semibold text-tf-text">{coin} 尚未累積歷史快照</p>
          <p className="mt-2">這不代表信任分數為零；完成分析並累積快照後，這裡才會顯示變化趨勢。</p>
          <a href="/" className="mt-4 inline-flex rounded border border-tf-accent px-3 py-2 font-semibold text-tf-link no-underline hover:bg-[color-mix(in_srgb,var(--color-tf-accent)_8%,transparent)]">回首頁選擇分析任務</a>
        </div>
      )}
      {data && data.history.length > 0 && data.history.length < 3 && (
        <div className="rounded-lg border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_8%,transparent)] p-3 text-xs text-tf-warn" role="status">
          目前僅累積 {data.history.length} 筆資料點，趨勢線尚不具代表性，持續累積中。
        </div>
      )}
      {data && data.history.length > 0 && (
        <div key={`${data.coin}-${days}`} className={`hermes-data-swap hermes-clip rounded-lg border border-tf-border bg-tf-card p-4${loading ? ' is-refreshing' : ''}`}>
          <Suspense fallback={<LoadingState label="趨勢圖載入中…" />}>
            <TrustHistoryChart history={data.history} />
          </Suspense>
          <p className="mt-2 text-xs text-tf-muted">
            虛線為每日<GlossaryTerm term="confidenceInterval" label="完整度區間" compact />校準後的落點，區間越窄代表當天估計越穩定。
          </p>
        </div>
      )}
    </main>
  )
}
