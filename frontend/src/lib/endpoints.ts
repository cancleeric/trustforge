import { ANALYZE_TIMEOUT_MS, apiFetch, DEFAULT_TIMEOUT_MS, REGISTER_TIMEOUT_MS } from './apiClient'
import {
  isAdminAuditData,
  isAdminBackendProvidersData,
  isAdminConfigData,
  isAnalyzeData,
  isAssetContextResponseData,
  isPeerMetricsResponseData,
  isEcoLinkResponseData,
  isComparisonAnalyzeData,
  isCostsData,
  isHealthData,
  isHistoryData,
  isOverviewData,
  isStatusData,
} from './validators'
import { isWhaleAlertCredentialStatus } from './validators'
import type { WhaleAlertCredentialStatus } from './types'
import type {
  AdminAuditData,
  AdminBackendProvidersData,
  AdminConfigChanges,
  AdminConfigData,
  AnalyzeData,
  ApiEnvelope,
  AssetContextResponseData,
  PeerMetricsResponseData,
  EcoLinkResponseData,
  BackendProvider,
  BackendProviderKey,
  ComparisonAnalyzeData,
  CostsData,
  HealthData,
  HistoryData,
  OverviewData,
  StatusData,
} from './types'
import { isFormalRunReceipt, type FormalRunReceipt } from './formalRun'

/**
 * `signal` 建議由呼叫端的 React effect 傳入（effect cleanup 時
 * `controller.abort()`），中止「已被取代」的請求，避免舊回應晚到覆蓋新
 * 狀態（race）。逾時另由 apiFetch 內部計時觸發，見 apiClient.ts。
 */
