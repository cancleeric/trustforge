import { ANALYZE_TIMEOUT_MS, apiFetch, DEFAULT_TIMEOUT_MS } from './apiClient'
import { isAnalyzeData, isHealthData, isOverviewData } from './validators'
import type { AnalyzeData, ApiEnvelope, HealthData, OverviewData } from './types'

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
