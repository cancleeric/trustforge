// Phase 2b 新增端點（/api/status、/api/costs、/api/history、
// /api/analyze?type=comparison）的 runtime validator 測試——沿用
// `apiClient.test.ts` 既有慣例：構造合法/畸形 payload，斷言 `apiFetch`
// 一律回結構化結果（不 throw），畸形形狀一律 `parse_error`，不讓半成品
// 資料流向下游元件造成白屏。

import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from './apiClient'
import { isComparisonAnalyzeData, isCostsData, isHistoryData, isStatusData } from './validators'
import type { ComparisonAnalyzeData, CostsData, HistoryData, StatusData } from './types'

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

/** 測試用：把任意值當成可自由塞畸形巢狀欄位的 mutable record，避免逐處
 * `as any` 造成 oxlint no-explicit-any 噪音——僅用於建構測試 fixture，
 * 不影響 production 程式碼路徑（比照 `apiClient.test.ts::asMutable`）。 */
function asMutable(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>
}

afterEach(() => {
  vi.unstubAllGlobals()
})

// ── /api/status ──────────────────────────────────────────────────────────

function validStatusData(): StatusData {
  return {
    version: 'v0.5.16',
    uptime_seconds: 123.456,
    bedrock_capable: true,
    live_token_set: false,
    cache_backend: {
      name: 'DynamoDBCache',
      connected: true,
      primary_connected: true,
      active_backend: 'DynamoDBCache',
      degraded: false,
    },
    freshness: {
      fresh: 1,
      stale: 0,
      missing: 1,
      entries: [
        { source: 'coingecko-price', coin: 'BTC', status: 'fresh', fetched_at: 1000, age_seconds: 5 },
        { source: 'coingecko-price', coin: 'ETH', status: 'missing', fetched_at: null, age_seconds: null },
      ],
    },
  }
}

