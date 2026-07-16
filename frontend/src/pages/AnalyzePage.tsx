import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalysisSnapshot, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import QueryConsole, { type QueryValues } from '../components/QueryConsole'
import AnalysisReportView from '../components/AnalysisReportView'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'

function defaultQuery(coin: string): string {
  return `分析${coin}近期市場狀況，整合多源資料`
}

function paramsFromSearch(sp: URLSearchParams): AnalyzeParams {
  const coin = sp.get('coin') || COIN_POOL[0]
  const type = (sp.get('type') as AnalyzeParams['type']) || 'multi_source'
  const q = sp.get('q') || defaultQuery(coin)
  const sample = sp.get('sample') === '1' ? '1' : undefined
  return { coin, type, q, sample }
}

export default function AnalyzePage() {
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<AnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [requestNonce, setRequestNonce] = useState(0)
  const mode = searchParams.get('mode') || (params.type === 'hypothesis' ? 'fundamentals' : 'risk')
  const hasExplicitRequest = true

  useEffect(() => {
    if (!hasExplicitRequest) {
      setLoading(false)
      setError(null)
      setData(null)
      return
    }
    setHologramData(data ? {
      question: params.q,
      analysisMode: params.type,
      snapshotAt: data.report.generated_at,
      runId: data.execution?.run_id,
      primaryLabel: data.report.coin,
      primaryValue: data.report.calibrated_confidence,
      total: data.evidence.length,
      status: data.report.decision_state,
      trustScore: data.report.calibrated_confidence,
      componentScores: {
        reputation: data.trust_components_aggregate.reputation,
        corroboration: data.trust_components_aggregate.corroboration,
        recency: data.trust_components_aggregate.recency,
        resistance: data.trust_components_aggregate.manipulation == null ? null : 1 - data.trust_components_aggregate.manipulation,
      },
      pipelineStages: (data.execution?.nodes ?? []).slice().sort((a, b) => a.order - b.order).map((node) => {
        const events = data.execution_log.filter((event) => {
          const hermes = event.params.hermes
          return typeof hermes === 'object' && hermes !== null && 'node_id' in hermes && hermes.node_id === node.id
        })
        const states = events.map((event) => {
          const hermes = event.params.hermes
          return typeof hermes === 'object' && hermes !== null && 'status' in hermes ? hermes.status : undefined
        })
        const failed = states.includes('failed')
        const completed = states.includes('completed') || (node.id === 'report_delivery' && data.execution_log.some((event) => event.tool === 'report.done'))
        const elapsed = events.map((event) => event.elapsed_sec).filter(Number.isFinite)
        const duration = elapsed.length > 1 ? Math.max(...elapsed) - Math.min(...elapsed) : elapsed[0] ?? 0
        return {
          id: node.id,
          label: node.label,
          metric: String(events.length),
          unit: `事件 · ${duration.toFixed(2)}s`,
          status: failed ? 'failed' as const : completed ? 'completed' as const : 'pending' as const,
        }
      }),
    } : null)
    return () => setHologramData(null)
  }, [data, hasExplicitRequest, setHologramData])

  useEffect(() => {
    if (!hasExplicitRequest) return
    // 真的 abort 底層 fetch（不只是忽略回應）：參數變更/卸載時中止「已被
    // 取代」的舊請求，避免它晚到覆蓋新狀態（race），也省下無謂的等待。
    const controller = new AbortController()
    // Snapshot swap 必須原子化：保留上一個完整結果直到新結果抵達，避免
    // 中央、右欄與能量管線各自先清空再補回而連續閃爍。
    setLoading(true)
    setError(null)
    const read = () => getAnalysisSnapshot(params.coin, mode, controller.signal, params.q).then((res) => {
      // 已被取消（cleanup）——這是被取代的舊請求，靜默捨棄，不當錯誤
      // UI、不覆蓋新狀態。逾時（backend stall）不會走到這裡，會正常落到
      // 下面的 res.ok===false 分支顯示錯誤狀態。
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        if (res.error.code === 'snapshot_pending') {
          setLoading(true)
          return
        }
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
  }, [hasExplicitRequest, mode, params.coin, params.q, requestNonce])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setError(null), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  const handleSubmit = (values: QueryValues) => {
    const next: Record<string, string> = {
      coin: values.coin, type: values.type, q: values.q,
      mode: values.mode,
    }
    if (params.sample) next.sample = params.sample
    setSearchParams(next)
    void registerAnalysisQuestion(values.coin, next.mode, values.q.trim())
    setRequestNonce((value) => value + 1)
  }

  return (
    <main
      className="relative mx-auto max-w-7xl px-4 py-6 sm:px-6"
      style={{ background: 'radial-gradient(ellipse at 50% 0%,#0b1420 0%,#050810 70%)', minHeight: 'calc(100vh - 57px)' }}
    >
      <div
        className="pointer-events-none absolute inset-0 z-0"
        style={{ background: 'repeating-linear-gradient(rgba(255,255,255,.012) 0px,rgba(255,255,255,.012) 1px,transparent 1px,transparent 3px)' }}
      />
      <div className="relative z-10">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3 border-b border-tf-border pb-4">
        <div>
          <p className="font-mono text-xs font-semibold uppercase tracking-[1.6px] text-tf-link">Hermes analysis run</p>
          <h1 className="mt-1 text-2xl font-bold text-tf-text">分析工作區 <span className="text-tf-link">· BRIDGE</span></h1>
          <p className="mt-1 text-sm text-tf-text2">每次執行固定一個 run，保留來源、節點、證據與輸出供後續稽核。</p>
        </div>
        <p className="font-mono text-xs text-tf-muted">asset: {params.coin} · mode: {params.type}</p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[288px_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-4 lg:self-start">
        <QueryConsole initial={{ coin: params.coin, type: params.type, mode, q: params.q }} onSubmit={handleSubmit} />
      </aside>

      <section className="flex flex-col gap-4">
        {loading && !data && <LoadingState label={`讀取 ${params.coin} 分析快照中…`} />}
        {loading && data && (
          <div className="hermes-analysis-pending" role="status" aria-live="polite">
            <i /> Hermes 正在分析新的資料快照；目前保留顯示上一個完整結果，完成後會一次切換。
          </div>
        )}
        {!loading && error && !data && <ErrorState code={error.code} message={error.message} />}
        {data && (
          <div key={data.execution?.run_id ?? `${data.report.coin}-${data.report.generated_at}`} className="hermes-data-swap" aria-busy={loading}>
            <AnalysisReportView data={data} />
          </div>
        )}
        {!loading && !error && !data && (
          <div className="hermes-clip border border-tf-border bg-tf-card p-5 text-sm text-tf-text2">
            Hermes 正在載入這組幣種、模式與題目的最新預分析快照；此按鈕只供立即重跑。
          </div>
        )}
      </section>
      </div>
      </div>
    </main>
  )
}