export function getOverview(signal?: AbortSignal): Promise<ApiEnvelope<OverviewData>> {
  return apiFetch<OverviewData>('/api/overview', undefined, isOverviewData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export interface AgentCoreStatusData {
  provider: 'builtin' | 'agentcore'
  selected: boolean
  runtime_configured: boolean
  state: 'inactive' | 'configured' | 'misconfigured'
}

export function getAgentCoreStatus(
  signal?: AbortSignal,
): Promise<ApiEnvelope<AgentCoreStatusData>> {
  const valid = (value: unknown): value is AgentCoreStatusData => {
    if (!value || typeof value !== 'object') return false
    const data = value as AgentCoreStatusData
    return (
      (data.provider === 'builtin' || data.provider === 'agentcore') &&
      typeof data.selected === 'boolean' &&
      typeof data.runtime_configured === 'boolean' &&
      ['inactive', 'configured', 'misconfigured'].includes(data.state)
    )
  }
  return apiFetch<AgentCoreStatusData>(
    '/api/agentcore/status',
    undefined,
    valid,
    { signal, timeoutMs: DEFAULT_TIMEOUT_MS },
  )
}

export interface AnalyzeParams {
  coin: string
  type: 'multi_source' | 'hypothesis' | 'comparison'
  q: string
  coin2?: string
  /** 範例模式：讀既有 sample fixture，不觸發真連接器（credit-safe）。 */
  sample?: '1'
}

// 分析端點可能觸發真連接器（多來源蒐集+推論），比 overview/health 讀
// cache 慢得多，逾時給較長的 ANALYZE_TIMEOUT_MS，避免真分析還沒跑完就被
// 誤判逾時。
export function getAnalyze(params: AnalyzeParams, signal?: AbortSignal): Promise<ApiEnvelope<AnalyzeData>> {
  return apiFetch<AnalyzeData>('/api/analyze', { ...params }, isAnalyzeData, {
    signal,
    timeoutMs: ANALYZE_TIMEOUT_MS,
  })
}

/** Read an already-published Hermes result. This endpoint never starts work. */
export function getAnalysisSnapshot(coin: string, mode: string, signal?: AbortSignal, q?: string): Promise<ApiEnvelope<AnalyzeData>> {
  return apiFetch<AnalyzeData>('/api/analysis-snapshot', { coin, mode, q }, isAnalyzeData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export type AnalysisQuestionReceipt = FormalRunReceipt

/** Narrative locale contract of `POST /api/analysis-question` (N11). */
export type NarrativeLocale = 'zh-Hant' | 'en'

// The UI locale (`hermesI18n.tsx`) is `zh-TW` | `en`; the API contract is
// `zh-Hant` | `en`.  `HermesI18nProvider.setLocale` persists the current UI
// language into this cookie, so reading it here keeps `registerAnalysisQuestion`
// a plain function (no React hook) while still following the live selection.
export function currentNarrativeLocale(): NarrativeLocale {
  const saved = typeof document === 'undefined'
    ? undefined
    : document.cookie.split('; ').find((item) => item.startsWith('trustforge_hermes_locale='))?.split('=')[1]
  return saved === 'en' ? 'en' : 'zh-Hant'
}

export function registerAnalysisQuestion(
  coin: string,
  mode: string,
  question: string,
  idempotencyKey: string,
  fresh: boolean,
  signal?: AbortSignal,
  locale?: NarrativeLocale,
): Promise<ApiEnvelope<AnalysisQuestionReceipt>> {
  const options = {
    signal, method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    jsonBody: { coin, mode, question, locale: locale ?? currentNarrativeLocale(), fresh },
    timeoutMs: REGISTER_TIMEOUT_MS,
  } as const
  const submit = () => apiFetch('/api/analysis-question', undefined, isFormalRunReceipt, options)
  return submit().then((result) => {
    // The first 428 installs/refreshes an HttpOnly caller-scope cookie. The
    // browser persists it from the response; replay the byte-identical intent
    // once with the same formal key. A second challenge is surfaced instead
    // of becoming an unbounded retry loop.
    if (!result.ok && result.error.code === 'caller_scope_required' && !signal?.aborted) {
      return submit()
    }
    return result
  })
}

export interface AnalysisJobStatus {
  job_id: string; state: 'queued' | 'running' | 'completed' | 'failed'; current_stage: string
  coin: string; mode: string; question: string
  error: string | null; origin: 'manual' | 'scheduled'; priority: number; queue_position: number | null
  error_code?: 'analysis_job_failed' | 'analysis_job_retrying' | null
  result: AnalyzeData | null
}
export function getAnalysisJob(jobId: string, signal?: AbortSignal): Promise<ApiEnvelope<AnalysisJobStatus>> {
  const valid = (value: unknown): value is AnalysisJobStatus => !!value && typeof value === 'object' &&
    typeof (value as AnalysisJobStatus).job_id === 'string' &&
    typeof (value as AnalysisJobStatus).state === 'string' &&
    typeof (value as AnalysisJobStatus).current_stage === 'string' &&
    typeof (value as AnalysisJobStatus).coin === 'string' &&
    typeof (value as AnalysisJobStatus).mode === 'string' &&
    typeof (value as AnalysisJobStatus).question === 'string' &&
    ((value as AnalysisJobStatus).result === null || isAnalyzeData((value as AnalysisJobStatus).result))
  return apiFetch('/api/analysis-job', { job_id: jobId }, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export interface AnalysisQuestionMatch {
  question_id: string; coin: string; mode: string; question: string; similarity: number
  answer: string | null; snapshot_id: string | null; job_id: string | null; published_at: number | null
  source_tier: 'historical_non_evidentiary'
}
export interface AnalysisConversationMessage {
  message_id: string; role: 'user' | 'hermes'; content: string; question_id: string | null
  job_id: string | null; snapshot_id: string | null; created_at: number
}
export interface AnalysisQuestionContext {
  query: string; matches: AnalysisQuestionMatch[]; conversation: AnalysisConversationMessage[]; retrieval: string
}
export function getAnalysisQuestionContext(coin: string, mode: string, question: string, signal?: AbortSignal): Promise<ApiEnvelope<AnalysisQuestionContext>> {
  const valid = (value: unknown): value is AnalysisQuestionContext => !!value && typeof value === 'object' &&
    Array.isArray((value as AnalysisQuestionContext).matches) &&
    (value as AnalysisQuestionContext).matches.every((match) => match.source_tier === 'historical_non_evidentiary') &&
    Array.isArray((value as AnalysisQuestionContext).conversation)
  return apiFetch('/api/analysis-question-context', { coin, mode, q: question }, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export interface AnalysisFlowStage {
  id: string
  queued: number
  next_retry_at?: number | null
  current: null | { coin: string; mode: string; question: string; snapshot_id: string; started_at: number; retry_count: number; error: string | null }
}
export interface AnalysisFlowData { agent: string; state: string; stages: AnalysisFlowStage[]; updated_at: string }
export interface AnalysisJourneyAttempt { attempt_id: string; job_id: string; stage: string; attempt: number; state: string; started_at: number; finished_at: number; duration_sec: number; retryable: number; error: string | null }
export interface AnalysisJourneyJob { job_id: string; coin: string; mode: string; question: string; snapshot_id: string; state: string; current_stage: string; retry_count: number; error: string | null; updated_at: number; attempts: AnalysisJourneyAttempt[]; stages: Array<Record<string, unknown>> }
export interface AnalysisDeadLetter { job_id: string; stage: string; coin: string; mode: string; question: string; snapshot_id: string; attempts: number; error: string; failed_at: number }
export interface AnalysisJourneyData { jobs: AnalysisJourneyJob[]; dead_letters: AnalysisDeadLetter[]; updated_at: string }

export function getAnalysisFlow(signal?: AbortSignal): Promise<ApiEnvelope<AnalysisFlowData>> {
  const valid = (value: unknown): value is AnalysisFlowData => {
    if (!value || typeof value !== 'object') return false
    const data = value as AnalysisFlowData
    return data.agent === 'hermes' && Array.isArray(data.stages) && data.stages.every((stage) =>
      typeof stage.id === 'string' && typeof stage.queued === 'number' && (stage.current === null || typeof stage.current === 'object'))
  }
  return apiFetch<AnalysisFlowData>('/api/analysis-flow', undefined, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export interface HermesUpgradeModule {
  id: string; name: string; plane: string; channel: string; family: string; revision: string; revision_short: string; version: string
  origin: string; state: 'locked' | 'active' | 'candidate'; recursive_upgrade: false; automatic_apply: false
  proposals: Array<{ id: string; area: string; severity: string; proposed_experiment: string; success_metric: string }>
  history: Array<Record<string, unknown>>
}
export interface HermesUpgradeData {
  agent: 'hermes'; kind: 'upgrade_control_plane'; metaphor: 'modular_flagship'
  core_policy: string; outer_policy: string; recursive_upgrade: false
  diagnostic: { status: string; generated_at: string | null; proposal_count: number }
  coverage: { registered: number; complete: boolean }
  automation: {
    mode: string; measurements: Record<string, unknown>
    llm_review: { status: string; reviews: Array<Record<string, unknown>>; can_activate: false }
    durable_queue: { durable: boolean; proposal_count: number; proposals: Array<{ proposal_id: string; area: string; severity: string; state: string; created_at: number; updated_at: number }>; reviews: Array<Record<string, unknown>>; sandbox_runs: Array<Record<string, unknown>>; decisions: Array<Record<string, unknown>> }
    historical_sources?: Array<{ source: string; kind: string; strategy: string; status: string; coverage: string; terms: string }>
    stages: Array<{ id: string; state: string }>
  }
  core_package: {
    id: string; name: string; version: string; revision: string; state: string; controls: string[]
    upgrade_channel: string; external_upgrade: { status: string; adapter: string | null; automatic_activation: false }
  }
  planes: string[]
  modules: HermesUpgradeModule[]
}
export function getHermesUpgrades(signal?: AbortSignal): Promise<ApiEnvelope<HermesUpgradeData>> {
  const valid = (value: unknown): value is HermesUpgradeData => !!value && typeof value === 'object' &&
    (value as HermesUpgradeData).agent === 'hermes' && Array.isArray((value as HermesUpgradeData).modules) &&
    (value as HermesUpgradeData).modules.length >= 25 && !!(value as HermesUpgradeData).core_package
  return apiFetch('/api/hermes-upgrades', undefined, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function postHermesUpgradeDecision(adminToken: string, proposalId: string, decision: 'approve' | 'reject', actor: string, reason: string) {
  const valid = (value: unknown): value is { proposal_id: string; state: string; activated: false } => !!value && typeof value === 'object' && typeof (value as { proposal_id?: unknown }).proposal_id === 'string'
  return apiFetch('/api/admin/hermes-upgrade-decision', undefined, valid, {
    method: 'POST', headers: { 'X-Admin-Token': adminToken },
    jsonBody: { proposal_id: proposalId, decision, actor, reason }, cache: 'no-store',
  })
}

export function getAnalysisJourney(signal?: AbortSignal): Promise<ApiEnvelope<AnalysisJourneyData>> {
  const valid = (value: unknown): value is AnalysisJourneyData => !!value && typeof value === 'object' &&
    Array.isArray((value as AnalysisJourneyData).jobs) && Array.isArray((value as AnalysisJourneyData).dead_letters)
  return apiFetch('/api/analysis-journey', { limit: 100 }, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function requeueAnalysis(jobId: string): Promise<ApiEnvelope<{ job_id: string; state: string }>> {
  const valid = (value: unknown): value is { job_id: string; state: string } => !!value && typeof value === 'object' &&
    typeof (value as { job_id: unknown }).job_id === 'string' && typeof (value as { state: unknown }).state === 'string'
  return apiFetch('/api/analysis-requeue', undefined, valid, { method: 'POST', jsonBody: { job_id: jobId }, timeoutMs: REGISTER_TIMEOUT_MS })
}

export function getHealth(signal?: AbortSignal): Promise<ApiEnvelope<HealthData>> {
  return apiFetch<HealthData>('/api/health', undefined, isHealthData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export interface TrainingStatusPerCoin { total: number; has_direction: number }
export interface TrainingStatusData {
  training_data: {
    total_records: number; has_direction: number; direction_ratio: number
    per_coin: Record<string, TrainingStatusPerCoin>
  }
  backfill: { mode: string; is_running: boolean; completed: number; total: number; progress_pct: number } | null
  upgrade_threshold: { target: number; current: number; met: boolean; pct: number }
}
const isNonNegativeInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const isFiniteRange = (value: unknown, minimum: number, maximum: number): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
const isWithinReportedPrecision = (
  reported: number,
  exact: number,
  halfUnit: number,
): boolean =>
  Math.abs(reported - exact) <=
    halfUnit + Number.EPSILON * Math.max(1, Math.abs(reported), Math.abs(exact))

export function isTrainingStatusData(value: unknown): value is TrainingStatusData {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const data = value as TrainingStatusData
  const training = data.training_data
  const threshold = data.upgrade_threshold
  if (!training || typeof training !== 'object' || Array.isArray(training) ||
      !isNonNegativeInteger(training.total_records) ||
      !isNonNegativeInteger(training.has_direction) ||
      training.has_direction > training.total_records ||
      !isFiniteRange(training.direction_ratio, 0, 1) ||
      !training.per_coin || typeof training.per_coin !== 'object' || Array.isArray(training.per_coin) ||
      !threshold || typeof threshold !== 'object' || Array.isArray(threshold) ||
      !isNonNegativeInteger(threshold.target) || threshold.target === 0 ||
      !isNonNegativeInteger(threshold.current) ||
      typeof threshold.met !== 'boolean' ||
      !isFiniteRange(threshold.pct, 0, Number.MAX_VALUE) ||
      threshold.current !== training.has_direction ||
      threshold.met !== (threshold.current >= threshold.target) ||
      !isWithinReportedPrecision(
        training.direction_ratio,
        training.total_records === 0
          ? 0
          : training.has_direction / training.total_records,
        0.00005,
      ) ||
      !isWithinReportedPrecision(
        threshold.pct,
        threshold.current / threshold.target * 100,
        0.05,
      )) {
    return false
  }
  let perCoinTotal = 0
  let perCoinDirection = 0
  for (const [coin, stat] of Object.entries(training.per_coin)) {
    if (!coin || !stat || typeof stat !== 'object' || Array.isArray(stat) ||
        !isNonNegativeInteger(stat.total) ||
        !isNonNegativeInteger(stat.has_direction) ||
        stat.has_direction > stat.total) {
      return false
    }
    perCoinTotal += stat.total
    perCoinDirection += stat.has_direction
    if (!Number.isSafeInteger(perCoinTotal) || !Number.isSafeInteger(perCoinDirection)) return false
  }
  if (perCoinTotal !== training.total_records || perCoinDirection !== training.has_direction) return false
  if (data.backfill !== null) {
    const backfill = data.backfill
    if (!backfill || typeof backfill !== 'object' || Array.isArray(backfill) ||
        typeof backfill.mode !== 'string' || backfill.mode.length === 0 ||
        typeof backfill.is_running !== 'boolean' ||
        !isNonNegativeInteger(backfill.completed) ||
        !isNonNegativeInteger(backfill.total) ||
        backfill.completed > backfill.total ||
        !isFiniteRange(backfill.progress_pct, 0, 100)) {
      return false
    }
  }
  return true
}
export function getTrainingStatus(signal?: AbortSignal): Promise<ApiEnvelope<TrainingStatusData>> {
  return apiFetch<TrainingStatusData>('/api/training-status', undefined, isTrainingStatusData, {
    signal, timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export interface ComparisonParams {
  coin: string
  coin2: string
  q: string
}

// `/api/analyze?type=comparison` 回傳形狀跟單幣分析完全不同（`report_a`/
// `report_b` 雙份，見 `types.ts::ComparisonAnalyzeData`），故獨立一支函式、
// 獨立 validator（`isComparisonAnalyzeData`），不與 `getAnalyze` 共用同一個
// `isAnalyzeData` guard（會誤殺，形狀對不上）。逾時沿用 `ANALYZE_TIMEOUT_MS`
// ——理由同 `getAnalyze`，comparison 內部同樣會觸發真連接器（兩倍分析量）。
export function getComparison(
  params: ComparisonParams,
  signal?: AbortSignal,
): Promise<ApiEnvelope<ComparisonAnalyzeData>> {
  return apiFetch<ComparisonAnalyzeData>(
    '/api/analyze',
    { ...params, type: 'comparison' },
    isComparisonAnalyzeData,
    { signal, timeoutMs: ANALYZE_TIMEOUT_MS },
  )
}

export function registerAnalysisComparison(params: ComparisonParams): Promise<ApiEnvelope<{ question_ids: string[]; job_ids: (string | null)[]; state: string }>> {
  const valid = (value: unknown): value is { question_ids: string[]; job_ids: (string | null)[]; state: string } => {
    if (!value || typeof value !== 'object') return false
    const data = value as { question_ids: unknown; job_ids: unknown; state: unknown }
    return Array.isArray(data.question_ids) && data.question_ids.every((x) => typeof x === 'string') &&
      Array.isArray(data.job_ids) && data.job_ids.every((x) => x === null || typeof x === 'string') && typeof data.state === 'string'
  }
  return apiFetch('/api/analysis-comparison-question', undefined, valid, {
    method: 'POST', jsonBody: { coin: params.coin, coin2: params.coin2, question: params.q }, timeoutMs: REGISTER_TIMEOUT_MS,
  })
}

export function getComparisonSnapshot(params: ComparisonParams, signal?: AbortSignal): Promise<ApiEnvelope<ComparisonAnalyzeData>> {
  return apiFetch('/api/comparison-snapshot', { coin: params.coin, coin2: params.coin2, q: params.q }, isComparisonAnalyzeData, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function getStatus(signal?: AbortSignal): Promise<ApiEnvelope<StatusData>> {
  return apiFetch<StatusData>('/api/status', undefined, isStatusData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export function getCosts(offsetOrSignal: number | AbortSignal = 0, signal?: AbortSignal): Promise<ApiEnvelope<CostsData>> {
  const offset = typeof offsetOrSignal === 'number' ? offsetOrSignal : 0
  const requestSignal = typeof offsetOrSignal === 'number' ? signal : offsetOrSignal
  return apiFetch<CostsData>('/api/costs', { offset, limit: 50 }, isCostsData, {
    signal: requestSignal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export interface HistoryParams {
  coin: string
  days?: number
}

export function getHistory(params: HistoryParams, signal?: AbortSignal): Promise<ApiEnvelope<HistoryData>> {
  return apiFetch<HistoryData>('/api/history', { ...params }, isHistoryData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

// ── /api/admin/*（管理控制台，PR-4）──────────────────────────────────────
// 認證一律走 `X-Admin-Token` header（絕不進 URL/query——query 會落
// access log；同後端 `web.py` admin 區塊紀律）。token 由呼叫端（AdminPage）
// 持有於 React state / sessionStorage，見 `adminConsole.ts`。
//
// qa L4：三個 admin 端點一律 `cache: 'no-store'`——設定快照含 cap/
// last4/version 等易失真資訊，不得進瀏覽器 heuristic cache/bfcache（後端
// no-cache response header 另歸 PR-5，這裡是前端側雙邊防禦）。

export function getAdminConfig(
  adminToken: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminConfigData>> {
  return apiFetch<AdminConfigData>('/api/admin/config', undefined, isAdminConfigData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    headers: { 'X-Admin-Token': adminToken },
    cache: 'no-store',
  })
}

/** 部分更新（CAS）：`expectedVersion` 必須是剛 GET 到的 `version`
 * （item 尚不存在——`exists=false`、`version=null`——時傳 0）。409
 * `version_conflict` 由呼叫端重載最新設定後再改（`error.current_version`
 * 附最新 version，重讀失敗時為 null）。 */
export function putAdminConfig(
  adminToken: string,
  changes: AdminConfigChanges,
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminConfigData>> {
  return apiFetch<AdminConfigData>('/api/admin/config', undefined, isAdminConfigData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    method: 'PUT',
    headers: { 'X-Admin-Token': adminToken },
    jsonBody: { ...changes, expected_version: expectedVersion },
    cache: 'no-store',
  })
}

export function getAdminAudit(
  adminToken: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminAuditData>> {
  return apiFetch<AdminAuditData>('/api/admin/audit', undefined, isAdminAuditData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    headers: { 'X-Admin-Token': adminToken },
    cache: 'no-store',
  })
}

export function getWhaleAlertCredentialStatus(
  adminToken: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<WhaleAlertCredentialStatus>> {
  return apiFetch<WhaleAlertCredentialStatus>(
    '/api/admin/whale-alert',
    undefined,
    isWhaleAlertCredentialStatus,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
      headers: { 'X-Admin-Token': adminToken },
      cache: 'no-store',
    },
  )
}

export function updateWhaleAlertCredential(
  adminToken: string,
  action: 'set' | 'clear' | 'test',
  apiKey?: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<WhaleAlertCredentialStatus>> {
  return apiFetch<WhaleAlertCredentialStatus>(
    '/api/admin/whale-alert',
    undefined,
    isWhaleAlertCredentialStatus,
    {
      signal,
      timeoutMs: action === 'test' ? 10_000 : DEFAULT_TIMEOUT_MS,
      method: 'POST',
      headers: { 'X-Admin-Token': adminToken },
      jsonBody: action === 'set' ? { action, api_key: apiKey } : { action },
      cache: 'no-store',
    },
  )
}

export function getAdminBackendProviders(
  adminToken: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminBackendProvidersData>> {
  return apiFetch<AdminBackendProvidersData>(
    '/api/admin/backend-providers',
    undefined,
    isAdminBackendProvidersData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
      headers: { 'X-Admin-Token': adminToken },
      cache: 'no-store',
    },
  )
}

export function setAdminBackendProvider(
  adminToken: string,
  key: BackendProviderKey,
  provider: BackendProvider,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminBackendProvidersData>> {
  return apiFetch<AdminBackendProvidersData>(
    '/api/admin/backend-provider',
    undefined,
    isAdminBackendProvidersData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
      method: 'POST',
      headers: { 'X-Admin-Token': adminToken },
      jsonBody: { key, provider },
      cache: 'no-store',
    },
  )
}

export function setAllAdminBackendProviders(
  adminToken: string,
  provider: BackendProvider,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AdminBackendProvidersData>> {
  return apiFetch<AdminBackendProvidersData>(
    '/api/admin/backend-providers-all',
    undefined,
    isAdminBackendProvidersData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
      method: 'POST',
      headers: { 'X-Admin-Token': adminToken },
      jsonBody: { provider },
      cache: 'no-store',
    },
  )
}

/** `GET /api/asset-context`：獨立於 `/api/analyze` 的輕量唯讀查詢，見
 * `web.py::_handle_api_asset_context` docstring——查無資料時
 * `data.asset_context` 為 `null`，不是錯誤，呼叫端不需特判 error。 */
export function getAssetContext(
  symbol: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AssetContextResponseData>> {
  return apiFetch<AssetContextResponseData>(
    '/api/asset-context',
    { symbol },
    isAssetContextResponseData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
    },
  )
}

/** `GET /api/peer-metrics`：獨立於 `/api/analyze` 的輕量唯讀查詢（模組③
 * Wave 3）——查無此資產時 `data.snapshot` 為 `null`、`data.peers` 為空
 * 陣列（語意是「查無資料」而非請求錯誤，同 `/api/asset-context` 慣例）。
 * `asset` 是資產識別碼，例如 `asset:arb`。 */
export function getPeerMetrics(
  asset: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<PeerMetricsResponseData>> {
  return apiFetch<PeerMetricsResponseData>(
    '/api/peer-metrics',
    { asset },
    isPeerMetricsResponseData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
    },
  )
}

/** `GET /api/eco-link`：獨立於 `/api/analyze` 的輕量唯讀查詢（模組③
 * Wave 3）——回傳*相關性*影響路徑（非因果）；`verdict` 為
 * `insufficient_data` 時 `impact_paths` 為空陣列。`asset` 是資產識別碼，
 * 例如 `asset:arb`。 */
export function getEcoLink(
  asset: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<EcoLinkResponseData>> {
  return apiFetch<EcoLinkResponseData>(
    '/api/eco-link',
    { asset },
    isEcoLinkResponseData,
    {
      signal,
      timeoutMs: DEFAULT_TIMEOUT_MS,
    },
  )
}

// ---------------------------------------------------------------------------
// Whale Alert — 大額轉帳即時摘要
// ---------------------------------------------------------------------------

import type { WhaleSummary } from '../components/WhaleActivityPanel'

export function getWhaleSummary(
  coin: string,
  signal?: AbortSignal,
): Promise<ApiEnvelope<WhaleSummary>> {
  const valid = (value: unknown): value is WhaleSummary => {
    if (!value || typeof value !== 'object') return false
    const data = value as Record<string, unknown>
    return typeof data.total_count === 'number' && typeof data.coin === 'string'
  }
  return apiFetch<WhaleSummary>(
    '/api/whale-summary',
    { coin },
    valid,
    { signal, timeoutMs: DEFAULT_TIMEOUT_MS },
  )
}