describe('isStatusData / /api/status', () => {
  it('完整合法資料 → 正常放行', async () => {
    const data = validStatusData()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<StatusData>('/api/status', undefined, isStatusData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('analysis_flow 成本紀錄的 coin 為 null → 正常放行', async () => {
    const data = validCostsData()
    data.runs[0] = {
      ...data.runs[0],
      coin: null,
      question_type: 'analysis_flow',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data.runs[0].coin).toBeNull()
  })

  it('freshness.entries 元素 status 不是合法三態之一 → parse_error', async () => {
    const data = validStatusData()
    asMutable(data.freshness.entries[0]).status = 'unknown'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<StatusData>('/api/status', undefined, isStatusData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('missing 格 age_seconds 是 undefined（非 null/number）→ parse_error', async () => {
    const data = validStatusData()
    delete asMutable(data.freshness.entries[1]).age_seconds
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<StatusData>('/api/status', undefined, isStatusData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('cache_backend 缺 primary_connected 欄位 → parse_error', async () => {
    const data = validStatusData()
    delete asMutable(data.cache_backend).primary_connected
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<StatusData>('/api/status', undefined, isStatusData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('freshness 缺 entries 陣列 → parse_error', async () => {
    const data = validStatusData()
    delete asMutable(data.freshness).entries
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<StatusData>('/api/status', undefined, isStatusData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})

// ── /api/costs ───────────────────────────────────────────────────────────

function validCostsData(): CostsData {
  return {
    total_cost_usd: 0.0042,
    by_model: { 'claude-3': 0.0042 },
    by_model_detail: { 'claude-3': { cost_usd: 0.0042, tokens_in: 100, tokens_out: 50 } },
    run_count: 1,
    runs: [
      {
        ts: '2026-01-01T00:00:00+00:00',
        coin: 'BTC',
        question_type: 'multi_source',
        offline: false,
        total_cost_usd: 0.0042,
        calls: [{ model: 'claude-3', cost_usd: 0.0042 }],
      },
    ],
  }
}

describe('isCostsData / /api/costs', () => {
  it('完整合法資料 → 正常放行', async () => {
    const data = validCostsData()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('account-level run 的 coin 為 null → 正常放行', async () => {
    const data = validCostsData()
    data.runs[0].coin = null
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('runs 某筆 coin 為非字串且非 null → parse_error', async () => {
    const data = validCostsData()
    asMutable(data.runs[0]).coin = 42
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('by_model_detail 某 model 缺 tokens_out → parse_error', async () => {
    const data = validCostsData()
    delete asMutable(data.by_model_detail['claude-3']).tokens_out
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('by_model 某 model 值是字串（非 number）→ parse_error', async () => {
    const data = validCostsData()
    asMutable(data.by_model)['claude-3'] = '0.0042'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('runs 不是陣列 → parse_error', async () => {
    const data = validCostsData()
    asMutable(data).runs = { not: 'array' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('total_cost_usd 缺席 → parse_error', async () => {
    const data = validCostsData()
    delete asMutable(data).total_cost_usd
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  // codex HIGH（成本端點可擴展性）：後端改回有界摘要後新增 `run_count`（真實
  // 總筆數），`runs` 只回最近 N 筆——這裡確保 validator 對齊新 shape。
  it('run_count 缺席 → parse_error', async () => {
    const data = validCostsData()
    delete asMutable(data).run_count
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('runs 某筆缺 ts → parse_error', async () => {
    const data = validCostsData()
    delete asMutable(data.runs[0]).ts
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<CostsData>('/api/costs', undefined, isCostsData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})

// ── /api/history ─────────────────────────────────────────────────────────

function validHistoryData(): HistoryData {
  return {
    coin: 'ETH',
    days: 30,
    history: [
      {
        date: '2026-06-30',
        coin: 'ETH',
        trust_score: 0.6,
        direction: '中性',
        calibrated_confidence: 0.55,
        decision_state: 'normal',
        generated_at: '2026-06-30T00:00:00Z',
      },
    ],
  }
}

describe('isHistoryData / /api/history', () => {
  it('完整合法資料（含歷史累積很少，只有一筆）→ 正常放行', async () => {
    const data = validHistoryData()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('history 空陣列（剛開始累積，合法情況）→ 正常放行，不是 parse_error', async () => {
    const data: HistoryData = { coin: 'BTC', days: 30, history: [] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data.history).toEqual([])
  })

  // #1 修復（PR #103 Round 2，legacy 快照炸掉 React overview）：這裡原本斷言
  // `decision_state` 為未知字面值（如 `'unknown'`）要整包 parse_error——這正是
  // CEO 終審指出的 HIGH 問題本身：legacy 快照／版本切換期可能帶尚未認識的
  // enum 值，跟 SSR（一律當 normal 處理）行為分裂，不該讓單一舊快照拖垮
  // 整個 `/api/history` 解析。改為斷言「未知字面值仍放行」，真正型別錯誤
  // （非字串）才 parse_error，跟 `isOverviewData`/`isAnalyzeData` 同一套
  // 「形狀合法」判準（見 `decisionState.test.ts`）。
  it('history 元素 decision_state 為未知字面值（legacy/未來新 enum）→ 仍放行，不 parse_error', async () => {
    const data = validHistoryData()
    asMutable(data.history[0]).decision_state = 'unknown'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(true)
  })

  it('history 元素 decision_state 型別錯誤（非字串）→ parse_error', async () => {
    const data = validHistoryData()
    asMutable(data.history[0]).decision_state = 123
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('history 元素 reputation_trace 存在但 entry 缺 delta → parse_error', async () => {
    const data = validHistoryData()
    asMutable(data.history[0]).reputation_trace = {
      src: { prior: 0.9, final: 0.9, agree_n: 0, contradict_n: 0 },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('days 不是 number（字串）→ parse_error', async () => {
    const data = validHistoryData()
    asMutable(data).days = '30'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HistoryData>('/api/history', undefined, isHistoryData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})

// ── /api/analyze?type=comparison ────────────────────────────────────────

function validReport() {
  return {
    coin: 'BTC',
    question_type: 'comparison',
    question: '比較BTC與ETH',
    market_judgment: '中性',
    facts: [],
    inferences: [],
    key_basis: [],
    confidence: 0.6,
    limits: [],
    could_flip: [],
    contrarian: [],
    generated_at: '2026-07-04T00:00:00Z',
    direction: '中性',
    cross_source_signal: null,
    calibrated_confidence: 0.6,
    decision_state: 'normal' as const,
  }
}

function validComparisonData(): ComparisonAnalyzeData {
  const radar = {
    price: { label: '價格信任', has_data: true, trust: 0.8, n_sources: 2, n_evidence: 3, single_source: false },
  }
  const aggregate = { reputation: 0.8, corroboration: 0.5, recency: 0.6, manipulation: 0.0 }
  return {
    version: 'v0.5.16',
    report_a: { ...validReport(), coin: 'BTC' },
    evidence_a: [],
    trust_radar_a: radar,
    trust_components_aggregate_a: aggregate,
    price_provenance_a: {},
    report_b: { ...validReport(), coin: 'ETH' },
    evidence_b: [],
    trust_radar_b: radar,
    trust_components_aggregate_b: aggregate,
    price_provenance_b: {},
    execution_log: [],
  }
}

describe('isComparisonAnalyzeData / /api/analyze?type=comparison', () => {
  it('完整合法雙幣比較資料 → 正常放行', async () => {
    const data = validComparisonData()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<ComparisonAnalyzeData>('/api/analyze', undefined, isComparisonAnalyzeData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('report_b 缺席 → parse_error（不能只驗 report_a 就放行）', async () => {
    const data = validComparisonData()
    delete asMutable(data).report_b
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<ComparisonAnalyzeData>('/api/analyze', undefined, isComparisonAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('trust_radar_b.price 是 null（非物件）→ parse_error', async () => {
    const data = validComparisonData()
    asMutable(data.trust_radar_b).price = null
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<ComparisonAnalyzeData>('/api/analyze', undefined, isComparisonAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('用單幣 isAnalyzeData 形狀（report/evidence 無 _a/_b 後綴）誤打成 comparison guard → parse_error', async () => {
    const singleShaped = {
      version: 'v0.5.16',
      report: validReport(),
      evidence: [],
      trust_radar: {},
      trust_components_aggregate: { reputation: 0.5, corroboration: 0.5, recency: 0.5, manipulation: 0 },
      price_provenance: {},
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data: singleShaped })))
    const result = await apiFetch<ComparisonAnalyzeData>('/api/analyze', undefined, isComparisonAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})
