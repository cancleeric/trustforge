import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getComparisonSnapshot, getPeerMetrics, registerAnalysisComparison } from '../lib/endpoints'
import type { ComparisonParams } from '../lib/endpoints'
import type { ComparisonAnalyzeData, PeerComparisonEntry, PeerMetricsSnapshot } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import AnalysisReportView from '../components/AnalysisReportView'
import PeerComparisonTable from '../components/PeerComparisonTable'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'
import CoinSelect from '../components/CoinSelect'

/** 模組③ Wave 3：同層 peer 比較——獨立於雙幣分析比較流程，僅依「幣種
 * A」查詢，唯讀查詢、不觸發任何分析工作。目前 peer-metrics fixture 用
 * `asset:` 前綴的資產識別碼（例：`asset:eth`），這裡直接由 `COIN_POOL`
 * 代號小寫轉換猜測；查無資料時（如 fixture 尚未收錄的資產）API 本身
 * 回 `snapshot: null`，UI 顯示空狀態，不視為錯誤。 */
function PeerComparisonSection({ coin }: { coin: string }) {
  const assetId = `asset:${coin.toLowerCase()}`
  const [snapshot, setSnapshot] = useState<PeerMetricsSnapshot | null>(null)
  const [peers, setPeers] = useState<PeerComparisonEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getPeerMetrics(assetId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        if (response.ok) {
          setSnapshot(response.data.snapshot)
          setPeers(response.data.peers)
        } else if (response.error.code !== 'cancelled') {
          setError(response.error)
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError({ code: 'network_error', message: '連線異常，請稍後再試' })
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [assetId])

  if (loading) return <LoadingState label={`讀取 ${assetId} 同層比較資料中…`} />
  if (error) return <ErrorState code={error.code} message={error.message} />
  if (!snapshot) {
    return (
      <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4 text-sm text-tf-muted">
        目前無 {assetId} 的同層比較資料。
      </div>
    )
  }
  return <PeerComparisonTable snapshot={snapshot} peers={peers} />
}

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
  const userChangedRef = useRef(false)
  const sameCoin = coin === coin2

  // 瀏覽器上下頁（或外部導覽帶新的 coin/coin2/q 進來）：URL 是唯一事實來源，
  // 把整個表單（含 queryEdited 旗標）重新對齊，避免可見控件跟網址/報告脫節。
  // 只在使用者改動後 debounce 同步；初次 mount / URL 對齊不能自動 submit。
  useEffect(() => {
    setCoin(initial.coin)
    setCoin2(initial.coin2)
    setQ(initial.q)
    setQueryEdited(false)
    userChangedRef.current = false
  }, [initial.coin, initial.coin2, initial.q])

  useEffect(() => {
    if (!userChangedRef.current) return
    if (sameCoin || !q.trim()) return
    const timer = window.setTimeout(() => onSubmit({ coin, coin2, q: q.trim() }), 350)
    return () => window.clearTimeout(timer)
  }, [coin, coin2, onSubmit, q, sameCoin])

  function handleCoinChange(next: string) {
    userChangedRef.current = true
    setCoin(next)
    if (!queryEdited) setQ(defaultQuery(next, coin2))
  }

  function handleCoin2Change(next: string) {
    userChangedRef.current = true
    setCoin2(next)
    if (!queryEdited) setQ(defaultQuery(coin, next))
  }

  function handleQChange(next: string) {
    userChangedRef.current = true
    setQ(next)
    setQueryEdited(true)
  }

  return (
    <form
      className="hermes-clip flex flex-col gap-3 rounded-lg border border-tf-border bg-tf-card p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (sameCoin) return
        onSubmit({ coin, coin2, q })
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <CoinSelect id="cmp-coin" label="幣種 A" value={coin} onChange={handleCoinChange} />
        <CoinSelect id="cmp-coin2" label="幣種 B" value={coin2} onChange={handleCoin2Change} />
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
        className="rounded-[8px] px-5 py-[13px] text-[13px] font-bold tracking-[0.06em] text-tf-bg transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)', boxShadow: '0 0 20px rgba(77,216,224,0.25)' }}
      >
        比較分析 <span className="tf-num opacity-70">&#8629;</span>
      </button>
    </form>
  )
}

export default function ComparePage() {
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<ComparisonAnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [requestNonce, setRequestNonce] = useState(0)
  const hasExplicitRequest = searchParams.has('q')

  useEffect(() => {
    if (!hasExplicitRequest) {
      setLoading(false)
      setError(null)
      setData(null)
      return
    }
    setHologramData(data ? {
      primaryLabel: data.report_a.coin,
      secondaryLabel: data.report_b.coin,
      primaryValue: data.report_a.calibrated_confidence,
      secondaryValue: data.report_b.calibrated_confidence,
      total: data.evidence_a.length + data.evidence_b.length,
      trustScore: data.report_a.calibrated_confidence,
      componentScores: {
        reputation: data.trust_components_aggregate_a.reputation,
        corroboration: data.trust_components_aggregate_a.corroboration,
        recency: data.trust_components_aggregate_a.recency,
        resistance: data.trust_components_aggregate_a.manipulation == null ? null : 1 - data.trust_components_aggregate_a.manipulation,
      },
    } : null)
    return () => setHologramData(null)
  }, [data, hasExplicitRequest, setHologramData])

  useEffect(() => {
    if (!hasExplicitRequest) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    const read = () => getComparisonSnapshot(params, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        if (res.error.code === 'snapshot_pending') { setLoading(true); return }
        setError(res.error)
      }
    })
    void read()
    const poll = window.setInterval(() => void read(), 1500)
    return () => {
      window.clearInterval(poll)
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasExplicitRequest, params.coin, params.coin2, params.q, requestNonce])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setError(null), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  const handleSubmit = useCallback((values: FormState) => {
    const next = new URLSearchParams()
    const workspace = searchParams.get('workspace')
    if (workspace) next.set('workspace', workspace)
    next.set('coin', values.coin)
    next.set('coin2', values.coin2)
    next.set('q', values.q)
    setSearchParams(next)
    void registerAnalysisComparison(values)
    setRequestNonce((value) => value + 1)
  }, [searchParams, setSearchParams])

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Parallel Hermes runs</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">雙幣比較</h1>
        <p className="mt-1 text-sm text-tf-text2">
          兩份獨立分析並列呈現，各自的信任分/資訊完整度/證據互不干擾，供相對強弱比較。
        </p>
      </div>

      <CompareForm initial={{ coin: params.coin, coin2: params.coin2, q: params.q }} onSubmit={handleSubmit} />

      <PeerComparisonSection coin={params.coin} />

      {loading && !data && <LoadingState label={`讀取 ${params.coin} / ${params.coin2} 比較快照中…`} />}
      {loading && data && <div className="hermes-analysis-pending" role="status"><i />Hermes 正在更新比較快照；目前保留上一個完整結果。</div>}
      {!loading && error && !data && <ErrorState code={error.code} message={error.message} />}
      {!loading && !error && !data && (
        <div className="hermes-clip border border-tf-border bg-tf-card p-5 text-sm text-tf-text2">
          選擇兩個市場後按下「比較分析」。開啟工作區不會自動執行兩次分析。
        </div>
      )}
      {data && (
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
