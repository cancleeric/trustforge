import type { ApiEnvelope, ApiFailure } from './types'

export const ANALYSIS_PLAN_TIMEOUT_MS = 6_500

export type AnalysisPlanLocale = 'zh-TW' | 'en'
export type AnalysisPlanIntentShape = 'single' | 'multiple' | 'unknown'

export interface AnalysisPlanRequest {
  question: string
  locale: AnalysisPlanLocale
  asset_hints?: string[]
  client_request_id?: string
}

export interface AnalysisPlanIntent {
  label: string
  rationale: string
}

export interface AnalysisPlanConfidence {
  level: 'low' | 'medium' | 'high'
  rationale: string
}

export interface AnalysisPlanClarification {
  id: string
  question: string
  options: string[]
}

export interface AnalysisPlanProvenance {
  planner: 'hermes'
  provider: 'aws-bedrock'
  policy_version: string
}

interface AnalysisPlanBase {
  detected_assets: string[]
  intent_shape: AnalysisPlanIntentShape
  intents: AnalysisPlanIntent[]
  source_classes: AnalysisPlanSourceClass[]
  strategy_summary: string
  clarifications: AnalysisPlanClarification[]
  warnings: string[]
  confidence: AnalysisPlanConfidence
  provenance: AnalysisPlanProvenance
}

export interface AnalysisPlanReady extends AnalysisPlanBase {
  outcome: 'ready'
}

export interface AnalysisPlanNeedsClarification extends AnalysisPlanBase {
  outcome: 'needs_clarification'
}

export type AnalysisPlan = AnalysisPlanReady | AnalysisPlanNeedsClarification

export type AnalysisPlanErrorCode =
  | 'invalid_plan_request'
  | 'plan_rate_limited'
  | 'plan_temporarily_unavailable'
  | 'plan_timeout'

export interface AnalysisPlanErrorEnvelope {
  ok: false
  error: {
    code: AnalysisPlanErrorCode
    message: string
    retryable: boolean
  }
}

export const ANALYSIS_PLAN_SOURCE_CLASSES = [
  'market_price',
  'derivatives',
  'on_chain',
  'news',
  'social',
  'regulatory',
  'macroeconomic',
  'project_primary',
  'exchange',
  'security_incident',
  'governance',
  'research',
] as const

export type AnalysisPlanSourceClass = (typeof ANALYSIS_PLAN_SOURCE_CLASSES)[number]

const ASSET_SYMBOL = /^[A-Z0-9][A-Z0-9._:-]{0,15}$/
const SAFE_ID = /^[A-Za-z0-9._-]+$/
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const BIDI_CONTROLS = /[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/

const ERROR_CONTRACT = {
  invalid_plan_request: {
    status: 400,
    retryable: false,
    message: '請檢查問題、語系與資產提示格式。',
  },
  plan_rate_limited: {
    status: 429,
    retryable: true,
    message: '規劃請求過於頻繁。你可以返回編輯，或稍後再試。',
  },
  plan_temporarily_unavailable: {
    status: 503,
    retryable: true,
    message: 'Hermes 規劃暫時不可用。你可以返回編輯。',
  },
  plan_timeout: {
    status: 504,
    retryable: true,
    message: 'Hermes 規劃逾時。你可以返回編輯。',
  },
} as const

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional])
  const keys = Object.keys(value)
  return required.every((key) => Object.hasOwn(value, key)) && keys.every((key) => allowed.has(key))
}

function hasOnlyUnicodeScalars(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index)
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false
      index += 1
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false
    }
  }
  return true
}

function isBoundedString(value: unknown, min: number, max: number): value is string {
  return (
    typeof value === 'string' &&
    hasOnlyUnicodeScalars(value) &&
    Array.from(value).length >= min &&
    Array.from(value).length <= max
  )
}

function isModelString(value: unknown, max: number): value is string {
  return (
    isBoundedString(value, 1, max) &&
    value === value.trim() &&
    !BIDI_CONTROLS.test(value)
  )
}

function isBoundedArray<T>(
  value: unknown,
  min: number,
  max: number,
  guard: (item: unknown) => item is T,
): value is T[] {
  return Array.isArray(value) && value.length >= min && value.length <= max && value.every(guard)
}

function isAssetSymbol(value: unknown): value is string {
  return typeof value === 'string' && ASSET_SYMBOL.test(value)
}

function isIntent(value: unknown): value is AnalysisPlanIntent {
  return (
    isObject(value) &&
    hasExactKeys(value, ['label', 'rationale']) &&
    isModelString(value.label, 64) &&
    isModelString(value.rationale, 240)
  )
}

function isConfidence(value: unknown): value is AnalysisPlanConfidence {
  return (
    isObject(value) &&
    hasExactKeys(value, ['level', 'rationale']) &&
    (value.level === 'low' || value.level === 'medium' || value.level === 'high') &&
    isModelString(value.rationale, 160)
  )
}

function isClarification(value: unknown): value is AnalysisPlanClarification {
  return (
    isObject(value) &&
    hasExactKeys(value, ['id', 'question', 'options']) &&
    isBoundedString(value.id, 1, 32) &&
    SAFE_ID.test(value.id) &&
    isModelString(value.question, 240) &&
    isBoundedArray(value.options, 0, 6, (option): option is string =>
      isModelString(option, 80),
    )
  )
}

