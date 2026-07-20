import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalysisJobStatus } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { BEGINNER_INTENTS, beginnerQuestion, beginnerTypeForMode, type BeginnerIntent } from '../lib/beginnerExperience'
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

function normalizeJobPart(value: string): string {
  return value.trim().toLowerCase()
}

function jobMatchesRequest(job: AnalysisJobStatus, coin: string, mode: string, question: string): boolean {
  return normalizeJobPart(job.coin) === normalizeJobPart(coin)
    && normalizeJobPart(job.mode) === normalizeJobPart(mode)
    && job.question.trim() === question.trim()
}

function useEmbeddedMobileComposer(embedded: boolean): boolean {
  const [mobile, setMobile] = useState(false)

  useEffect(() => {
    if (!embedded || typeof window.matchMedia !== 'function') {
      setMobile(false)
      return
    }
    const query = window.matchMedia('(max-width: 560px)')
    const sync = () => setMobile(query.matches)
    sync()
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', sync)
      return () => query.removeEventListener('change', sync)
    }
    query.addListener(sync)
    return () => query.removeListener(sync)
  }, [embedded])

  return mobile
}

export default function AnalyzePage({ embedded = false }: { embedded?: boolean } = {}) {
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<AnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [manualJob, setManualJob] = useState<AnalysisJobStatus | null>(null)
  const [requestNonce, setRequestNonce] = useState(0)
  const processedRequestKeys = useRef(new Set<string>())
  const [focusCoin, setFocusCoin] = useState(params.coin)
  const [focusIntent, setFocusIntent] = useState<BeginnerIntent>('trust')
  const mode = searchParams.get('mode') || (params.type === 'hypothesis' ? 'fundamentals' : 'risk')
  const hasExplicitRequest = searchParams.has('q') || searchParams.get('sample') === '1'
  const focusMode = !embedded && searchParams.get('focus') === '1'
  const urlJobId = searchParams.get('job') || ''
  const embeddedMobileComposer = useEmbeddedMobileComposer(embedded)
  const showComposer = !focusMode && (!embedded || embeddedMobileComposer)

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
      trustScore: data.report.confidence,
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
    const poll = (jobId: string, fromUrl = false) => {
      void getAnalysisJob(jobId, controller.signal).then((res) => {
        if (controller.signal.aborted) return
        if (!res.ok) {
          setLoading(false)
          setError(res.error)
          return
        }
        if (fromUrl && !jobMatchesRequest(res.data, params.coin, mode, params.q)) {
          setLoading(false)
          setError({ code: 'analysis_job_mismatch', message: 'URL job does not match this analysis request' })
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
          window.setTimeout(() => poll(jobId, fromUrl), 1200)
        }
      })
    }
    if (urlJobId) {
      poll(urlJobId, true)
      return () => controller.abort()
    }
    const requestKey = `${params.coin}\n${mode}\n${params.q}\n${params.sample ?? ''}\n${requestNonce}`
    if (requestNonce === 0 && processedRequestKeys.current.has(requestKey)) {
      setLoading(false)
      return () => controller.abort()
    }
    processedRequestKeys.current.add(requestKey)
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
  }, [hasExplicitRequest, mode, params.coin, params.q, params.sample, params.type, requestNonce, urlJobId])

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

  const handleFocusStart = () => {
    const intent = BEGINNER_INTENTS.find((item) => item.id === focusIntent) ?? BEGINNER_INTENTS[0]
    const next = new URLSearchParams()
    next.set('coin', focusCoin)
    next.set('type', beginnerTypeForMode(intent.mode))
    next.set('q', beginnerQuestion(focusCoin, intent.id))
    next.set('mode', intent.mode)
    next.set('focus', '1')
    setSearchParams(next)
    setRequestNonce((value) => value + 1)
  }

  const showFullAnalysis = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('focus')
    setSearchParams(next)
  }

  return (
    <main
      className="relative mx-auto max-w-7xl px-4 py-6 sm:px-6"
      style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}
    >
      <div
        className="pointer-events-none absolute inset-0 z-0"
        style={{ background: 'repeating-linear-gradient(rgba(255,255,255,.012) 0px,rgba(255,255,255,.012) 1px,transparent 1px,transparent 3px)' }}
      />
      <div className="relative z-10">
        {!focusMode && <div className="mb-5 flex flex-wrap items-end justify-between gap-3 border-b border-tf-border pb-4">
          <div>
            <p className="font-mono text-xs font-semibold uppercase tracking-[1.6px] text-tf-link">Hermes analysis run</p>
            <h1 className="mt-1 text-2xl font-bold text-tf-text">分析工作區 <span className="text-tf-link">· BRIDGE</span></h1>
            <p className="mt-1 text-sm text-tf-text2">每次執行固定一個 run，保留來源、節點、證據與輸出供後續稽核。</p>
          </div>
          <p className="font-mono text-xs text-tf-muted">asset: {params.coin} · mode: {params.type}</p>
        </div>}

        {focusMode && !data && !loading && !error && (
          <section className="mx-auto max-w-3xl rounded-lg border border-tf-border bg-tf-card p-5 sm:p-6">
            <p className="text-xs font-semibold uppercase text-tf-link">首次試用</p>
            <h1 className="mt-2 text-2xl font-bold text-tf-text">先完成一次清楚的市場判讀</h1>
            <div className="mt-5 grid gap-4">
              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-tf-text">1. 選擇想了解的資產</span>
                <select
                  value={focusCoin}
                  onChange={(event) => setFocusCoin(event.target.value)}
                  className="w-full rounded border border-tf-border bg-tf-bg px-3 py-2 text-sm text-tf-text"
                >
                  {COIN_POOL.map((coin) => <option key={coin} value={coin}>{coin}</option>)}
                </select>
              </label>
              <fieldset>
                <legend className="mb-2 text-sm font-semibold text-tf-text">2. 選擇想解決的問題</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {BEGINNER_INTENTS.map((intent) => (
                    <label key={intent.id} className={`rounded-md border p-3 text-sm ${focusIntent === intent.id ? 'border-tf-link bg-tf-accent/20 text-tf-text' : 'border-tf-border bg-tf-bg text-tf-text2'}`}>
                      <input
                        type="radio"
                        name="beginner-intent"
                        value={intent.id}
                        checked={focusIntent === intent.id}
                        onChange={() => setFocusIntent(intent.id)}
                        className="mr-2"
                      />
                      <span className="font-semibold">{intent.label}</span>
                      <span className="mt-1 block text-xs text-tf-muted">{intent.description}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
                3. 開始後會整理近期資料，給你一句結論、三個原因、目前限制與下一步。
              </div>
              <button
                type="button"
                onClick={handleFocusStart}
                className="rounded-[8px] px-5 py-[13px] text-[13px] font-bold tracking-[0.06em] text-tf-bg hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)', boxShadow: '0 0 20px rgba(77,216,224,0.25)' }}
              >
                開始第一次分析 <span className="tf-num opacity-70">&#8594;</span>
              </button>
            </div>
          </section>
        )}

        {(!focusMode || loading || error || data) && <div className={`grid grid-cols-1 gap-5${!searchParams.get('workspace') && !focusMode ? ' lg:grid-cols-[288px_minmax(0,1fr)]' : ''}`}>
          {/* 桌面 embedded 時（workspace 參數存在 + 左欄可見），隱藏 QueryConsole 避免重複 composer。
              手機 ≤560px 左欄被 CSS 隱藏，此時 QueryConsole 必須保留作為唯一入口。 */}
          {showComposer && <aside className={`lg:sticky lg:top-4 lg:self-start${(embedded || searchParams.get('workspace')) ? ' hidden-when-left-rail-visible' : ''}`}>
            <QueryConsole initial={{ coin: params.coin, type: params.type, mode, q: params.q }} onSubmit={handleSubmit} disabled={loading} />
          </aside>}

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
            {data && focusMode && (
              <BeginnerResult data={data} onShowFullAnalysis={showFullAnalysis} />
            )}
            {data && !focusMode && (
              <div key={data.execution?.run_id ?? `${data.report.coin}-${data.report.generated_at}`} className="hermes-data-swap" aria-busy={loading}>
                <AnalysisReportView data={data} mode={params.type} />
              </div>
            )}
            {!loading && !error && !data && (
              <div className="hermes-clip border border-tf-border bg-tf-card p-5 text-sm text-tf-text2">
                分析完成後會在此顯示本次 run 的報告、證據與執行紀錄。
              </div>
            )}
          </section>
        </div>}
      </div>
    </main>
  )
}

function BeginnerResult({ data, onShowFullAnalysis }: { data: AnalyzeData; onShowFullAnalysis: () => void }) {
  const reasons = data.report.key_basis.map((item) => item.claim || item.explanation).filter(Boolean).slice(0, 3)
  const fallbackReasons = data.report.facts.concat(data.report.inferences).slice(0, 3)
  const shownReasons = reasons.length ? reasons : fallbackReasons
  const limit = data.report.limits[0] || '目前資料仍可能不足，請把結論視為需要持續追蹤的判讀。'
  const nextStep = data.report.could_flip[0] || data.report.contrarian[0] || '保留觀察，等更多來源更新後再重新分析。'
  return (
    <article className="mx-auto max-w-3xl rounded-lg border border-tf-border bg-tf-card p-5 sm:p-6">
      <p className="text-xs font-semibold uppercase text-tf-link">首次分析結果</p>
      <h2 className="mt-2 text-2xl font-bold text-tf-text">{data.report.coin}：{data.report.market_judgment}</h2>
      <div className="mt-4 rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
        目前狀態：{data.report.decision_state === 'abstain' ? '資料不足，暫不判定' : data.report.direction || '需要持續觀察'}
      </div>
      <section className="mt-5">
        <h3 className="text-sm font-semibold text-tf-text">三個原因</h3>
        <ol className="mt-2 grid gap-2 text-sm text-tf-text2">
          {shownReasons.map((reason, index) => <li key={index} className="rounded-md border border-tf-border bg-tf-bg p-3">{reason}</li>)}
        </ol>
      </section>
      <section className="mt-5 rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
        <p><span className="font-semibold text-tf-text">限制：</span>{limit}</p>
        <p className="mt-2"><span className="font-semibold text-tf-text">下一步：</span>{nextStep}</p>
      </section>
      <div className="mt-5 flex flex-wrap gap-3">
        <button type="button" onClick={onShowFullAnalysis} className="rounded-md border border-tf-border px-4 py-2 text-sm font-semibold text-tf-text hover:text-tf-link">
          查看完整分析
        </button>
        <a href="/analyze" className="rounded-md border border-tf-border px-4 py-2 text-sm font-semibold text-tf-text2 hover:text-tf-link">
          再做一次簡化分析
        </a>
      </div>
    </article>
  )
}
