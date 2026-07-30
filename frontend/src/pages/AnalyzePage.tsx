import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { currentNarrativeLocale, getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalysisJobStatus } from '../lib/endpoints'
import { beginFormalRunIntent, completeFormalRunIntent, formalRunIntent } from '../lib/formalRun'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import { BEGINNER_INTENTS, beginnerQuestion, beginnerTypeForMode, type BeginnerIntent } from '../lib/beginnerExperience'
import QueryConsole, { type QueryValues } from '../components/QueryConsole'
import AnalysisReportView from '../components/AnalysisReportView'
import { ErrorState, LoadingState } from '../components/StatusStates'
import { useBridgeHologram } from '../components/BridgeHologramContext'
import { useHermesI18n } from '../hermes/hermesI18n'
import MultiAngleOverview from '../hermes/MultiAngleOverview'

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

// N9/point4 fix: 手動分析的 job_id 存進 sessionStorage（依 coin+mode+question 為
// key），reload 頁面時可以接回同一個工作，而不是重新排一個新的。故意不寫回
// URL/searchParams——那會讓 `urlJobId` 這個 effect 依賴值跟著變、把整個輪詢
// effect 重新觸發一次，導致多跑一輪 submit/poll、UI 短暫閃爍。
function manualJobStorageKey(coin: string, mode: string, question: string): string {
  return `hermes:analyze-job:${normalizeJobPart(coin)}|${normalizeJobPart(mode)}|${question.trim()}`
}

