import { ANALYZE_TIMEOUT_MS, apiFetch, DEFAULT_TIMEOUT_MS } from './apiClient'
import {
  isAdminAuditData,
  isAdminConfigData,
  isAnalyzeData,
  isComparisonAnalyzeData,
  isCostsData,
  isHealthData,
  isHistoryData,
  isOverviewData,
  isStatusData,
} from './validators'
import type {
  AdminAuditData,
  AdminConfigChanges,
  AdminConfigData,
  AnalyzeData,
  ApiEnvelope,
  ComparisonAnalyzeData,
  CostsData,
  HealthData,
  HistoryData,
  OverviewData,
  StatusData,
} from './types'

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

interface AnalysisQuestionReceipt { question_id: string; job_id: string | null; state: string }
export function registerAnalysisQuestion(coin: string, mode: string, question: string): Promise<ApiEnvelope<AnalysisQuestionReceipt>> {
  const valid = (value: unknown): value is AnalysisQuestionReceipt => !!value && typeof value === 'object' &&
    typeof (value as AnalysisQuestionReceipt).question_id === 'string' &&
    ((value as AnalysisQuestionReceipt).job_id === null || typeof (value as AnalysisQuestionReceipt).job_id === 'string') &&
    typeof (value as AnalysisQuestionReceipt).state === 'string'
  return apiFetch('/api/analysis-question', undefined, valid, {
    method: 'POST', jsonBody: { coin, mode, question }, timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export interface AnalysisQuestionMatch {
  question_id: string; coin: string; mode: string; question: string; similarity: number
  answer: string | null; snapshot_id: string | null; job_id: string | null; published_at: number | null
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
    Array.isArray((value as AnalysisQuestionContext).matches) && Array.isArray((value as AnalysisQuestionContext).conversation)
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

export function getAnalysisJourney(signal?: AbortSignal): Promise<ApiEnvelope<AnalysisJourneyData>> {
  const valid = (value: unknown): value is AnalysisJourneyData => !!value && typeof value === 'object' &&
    Array.isArray((value as AnalysisJourneyData).jobs) && Array.isArray((value as AnalysisJourneyData).dead_letters)
  return apiFetch('/api/analysis-journey', { limit: 100 }, valid, { signal, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function requeueAnalysis(jobId: string): Promise<ApiEnvelope<{ job_id: string; state: string }>> {
  const valid = (value: unknown): value is { job_id: string; state: string } => !!value && typeof value === 'object' &&
    typeof (value as { job_id: unknown }).job_id === 'string' && typeof (value as { state: unknown }).state === 'string'
  return apiFetch('/api/analysis-requeue', undefined, valid, { method: 'POST', jsonBody: { job_id: jobId }, timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function getHealth(signal?: AbortSignal): Promise<ApiEnvelope<HealthData>> {
  return apiFetch<HealthData>('/api/health', undefined, isHealthData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
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
    method: 'POST', jsonBody: { coin: params.coin, coin2: params.coin2, question: params.q }, timeoutMs: DEFAULT_TIMEOUT_MS,
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
