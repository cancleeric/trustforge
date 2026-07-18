import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalysisJobStatus } from '../lib/endpoints'
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
  const [manualJob, setManualJob] = useState<AnalysisJobStatus | null>(null)
  const [requestNonce, setRequestNonce] = useState(0)
  const mode = searchParams.get('mode') || (params.type === 'hypothesis' ? 'fundamentals' : 'risk')
  const hasExplicitRequest = searchParams.has('q') || searchParams.get('sample') === '1'

  useEffect(() => {
    if (!hasExplicitRequest) {
      setLoading(false)
      setError(null)
      setData(null)
      return
    }
    setHologramData(data ? {
      analysis: data,
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
  }, [data, hasExplicitRequest, params.q, params.type, setHologramData])

  useEffect(() => {
    if (!hasExplicitRequest) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    setManualJob(null)
    if (params.sample) {
      void getAnalyze({ coin: params.coin, type: params.type, q: params.q, sample: params.sample }, controller.signal).then((res) => {
        if (controller.signal.aborted) return
        setLoading(false)
        if (res.ok) setData(res.data)
        else setError(res.error)
      })
      return () => controller.abort()
    }
    const poll = (jobId: string) => {
      void getAnalysisJob(jobId, controller.signal).then((res) => {
        if (controller.signal.aborted) return
        if (!res.ok) {
          setLoading(false)
          setError(res.error)
          return
        }
        setManualJob(res.data)
        if (res.data.state === 'completed' && res.data.result) {
          setData(res.data.result)
          setLoading(false)
        } else if (res.data.state === 'failed') {
          setLoading(false)
          setError({ code: 'analysis_failed', message: res.data.error || '分析工作失敗' })
        } else {
          window.setTimeout(() => poll(jobId), 1200)
        }
      })
    }
    // Explicit manual runs are durable high-priority jobs. The scheduler switch
    // controls only scheduled jobs, so it cannot disable this path.
    void registerAnalysisQuestion(params.coin, mode, params.q, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      if (!res.ok || !res.data.job_id) {
        setLoading(false)
        setError(res.ok ? { code: 'analysis_queue_unavailable', message: '分析工作尚未建立' } : res.error)
        return
      }
      poll(res.data.job_id)
    })
    return () => controller.abort()
  }, [hasExplicitRequest, mode, params.coin, params.q, params.sample, params.type, requestNonce])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setError(null), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  const handleSubmit = (values: QueryValues) => {
    const next = new URLSearchParams()
    const workspace = searchParams.get('workspace')
    if (workspace) next.set('workspace', workspace)
    next.set('coin', values.coin)
    next.set('type', values.type)
    next.set('q', values.q)
    next.set('mode', values.mode)
    if (params.sample) next.set('sample', params.sample)
    setSearchParams(next)
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
            {loading && !data && <LoadingState label={manualJob
              ? `手動優先處理中：${manualJob.current_stage}${manualJob.queue_position ? `（佇列第 ${manualJob.queue_position} 位）` : ''}`
              : `Hermes 正在建立 ${params.coin} 的手動分析工作…`} />}
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
                分析完成後會在此顯示本次 run 的報告、證據與執行紀錄。
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  )
}
