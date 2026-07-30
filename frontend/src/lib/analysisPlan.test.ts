import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ANALYSIS_PLAN_TIMEOUT_MS,
  isAnalysisPlan,
  isAnalysisPlanRequest,
  previewAnalysisPlan,
  type AnalysisPlanReady,
} from './analysisPlan'

const readyPlan = (): AnalysisPlanReady => ({
  outcome: 'ready',
  detected_assets: ['BTC'],
  intent_shape: 'single',
  intents: [{ label: 'risk outlook', rationale: 'Assess the requested risk.' }],
  source_classes: ['market_price', 'research'],
  strategy_summary: 'Compare recent market structure with published research.',
  clarifications: [],
  warnings: [],
  confidence: { level: 'high', rationale: 'The request is specific.' },
  provenance: {
    planner: 'hermes',
    provider: 'aws-bedrock',
    policy_version: 'plan-v1',
  },
})

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('analysis plan exact runtime contract', () => {
  it('accepts both strict union variants', () => {
    expect(isAnalysisPlan(readyPlan())).toBe(true)
    expect(
      isAnalysisPlan({
        ...readyPlan(),
        outcome: 'needs_clarification',
        clarifications: [{ id: 'scope', question: 'Which scope?', options: [] }],
      }),
    ).toBe(true)
  })

  it.each([
    ['top-level extra field', { ...readyPlan(), original_question: 'secret' }],
    [
      'nested extra field',
      { ...readyPlan(), confidence: { ...readyPlan().confidence, probability: 0.9 } },
    ],
    ['unknown source class', { ...readyPlan(), source_classes: ['connector_name'] }],
    ['wrong discriminator', { ...readyPlan(), outcome: 'complete' }],
    ['empty required strategy', { ...readyPlan(), strategy_summary: '' }],
    ['clarification required by variant', { ...readyPlan(), outcome: 'needs_clarification' }],
    [
      'lone surrogate',
      { ...readyPlan(), intents: [{ label: '\ud800', rationale: 'invalid scalar' }] },
    ],
    [
      'leading whitespace in a model string',
      { ...readyPlan(), intents: [{ label: ' risk outlook', rationale: 'valid' }] },
    ],
    [
      'trailing whitespace in a model string',
      { ...readyPlan(), strategy_summary: 'not canonical ' },
    ],
    [
      'bidi control in a model string',
      { ...readyPlan(), warnings: ['safe\u202etxt'] },
    ],
    ['duplicate detected assets', { ...readyPlan(), detected_assets: ['BTC', 'BTC'] }],
    [
      'duplicate source classes',
      { ...readyPlan(), source_classes: ['research', 'research'] },
    ],
    [
      'duplicate clarification ids',
      {
        ...readyPlan(),
        clarifications: [
          { id: 'scope', question: 'Which scope?', options: [] },
          { id: 'scope', question: 'Which horizon?', options: [] },
        ],
      },
    ],
  ])('rejects %s', (_name, value) => {
    expect(isAnalysisPlan(value)).toBe(false)
  })

  it.each([
    ['intent label', { intents: [{ label: 'risk\u2066', rationale: 'valid' }] }],
    ['intent rationale', { intents: [{ label: 'risk', rationale: 'valid\u200f' }] }],
    [
      'clarification question',
      { clarifications: [{ id: 'scope', question: 'Which\u061c scope?', options: [] }] },
    ],
    [
      'clarification option',
      { clarifications: [{ id: 'scope', question: 'Which scope?', options: ['one\u2069'] }] },
    ],
    ['confidence rationale', { confidence: { level: 'high', rationale: 'clear\u202a' } }],
  ])('rejects bidi controls in %s', (_name, replacement) => {
    expect(isAnalysisPlan({ ...readyPlan(), ...replacement })).toBe(false)
  })

  it('checks code-point bounds rather than UTF-16 units', () => {
    expect(isAnalysisPlan({ ...readyPlan(), strategy_summary: '😀'.repeat(600) })).toBe(true)
    expect(isAnalysisPlan({ ...readyPlan(), strategy_summary: '😀'.repeat(601) })).toBe(false)
  })
})

describe('analysis plan request and client', () => {
  it('keeps preview locale separate and validates canonical request fields', () => {
    expect(isAnalysisPlanRequest({ question: '分析 BTC', locale: 'zh-TW' })).toBe(true)
    expect(isAnalysisPlanRequest({ question: 'Analyze BTC', locale: 'en' })).toBe(true)
    expect(isAnalysisPlanRequest({ question: 'Analyze BTC', locale: 'zh-Hant' })).toBe(false)
    expect(
      isAnalysisPlanRequest({ question: 'Analyze BTC', locale: 'en', asset_hints: ['btc'] }),
    ).toBe(false)
    expect(
      isAnalysisPlanRequest({
        question: 'Analyze BTC',
        locale: 'en',
        asset_hints: ['BTC', 'BTC'],
      }),
    ).toBe(false)
    expect(isAnalysisPlanRequest({ question: '   ', locale: 'en' })).toBe(false)
    expect(isAnalysisPlanRequest({ question: `${'x'.repeat(1_000)} `, locale: 'en' })).toBe(true)
    expect(isAnalysisPlanRequest({ question: `${'x'.repeat(1_001)} `, locale: 'en' })).toBe(false)
    expect(isAnalysisPlanRequest({ question: 'x', locale: 'en', extra: true })).toBe(false)
  })

  it('posts strict JSON with no-store and accepts a strict success envelope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(200, { ok: true, data: readyPlan() }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' })
    expect(result.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/analysis-plan',
      expect.objectContaining({
        method: 'POST',
        cache: 'no-store',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      }),
    )
  })

  it('rejects invalid request locally without a provider-facing HTTP call', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const result = await previewAnalysisPlan({
      question: '',
      locale: 'en',
    } as Parameters<typeof previewAnalysisPlan>[0])
    expect(result).toEqual({
      ok: false,
      error: {
        code: 'invalid_plan_request',
        message: '請檢查問題、語系與資產提示格式。',
        retryable: false,
      },
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('preserves only an exact status-bound safe error envelope', async () => {
    const safeError = {
      ok: false,
      error: {
        code: 'plan_rate_limited',
        message: '規劃請求過於頻繁。你可以返回編輯，或稍後再試。',
        retryable: true,
      },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(429, safeError)))
    expect(await previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' })).toEqual(safeError)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(503, safeError)))
    const mismatched = await previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' })
    expect(mismatched.ok).toBe(false)
    if (!mismatched.ok) expect(mismatched.error.code).toBe('parse_error')
  })

  it('rejects extra success-envelope fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(200, { ok: true, data: readyPlan(), original_question: 'must-not-pass' }),
      ),
    )
    const result = await previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('honors caller abort without retry', async () => {
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        )
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const pending = previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' }, controller.signal)
    controller.abort()
    const result = await pending
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('cancelled')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('uses a dedicated timeout slightly above the six-second server deadline, with no retry', async () => {
    expect(ANALYSIS_PLAN_TIMEOUT_MS).toBeGreaterThan(6_000)
    expect(ANALYSIS_PLAN_TIMEOUT_MS).toBeLessThan(7_000)
    vi.useFakeTimers()
    const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        )
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const pending = previewAnalysisPlan({ question: 'Analyze BTC', locale: 'en' })
    await vi.advanceTimersByTimeAsync(ANALYSIS_PLAN_TIMEOUT_MS)
    const result = await pending
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('timeout')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