function readStoredJobId(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStoredJobId(key: string, jobId: string): void {
  try {
    window.sessionStorage.setItem(key, jobId)
  } catch {
    // sessionStorage 可能不可用（隱私模式等）——job 追蹤退化成不可持久化，不影響本次流程。
  }
}

function clearStoredJobId(key: string): void {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // The unresolved formal intent remains authoritative even if cleanup fails.
  }
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

export default function AnalyzePage({ embedded = false, onBusyChange, resubmitSignal }: { embedded?: boolean; onBusyChange?: (busy: boolean) => void; resubmitSignal?: number } = {}) {
  const { t } = useHermesI18n()
  const { setData: setHologramData } = useBridgeHologram()
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<AnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [manualJob, setManualJob] = useState<AnalysisJobStatus | null>(null)
  const [requestNonce, setRequestNonce] = useState(0)
  const [freshIntentNonce, setFreshIntentNonce] = useState<number | null>(null)
  const [transportRetryNonce, setTransportRetryNonce] = useState(0)
  // N13 fix: the embedded host (HermesDashboard's own left-rail "立即重新
  // 分析" button) doesn't go through `handleSubmit` below — it drives the
  // shared URL search params directly (see HermesModuleDeck/HermesDashboard).
  // When the question text is unchanged, that produces a byte-identical
  // query string, so React Router never re-renders with new param values
  // and the polling effect's dependency array never changes — no new POST
  // ever fires, and the screen just keeps showing the previous run's stale
  // report with no indication it's stale. `resubmitSignal` is a plain
  // counter the host bumps on every explicit click (independent of URL
  // content) so we can force a real resubmit here. It intentionally does
  // NOT persist across a real page reload (host state resets to its
  // initial value on remount, same as `requestNonce`), so N9's
  // reload-reconnects-to-the-same-job behavior is untouched.
  const prevResubmitSignal = useRef(resubmitSignal)
  useEffect(() => {
    if (resubmitSignal !== undefined && resubmitSignal !== prevResubmitSignal.current) {
      prevResubmitSignal.current = resubmitSignal
      const nextNonce = requestNonce + 1
      setFreshIntentNonce(nextNonce)
      setRequestNonce(nextNonce)
    }
  }, [requestNonce, resubmitSignal])
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
    const timers: number[] = []
    const setTrackedTimeout = (fn: () => void, delay: number) => {
      const id = window.setTimeout(fn, delay)
      timers.push(id)
      return id
    }
    setLoading(true)
    setError(null)
    setManualJob(null)
    // 後端 `_install_request_limit` 併發上限觸發時回 503 `server_busy`
    // （固定 `Retry-After: 2`）——這是暫時性壅塞，不是真的失敗，所以先做
    // 有限次數的指數退避重試，而不是立刻把使用者丟進錯誤態。
    const BUSY_BASE_DELAY_MS = 2000
    const MAX_BUSY_RETRIES = 5
    // N8 fix: 終止輪詢的條件是後端回報的 `state`（completed/failed），不是
    // 一個寫死的秒數——job 在 `trust_reasoning` 等階段跑得比較久是正常的，
    // 不代表後端死掉。`INACTIVITY_TIMEOUT_MS` 只當「後端完全沒回應」的保
    // 險絲：只有連續拿不到成功回應（每次成功回應都會把 `lastContactAt`
    // 往後推）超過這個時間，才判定逾時；job 只要還活著（持續有成功回應）
    // 就一直等下去，不會被硬砍。
    const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000
    let lastContactAt = Date.now()
    if (params.sample) {
      const runSample = (attempt: number) => {
        void getAnalyze({ coin: params.coin, type: params.type, q: params.q, sample: params.sample }, controller.signal).then((res) => {
          if (controller.signal.aborted) return
          if (!res.ok && res.error.code === 'server_busy' && attempt < MAX_BUSY_RETRIES) {
            setTrackedTimeout(() => runSample(attempt + 1), BUSY_BASE_DELAY_MS * 2 ** attempt)
            return
          }
          setLoading(false)
          if (res.ok) setData(res.data)
          else setError(res.error)
        })
      }
      runSample(0)
      return () => { controller.abort(); timers.forEach(window.clearTimeout) }
    }
    const poll = (jobId: string, fromUrl = false, formalKey?: string) => {
      void getAnalysisJob(jobId, controller.signal).then((res) => {
        if (controller.signal.aborted) return
        if (!res.ok) {
          if (Date.now() - lastContactAt < INACTIVITY_TIMEOUT_MS) {
            // 後端還在（或至少最近才失聯），無論是 server_busy 還是短暫的
            // network hiccup，都先重試，而不是把還活著的 job 判死刑。
            setTrackedTimeout(() => poll(jobId, fromUrl, formalKey), BUSY_BASE_DELAY_MS)
            return
          }
          setLoading(false)
          setError({ code: 'analysis_timeout', message: '分析工作長時間沒有回應，後端可能已中斷，請稍後再確認工作狀態' })
          return
        }
        lastContactAt = Date.now()
        if (fromUrl && !jobMatchesRequest(res.data, params.coin, mode, params.q)) {
          setLoading(false)
          setError({ code: 'analysis_job_mismatch', message: 'URL job does not match this analysis request' })
          return
        }
        setManualJob(res.data)
        if (res.data.state === 'completed' && res.data.result) {
          if (formalKey) completeFormalRunIntent('analyze', formalKey)
          setData(res.data.result)
          setLoading(false)
        } else if (res.data.state === 'failed') {
          if (formalKey) completeFormalRunIntent('analyze', formalKey)
          setLoading(false)
          setError({ code: 'analysis_failed', message: res.data.error || '分析工作失敗' })
        } else {
          // queued / running：job 還活著，持續輪詢，不設人為秒數上限（見 N8）。
          setTrackedTimeout(() => poll(jobId, fromUrl, formalKey), 1200)
        }
      })
    }
    // N9/point4 fix: 沒有明確的 `?job=` URL 參數時，第一次載入（requestNonce
    // === 0，代表不是使用者手動重新送出）也試著從 sessionStorage 接回同一
    // coin/mode/question 的既有 job，而不是無條件重新排一個新的。
    const storageKey = manualJobStorageKey(params.coin, mode, params.q)
    const reconnectJobId = urlJobId || (requestNonce === 0 && !params.sample ? readStoredJobId(storageKey) : null)
    if (reconnectJobId) {
      poll(reconnectJobId, true)
      return () => { controller.abort(); timers.forEach(window.clearTimeout) }
    }
    const requestKey = `${params.coin}\n${mode}\n${params.q}\n${params.sample ?? ''}\n${requestNonce}\n${transportRetryNonce}`
    if (requestNonce === 0 && processedRequestKeys.current.has(requestKey)) {
      setLoading(false)
      return () => { controller.abort(); timers.forEach(window.clearTimeout) }
    }
    // Explicit manual runs are durable high-priority jobs. The scheduler switch
    // controls only scheduled jobs, so it cannot disable this path.
    //
    // `processedRequestKeys` exists to stop React StrictMode's dev-only
    // double effect invocation (mount → cleanup → mount) from submitting
    // the same manual request twice. We claim the key up front so the
    // second (persisting) invocation doesn't also submit — but the claim
    // must be released on cleanup if this attempt never got to actually
    // settle (i.e. it's the throwaway first invocation, whose controller
    // gets aborted before the request resolves). Claiming permanently
    // regardless of outcome previously meant: the first invocation fires
    // the real POST, gets aborted mid-flight and its result silently
    // discarded (see the `aborted` guard below), while the second,
    // persisting invocation saw the key as "already handled" and bailed
    // out via the branch above — never submitting for real, no retry, no
    // error, stuck in loading forever (see D2).
    let requestSettled = false
    const processedKeys = processedRequestKeys.current
    processedKeys.add(requestKey)
    const locale = currentNarrativeLocale()
    // The nonce is part of the browser-side intent identity: StrictMode and
    // transport retries keep the same key, while each explicit rerun gets a
    // distinct key even when every visible field is unchanged.
    const visibleIntent = formalRunIntent(params.coin, mode, params.q, locale)
    const activeIntent = beginFormalRunIntent(
      'analyze',
      visibleIntent,
      `${requestNonce}`,
      freshIntentNonce === requestNonce,
      requestNonce === 0,
    )
    const { fresh, key: idempotencyKey } = activeIntent
    if (fresh) clearStoredJobId(storageKey)
    const submit = (attempt: number) => {
      void registerAnalysisQuestion(
        params.coin,
        mode,
        params.q,
        idempotencyKey,
        fresh,
        controller.signal,
        locale,
      ).then((res) => {
        if (controller.signal.aborted) return
        requestSettled = true
        if (!res.ok || !res.data.job_id) {
          if (!res.ok && res.error.code === 'server_busy' && attempt < MAX_BUSY_RETRIES) {
            setTrackedTimeout(() => submit(attempt + 1), BUSY_BASE_DELAY_MS * 2 ** attempt)
            return
          }
          setLoading(false)
          setError(res.ok ? { code: 'analysis_queue_unavailable', message: t('analysisQueueUnavailable') } : res.error)
          return
        }
        // N9/point4 fix: 把拿到的 job_id 存進 sessionStorage，reload 頁面時
        // 由上面的 `reconnectJobId` 接回同一個工作（見 jobMatchesRequest）。
        writeStoredJobId(storageKey, res.data.job_id)
        poll(res.data.job_id, false, idempotencyKey)
      })
    }
    submit(0)
    return () => {
      controller.abort()
      timers.forEach(window.clearTimeout)
      if (!requestSettled) processedKeys.delete(requestKey)
    }
    // `t` intentionally excluded: this effect fires the manual-analysis request, and
    // re-running it on a locale switch would resubmit an in-flight request. `t()`
    // above is only used to build a one-off error message, never part of the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [freshIntentNonce, hasExplicitRequest, mode, params.coin, params.q, params.sample, params.type, requestNonce, transportRetryNonce, urlJobId])

  useEffect(() => {
    if (error?.code !== 'network_error') return
    const timer = window.setTimeout(() => setError(null), 1800)
    return () => window.clearTimeout(timer)
  }, [error])

  // N2 fix: embedded host (HermesDashboard's left-rail submit button) tracks
  // its own independent `phase` state, previously only reset to 'ready' when
  // `data` (success) landed via BridgeHologramContext. On an error outcome
  // `data` never arrives, so the host stayed stuck showing its loading label
  // forever. `loading` here already flips to false on every settle path
  // (success, error, or cleared request — see the effect above), so it's a
  // reliable "still busy" signal to forward regardless of outcome.
  useEffect(() => {
    onBusyChange?.(loading)
    return () => onBusyChange?.(false)
  }, [loading, onBusyChange])

  const navigate = useNavigate()

  const handleSubmit = (values: QueryValues) => {
    const next = new URLSearchParams()
    const workspace = searchParams.get('workspace')
    // PLAN §7：比較分析是雙幣題型，不能當單幣分析送出去。嵌在 HERMES 工作區
    // 時切 workspace，獨立頁時導到 /compare。
    if (values.type === 'comparison') {
      const compare = new URLSearchParams({ coin: values.coin, q: values.q })
      if (workspace) {
        compare.set('workspace', 'compare')
        setSearchParams(compare)
      } else {
        navigate({ pathname: '/compare', search: compare.toString() })
      }
      return
    }
    if (workspace) next.set('workspace', workspace)
    next.set('coin', values.coin)
    next.set('type', values.type)
    next.set('q', values.q)
    next.set('mode', values.mode)
    if (params.sample) next.set('sample', params.sample)
    setSearchParams(next)
    const nextNonce = requestNonce + 1
    setFreshIntentNonce(hasExplicitRequest ? nextNonce : null)
    setRequestNonce(nextNonce)
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
    setFreshIntentNonce(null)
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
            <h1 className="mt-1 text-2xl font-bold text-tf-text">{t('analyzeWorkspaceTitle')} <span className="text-tf-link">· BRIDGE</span></h1>
            <p className="mt-1 text-sm text-tf-text2">{t('analyzeWorkspaceSubtitle')}</p>
          </div>
          <p className="font-mono text-xs text-tf-muted">asset: {params.coin} · mode: {params.type}</p>
        </div>}

        {focusMode && !data && !loading && !error && (
          <section className="mx-auto max-w-3xl rounded-lg border border-tf-border bg-tf-card p-5 sm:p-6">
            <p className="text-xs font-semibold uppercase text-tf-link">{t('firstRunEyebrow')}</p>
            <h1 className="mt-2 text-2xl font-bold text-tf-text">{t('firstRunTitle')}</h1>
            <div className="mt-5 grid gap-4">
              <label className="block">
                <span className="mb-2 block text-sm font-semibold text-tf-text">{t('firstRunStep1')}</span>
                <select
                  value={focusCoin}
                  onChange={(event) => setFocusCoin(event.target.value)}
                  className="w-full rounded border border-tf-border bg-tf-bg px-3 py-2 text-sm text-tf-text"
                >
                  {COIN_POOL.map((coin) => <option key={coin} value={coin}>{coin}</option>)}
                </select>
              </label>
              <fieldset>
                <legend className="mb-2 text-sm font-semibold text-tf-text">{t('firstRunStep2')}</legend>
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
                      <span className="font-semibold">{t(intent.labelKey)}</span>
                      <span className="mt-1 block text-xs text-tf-muted">{t(intent.descriptionKey)}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
                {t('firstRunStep3')}
              </div>
              <button
                type="button"
                onClick={handleFocusStart}
                className="rounded-[8px] px-5 py-[13px] text-[13px] font-bold tracking-[0.06em] text-tf-bg hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)', boxShadow: '0 0 20px rgba(77,216,224,0.25)' }}
              >
                {t('firstRunStart')} <span className="tf-num opacity-70">&#8594;</span>
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
              ? `${t('manualPriorityPrefix')}${manualJob.current_stage}${manualJob.queue_position ? `${t('queuePositionPrefix')}${manualJob.queue_position}${t('queuePositionSuffix')}` : ''}`
              : t('creatingManualJob', { coin: params.coin })} />}
            {loading && data && (
              <div className="hermes-analysis-pending" role="status" aria-live="polite">
                <i /> {t('analyzingNewSnapshot')}
              </div>
            )}
            {!loading && error && !data && <ErrorState code={error.code} message={error.message} onRetry={() => setTransportRetryNonce((value) => value + 1)} />}
            {data && focusMode && (
              <BeginnerResult data={data} onShowFullAnalysis={showFullAnalysis} />
            )}
            {data && !focusMode && (
              <div key={data.execution?.run_id ?? `${data.report.coin}-${data.report.generated_at}`} className="hermes-data-swap" aria-busy={loading}>
                <AnalysisReportView data={data} mode={params.type} />
              </div>
            )}
            {!focusMode && (
              <MultiAngleOverview
                coin={params.coin}
                snapshotId={searchParams.get('snapshot') || undefined}
              />
            )}
            {!loading && !error && !data && (
              <div className="hermes-clip border border-tf-border bg-tf-card p-5 text-center" role="status">
                <p className="text-base font-semibold text-tf-text">{t('noAnalysisData')}</p>
                <p className="mt-2 text-sm text-tf-text2">
                  {hasExplicitRequest
                    ? t('noAnalysisDataPendingHint')
                    : t('noAnalysisDataEmptyHint')}
                </p>
              </div>
            )}
          </section>
        </div>}
      </div>
    </main>
  )
}

