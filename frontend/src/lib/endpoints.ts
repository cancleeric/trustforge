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

export function getStatus(signal?: AbortSignal): Promise<ApiEnvelope<StatusData>> {
  return apiFetch<StatusData>('/api/status', undefined, isStatusData, {
    signal,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  })
}

export function getCosts(signal?: AbortSignal): Promise<ApiEnvelope<CostsData>> {
  return apiFetch<CostsData>('/api/costs', undefined, isCostsData, {
    signal,
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