function isProvenance(value: unknown): value is AnalysisPlanProvenance {
  return (
    isObject(value) &&
    hasExactKeys(value, ['planner', 'provider', 'policy_version']) &&
    value.planner === 'hermes' &&
    value.provider === 'aws-bedrock' &&
    isBoundedString(value.policy_version, 1, 32) &&
    SAFE_ID.test(value.policy_version)
  )
}

function isSourceClass(value: unknown): value is AnalysisPlanSourceClass {
  return (
    typeof value === 'string' &&
    (ANALYSIS_PLAN_SOURCE_CLASSES as readonly string[]).includes(value)
  )
}

export function isAnalysisPlan(value: unknown): value is AnalysisPlan {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      'outcome',
      'detected_assets',
      'intent_shape',
      'intents',
      'source_classes',
      'strategy_summary',
      'clarifications',
      'warnings',
      'confidence',
      'provenance',
    ])
  ) {
    return false
  }
  if (value.outcome !== 'ready' && value.outcome !== 'needs_clarification') return false
  if (
    !isBoundedArray(value.detected_assets, 0, 8, isAssetSymbol) ||
    (value.intent_shape !== 'single' &&
      value.intent_shape !== 'multiple' &&
      value.intent_shape !== 'unknown') ||
    !isBoundedArray(value.intents, 0, 8, isIntent) ||
    !isBoundedArray(value.source_classes, 0, 12, isSourceClass) ||
    !isModelString(value.strategy_summary, 600) ||
    !isBoundedArray(value.warnings, 0, 8, (warning): warning is string =>
      isModelString(warning, 160),
    ) ||
    !isConfidence(value.confidence) ||
    !isProvenance(value.provenance)
  ) {
    return false
  }
  if (new Set(value.detected_assets).size !== value.detected_assets.length) return false
  if (new Set(value.source_classes).size !== value.source_classes.length) return false
  const minimumClarifications = value.outcome === 'needs_clarification' ? 1 : 0
  return (
    isBoundedArray(value.clarifications, minimumClarifications, 3, isClarification) &&
    new Set(value.clarifications.map((item) => item.id)).size === value.clarifications.length
  )
}

export function isAnalysisPlanRequest(value: unknown): value is AnalysisPlanRequest {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ['question', 'locale'], ['asset_hints', 'client_request_id']) ||
    typeof value.question !== 'string' ||
    !hasOnlyUnicodeScalars(value.question) ||
    !isBoundedString(value.question.trim(), 1, 1_000) ||
    (value.locale !== 'zh-TW' && value.locale !== 'en')
  ) {
    return false
  }
  if (value.asset_hints !== undefined) {
    if (!isBoundedArray(value.asset_hints, 0, 8, isAssetSymbol)) return false
    if (new Set(value.asset_hints).size !== value.asset_hints.length) return false
  }
  return value.client_request_id === undefined ||
    (typeof value.client_request_id === 'string' && UUID_V4.test(value.client_request_id))
}

function isPlanErrorEnvelope(value: unknown, status: number): value is AnalysisPlanErrorEnvelope {
  if (!isObject(value) || !hasExactKeys(value, ['ok', 'error']) || value.ok !== false) return false
  const error = value.error
  if (!isObject(error) || !hasExactKeys(error, ['code', 'message', 'retryable'])) return false
  if (typeof error.code !== 'string' || !(error.code in ERROR_CONTRACT)) return false
  const expected = ERROR_CONTRACT[error.code as AnalysisPlanErrorCode]
  return (
    status === expected.status &&
    error.message === expected.message &&
    error.retryable === expected.retryable
  )
}

function clientFailure(code: string, message: string): ApiFailure {
  return { ok: false, error: { code, message } }
}

export async function previewAnalysisPlan(
  request: AnalysisPlanRequest,
  signal?: AbortSignal,
): Promise<ApiEnvelope<AnalysisPlan>> {
  if (!isAnalysisPlanRequest(request)) {
    return {
      ok: false,
      error: {
        code: 'invalid_plan_request',
        message: ERROR_CONTRACT.invalid_plan_request.message,
        retryable: false,
      },
    }
  }
  const body = JSON.stringify(request)
  if (new TextEncoder().encode(body).byteLength > 16 * 1_024) {
    return {
      ok: false,
      error: {
        code: 'invalid_plan_request',
        message: ERROR_CONTRACT.invalid_plan_request.message,
        retryable: false,
      },
    }
  }

  const controller = new AbortController()
  let timedOut = false
  const onAbort = () => controller.abort()
  if (signal?.aborted) controller.abort()
  else signal?.addEventListener('abort', onAbort)
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, ANALYSIS_PLAN_TIMEOUT_MS)

  let response: Response
  let payload: unknown
  try {
    response = await fetch('/api/analysis-plan', {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body,
      cache: 'no-store',
      signal: controller.signal,
    })
    payload = await response.json()
  } catch (error) {
    if (timedOut) return clientFailure('timeout', 'Hermes 規劃請求逾時。')
    if (signal?.aborted) return clientFailure('cancelled', '請求已取消')
    return clientFailure(
      'network_error',
      error instanceof Error ? error.message : '網路連線失敗',
    )
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }

  if (
    response.status === 200 &&
    isObject(payload) &&
    hasExactKeys(payload, ['ok', 'data']) &&
    payload.ok === true &&
    isAnalysisPlan(payload.data)
  ) {
    return { ok: true, data: payload.data }
  }
  if (isPlanErrorEnvelope(payload, response.status)) return payload
  return clientFailure('parse_error', '伺服器回應格式不符規劃契約')
}