function BeginnerResult({ data, onShowFullAnalysis }: { data: AnalyzeData; onShowFullAnalysis: () => void }) {
  const { t } = useHermesI18n()
  const reasons = data.report.key_basis.map((item) => item.claim || item.explanation).filter(Boolean).slice(0, 3)
  const fallbackReasons = data.report.facts.concat(data.report.inferences).slice(0, 3)
  const shownReasons = reasons.length ? reasons : fallbackReasons
  const limit = data.report.limits[0] || t('beginnerResultDefaultLimit')
  const nextStep = data.report.could_flip[0] || data.report.contrarian[0] || t('beginnerResultDefaultNextStep')
  return (
    <article className="mx-auto max-w-3xl rounded-lg border border-tf-border bg-tf-card p-5 sm:p-6">
      <p className="text-xs font-semibold uppercase text-tf-link">{t('beginnerResultEyebrow')}</p>
      <h2 className="mt-2 text-2xl font-bold text-tf-text">{data.report.coin}：{data.report.market_judgment}</h2>
      <div className="mt-4 rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
        {t('beginnerResultCurrentState')}{data.report.decision_state === 'abstain' ? t('beginnerResultInsufficientData') : data.report.direction || t('beginnerResultNeedsWatching')}
      </div>
      <section className="mt-5">
        <h3 className="text-sm font-semibold text-tf-text">{t('beginnerResultReasonsTitle')}</h3>
        <ol className="mt-2 grid gap-2 text-sm text-tf-text2">
          {shownReasons.map((reason, index) => <li key={index} className="rounded-md border border-tf-border bg-tf-bg p-3">{reason}</li>)}
        </ol>
      </section>
      <section className="mt-5 rounded-md border border-tf-border bg-tf-bg p-3 text-sm text-tf-text2">
        <p><span className="font-semibold text-tf-text">{t('beginnerResultLimitLabel')}</span>{limit}</p>
        <p className="mt-2"><span className="font-semibold text-tf-text">{t('beginnerResultNextStepLabel')}</span>{nextStep}</p>
      </section>
      <div className="mt-5 flex flex-wrap gap-3">
        <button type="button" onClick={onShowFullAnalysis} className="rounded-md border border-tf-border px-4 py-2 text-sm font-semibold text-tf-text hover:text-tf-link">
          {t('beginnerResultShowFull')}
        </button>
        <a href="/analyze" className="rounded-md border border-tf-border px-4 py-2 text-sm font-semibold text-tf-text2 hover:text-tf-link">
          {t('beginnerResultRedo')}
        </a>
      </div>
    </article>
  )
}
