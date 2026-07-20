// Runtime 信封驗證測試——codex code review 指出：任何有 `ok` 屬性的
// JSON 都被無條件轉型，畸形信封（`ok` 非 boolean、成功缺 data、失敗缺
// error）會讓呼叫端讀到 undefined 而白屏。這裡逐一構造畸形回應，斷言
// `apiFetch` 一律回傳結構化的錯誤結果（觸發錯誤 UI）、不 throw、不讓
// 半成品資料流出去。

/// <reference types="node" />
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from './apiClient'
import { isAnalyzeData, isHealthData, isOverviewData } from './validators'
import type { AnalyzeData, HealthData, OverviewCoin, OverviewData } from './types'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/** codex 建議：用 curl 存下的真實 live `/api/overview`、`/api/analyze`
 * 回應當 fixture，驗證 guard 不會誤殺真實後端資料（過嚴驗證比不驗更糟）。
 * 見 `src/lib/__fixtures__/README.md`。 */
function loadFixtureEnvelope(name: string): { ok: boolean; data?: unknown } {
  return JSON.parse(readFileSync(path.join(__dirname, '__fixtures__', name), 'utf-8'))
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

function nonJsonResponse(status: number): Response {
  return {
    status,
    json: () => Promise.reject(new SyntaxError('Unexpected end of JSON input')),
  } as unknown as Response
}

/** 通用「只驗證信封層、不管 payload 細節」的 validator，用來隔離測試
 * 信封解析邏輯本身（payload schema 由 `isOverviewData`/`isHealthData` 等
 * 另外覆蓋）。 */
const anyData = (_data: unknown): _data is unknown => true

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiFetch — 畸形信封（不 throw、觸發錯誤 UI）', () => {
  it('ok 是字串 "false"（非 boolean）→ parse_error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: 'false' })))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('ok:true 但沒有 data → parse_error（用真實 validator，不能靠寬鬆 guard 矇混過去）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true })))
    const result = await apiFetch<HealthData>('/api/health', undefined, isHealthData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('ok:false 但沒有 error → parse_error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: false })))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('ok:false 但 error 缺 message → parse_error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { ok: false, error: { code: 'bad' } })),
    )
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('body 非 JSON（json() 拋錯）→ parse_error，不 throw', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(nonJsonResponse(200)))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('HTTP 500 空 body（json() 拋錯）→ parse_error，不 throw', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(nonJsonResponse(500)))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('body 不是物件（例如陣列）→ parse_error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, [1, 2, 3])))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('fetch 本身 reject（網路錯誤）→ network_error，不 throw', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('network_error')
  })
})

describe('apiFetch — 端點 payload schema 驗證', () => {
  it('/api/health data 缺 uptime_seconds → parse_error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(200, { ok: true, data: { status: 'ok', version: '1.0' } }),
      ),
    )
    const result = await apiFetch<HealthData>('/api/health', undefined, isHealthData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('/api/overview data.coins 非陣列 → parse_error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data: { coins: 'nope' } })),
    )
    const result = await apiFetch('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})

/** 測試用：把任意值當成可自由塞畸形巢狀欄位的 mutable record，避免逐處
 * `as any` 造成 oxlint no-explicit-any 噪音——僅用於建構測試 fixture，
 * 不影響 production 程式碼路徑。 */
function asMutable(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>
}

/** 完整合法的 AnalyzeData——巢狀驗證測試的基準，每個 case 用
 * `structuredClone` 複製後只動一個欄位，避免互相污染。 */
function validAnalyzeData(): AnalyzeData {
  return {
    version: 'v0.5.16',
    report: {
      coin: 'BTC',
      question_type: 'multi_source',
      question: '分析BTC近期市場狀況',
      market_judgment: '中性',
      facts: ['fact1'],
      inferences: ['inference1'],
      key_basis: [],
      confidence: 0.6,
      limits: [],
      could_flip: [],
      contrarian: [],
      generated_at: '2026-07-04T00:00:00Z',
      direction: '中性',
      cross_source_signal: null,
      calibrated_confidence: 0.65,
      decision_state: 'normal',
    },
    evidence: [],
    trust_radar: {
      price: {
        label: '價格信任',
        has_data: true,
        trust: 0.8,
        n_sources: 2,
        n_evidence: 3,
        single_source: false,
      },
    },
    trust_components_aggregate: {
      reputation: 0.8,
      corroboration: 0.5,
      recency: 0.6,
      manipulation: 0.0,
    },
    price_provenance: {
      ohlcv: {
        content_reference: 'BTC OHLCV 2026-07-04',
        fetched_at: '2026-07-04T00:00:00Z',
        source_url: 'https://example.com/ohlcv',
      },
    },
    execution_log: [],
  }
}

describe('apiFetch — 巢狀 payload 驗證（trust_radar / price_provenance）', () => {
  it('price_provenance.ohlcv 是 null（非物件）→ parse_error', async () => {
    const data = validAnalyzeData()
    asMutable(data.price_provenance).ohlcv = null
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('trust_radar.price 是 null（非物件）→ parse_error', async () => {
    const data = validAnalyzeData()
    asMutable(data.trust_radar).price = null
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('trust_radar.price 是 primitive（字串）→ parse_error', async () => {
    const data = validAnalyzeData()
    asMutable(data.trust_radar).price = 'not-a-dimension'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('trust_radar.price 缺 has_data 欄位 → parse_error', async () => {
    const data = validAnalyzeData()
    delete asMutable(data.trust_radar.price).has_data
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('price_provenance.ohlcv 缺 source_url 欄位 → parse_error', async () => {
    const data = validAnalyzeData()
    delete asMutable(data.price_provenance.ohlcv).source_url
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('cross_source_signal.stance_pairs 是數字（非陣列，非 nullish，會讓 for...of 直接 throw）→ parse_error', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'divergence', summary: '分歧' }
    asMutable(data.report.cross_source_signal).stance_pairs = 123
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('cross_source_signal.stance_pairs 陣列元素缺 source 欄位 → parse_error', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = {
      type: 'divergence',
      summary: '分歧',
      stance_pairs: [asMutable({ stance: 'bullish' }) as unknown as { source: string; stance: string }],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('完整合法的巢狀 AnalyzeData（含 stance_pairs）→ 正常回傳', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = {
      type: 'divergence',
      summary: '分歧',
      stance_pairs: [{ source: 'CoinDesk', stance: 'bullish', text: '看漲' }],
      supporting_claim_ids: ['c1'],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  // #13 codex 追加 MEDIUM：distinct_sources 缺欄位要放行（stale 後端相容，
  // `groupByStance` 有 client fallback），但存在時形狀畸形要 parse_error
  // （不可讓 `.map()` 在 render 時直接崩，同 `/costs` 那次教訓）。

  it('cross_source_signal 缺 distinct_sources（舊/stale 後端回應）→ 正常回傳（放行，走 client fallback）', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = {
      type: 'divergence',
      summary: '分歧',
      stance_pairs: [{ source: 'CoinDesk', stance: 'bullish', text: '看漲' }],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
  })

  it('cross_source_signal.distinct_sources.bullish 是數字（非陣列）→ parse_error（不 crash，不讓 .map() 炸掉）', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'divergence', summary: '分歧' }
    asMutable(data.report.cross_source_signal).distinct_sources = { bullish: 123, bearish: [] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('cross_source_signal.distinct_sources 本身是陣列（非物件）→ parse_error', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'divergence', summary: '分歧' }
    asMutable(data.report.cross_source_signal).distinct_sources = []
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('cross_source_signal.distinct_sources.bearish 陣列元素缺 source 欄位 → parse_error', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'divergence', summary: '分歧' }
    asMutable(data.report.cross_source_signal).distinct_sources = {
      bullish: [],
      bearish: [asMutable({ stance: 'bearish' })],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('完整合法的巢狀 AnalyzeData（含正常 distinct_sources）→ 正常回傳', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = {
      type: 'divergence',
      summary: '分歧',
      stance_pairs: [
        { source: 'CoinDesk', stance: 'bullish', text: '看漲' },
        { source: 'Decrypt', stance: 'bearish', text: '看跌' },
      ],
      distinct_sources: {
        bullish: [{ source: 'CoinDesk', stance: 'bullish', text: '看漲' }],
        bearish: [{ source: 'Decrypt', stance: 'bearish', text: '看跌' }],
      },
      supporting_claim_ids: ['c1', 'c2'],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  // issue #21（CISO-LOW）必補 3：`sentiment_source_count` 選填數字欄位——
  // 缺欄位（`_stance_pair_signal()` 備援分支/舊快照）要放行，存在時型別
  // 不對（如字串 `'1'`）要 parse_error，不能悄悄放行讓 `=== 1` 永遠比對
  // 失敗、徽章悄悄漏顯示（同本檔其餘欄位一貫慣例）。

  it('cross_source_signal 缺 sentiment_source_count（舊/stale 後端回應）→ 正常回傳', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'consensus', summary: '共識' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
  })

  it('cross_source_signal.sentiment_source_count 是數字 1 → 正常回傳', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'consensus', summary: '共識', sentiment_source_count: 1 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('cross_source_signal.sentiment_source_count 是字串 "1"（非數字）→ parse_error', async () => {
    const data = validAnalyzeData()
    data.report.cross_source_signal = { type: 'consensus', summary: '共議' }
    asMutable(data.report.cross_source_signal).sentiment_source_count = '1'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  // issue #106 D0.4 三態誠實合約「未評估 ≠ 零」：本輪無可用分項資料時，
  // 後端回傳的 `trust_components_aggregate` 4 個分項值皆為 null（絕非 0）。
  // `isAnalyzeData` 必須放行這種「未評分」形狀，不能過嚴把整張分析卡誤殺成
  // parse_error；前端 `TrustBreakdown` 再把 null 渲染成「暫無評分」中性態。
  it('trust_components_aggregate 全為 null（未評分）→ 正常回傳，不被當成畸形', async () => {
    const data = validAnalyzeData()
    data.trust_components_aggregate = {
      reputation: null,
      corroboration: null,
      recency: null,
      manipulation: null,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (result.ok) {
      const tc = result.data.trust_components_aggregate
      expect(tc.reputation).toBeNull()
      expect(tc.manipulation).toBeNull()
    }
  })
})

/** 最小合法的 overview coin——對齊後端 `_snapshot_dict()`：evidence 為
 * 空/None 時完全不帶 `reputation_trace` key（見
 * `tests/test_snapshot_history.py::test_snapshot_dict_omits_reputation_trace_key_when_no_evidence`），
 * 這是合法情況，不是缺資料。 */
function minimalOverviewCoin(): OverviewCoin {
  return {
    coin: 'BTC',
    trust_score: 0.5,
    direction: '中性',
    calibrated_confidence: 0.5,
    decision_state: 'normal',
    generated_at: '2026-07-04T00:00:00Z',
    fetched_at_epoch: 1234567890,
  }
}

describe('apiFetch — reputation_trace 為選填欄位（不能誤殺合法缺席）', () => {
  it('reputation_trace 完全缺席（後端合法情況）→ 正常放行，不是 parse_error', async () => {
    const data: OverviewData = { coins: [minimalOverviewCoin()] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('reputation_trace 存在但是 null（非物件）→ parse_error', async () => {
    const data = { coins: [{ ...minimalOverviewCoin(), reputation_trace: null }] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('reputation_trace 存在但是 primitive（字串）→ parse_error', async () => {
    const data = { coins: [{ ...minimalOverviewCoin(), reputation_trace: 'nope' }] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('reputation_trace 存在但 entry 缺 final 欄位 → parse_error', async () => {
    const data = {
      coins: [
        {
          ...minimalOverviewCoin(),
          reputation_trace: { 'some-source': { prior: 0.9, delta: 0, agree_n: 0, contradict_n: 0 } },
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('reputation_trace 存在且完整合法 → 正常放行', async () => {
    const data = {
      coins: [
        {
          ...minimalOverviewCoin(),
          reputation_trace: {
            'mempool-space-fees': { prior: 0.95, final: 0.95, delta: 0, agree_n: 0, contradict_n: 0 },
          },
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(true)
  })
})

describe('apiFetch — 真實 live API 回應（curl 存下的 fixture，驗 guard 不誤殺）', () => {
  it('live /api/overview 回應 → 正常通過，不是 parse_error', async () => {
    const envelope = loadFixtureEnvelope('live-overview.json')
    expect(envelope.ok).toBe(true) // fixture 本身應該是成功回應，測試前提要成立
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, envelope)))
    const result = await apiFetch<OverviewData>('/api/overview', undefined, isOverviewData)
    expect(result.ok).toBe(true)
  })

  it('live /api/analyze（sample=1）回應 → 正常通過，不是 parse_error', async () => {
    const envelope = loadFixtureEnvelope('live-analyze.json')
    expect(envelope.ok).toBe(true)
    const serialized = JSON.stringify(envelope)
    expect(serialized).not.toContain('[OFFLINE]')
    expect(serialized).not.toContain('would answer')
    expect(serialized).toContain('未執行線上模型生成')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, envelope)))
    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
  })
})

/** 模擬「後端收了請求但不回應」——fetch 永遠不 resolve，只有在傳入的
 * `signal` 被 abort（不論是 apiFetch 內部逾時計時器、還是外部呼叫端主動
 * 取消）時才 reject，跟真實瀏覽器 fetch 的 abort 語意一致。 */
function stallingFetch() {
  return (_url: string, init?: RequestInit): Promise<Response> =>
    new Promise((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) return
      if (signal.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
        return
      }
      signal.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      })
    })
}

describe('apiFetch — timeout / 主動取消（後端 stall 不能永遠 loading）', () => {
  it('後端 stall（fetch 永不 resolve）→ 逾時後回傳 timeout 錯誤，promise 會 settle 不會永遠 pending', async () => {
    vi.stubGlobal('fetch', vi.fn(stallingFetch()))
    const result = await apiFetch('/api/x', undefined, anyData, { timeoutMs: 20 })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('timeout')
  })

  it('呼叫端主動取消（外部 signal abort，如 effect cleanup）→ cancelled，不是 timeout/network_error', async () => {
    vi.stubGlobal('fetch', vi.fn(stallingFetch()))
    const controller = new AbortController()
    const promise = apiFetch('/api/x', undefined, anyData, { signal: controller.signal, timeoutMs: 5000 })
    controller.abort()
    const result = await promise
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('cancelled')
  })

  it('請求被取代（快速切換）：舊請求 abort 後回 cancelled，新請求正常完成拿到資料，互不污染', async () => {
    let callCount = 0
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      callCount += 1
      // 第一次呼叫模擬「已被取代」的舊請求：永遠卡住，只等它被 abort。
      if (callCount === 1) return stallingFetch()(url, init)
      // 第二次呼叫模擬「取代它」的新請求：正常、很快回應。
      return Promise.resolve(
        jsonResponse(200, { ok: true, data: { status: 'ok', version: '1.0', uptime_seconds: 1 } }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const oldController = new AbortController()
    const oldResultPromise = apiFetch<HealthData>('/api/health', undefined, isHealthData, {
      signal: oldController.signal,
      timeoutMs: 5000,
    })
    // 模擬 React effect cleanup：參數變了，取代舊請求。
    oldController.abort()
    const newResult = await apiFetch<HealthData>('/api/health', undefined, isHealthData, { timeoutMs: 5000 })
    const oldResult = await oldResultPromise

    expect(oldResult.ok).toBe(false)
    if (!oldResult.ok) expect(oldResult.error.code).toBe('cancelled')
    expect(newResult.ok).toBe(true)
    if (newResult.ok) expect(newResult.data.uptime_seconds).toBe(1)
  })

  it('正常情境（無逾時、無取消）→ 不受 timeout 機制影響，正常回傳', async () => {
    const data: HealthData = { status: 'ok', version: '1.0', uptime_seconds: 7 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HealthData>('/api/health', undefined, isHealthData, { timeoutMs: 10_000 })
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })
})

describe('apiFetch — 正常信封', () => {
  it('合法成功信封 → 正常回傳 data', async () => {
    const data: HealthData = { status: 'ok', version: '1.0', uptime_seconds: 42 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await apiFetch<HealthData>('/api/health', undefined, isHealthData)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('合法失敗信封 → 原樣回傳 error（不被覆寫成 parse_error）', async () => {
    const envelope = { ok: false, error: { code: 'bad_request', message: '參數錯誤' } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(400, envelope)))
    const result = await apiFetch('/api/x', undefined, anyData)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error.code).toBe('bad_request')
      expect(result.error.message).toBe('參數錯誤')
    }
  })

  it('HTTP>=400 但信封宣稱 ok:true 且形狀合法 → 仍轉成 http_error（雙訊號都檢查）', async () => {
    const data: HealthData = { status: 'ok', version: '1.0', uptime_seconds: 1 }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(500, { ok: true, data })))
    const result = await apiFetch<HealthData>('/api/health', undefined, isHealthData)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('http_error')
  })
})

describe('REGISTER_TIMEOUT_MS (#245)', () => {
  it('REGISTER_TIMEOUT_MS > DEFAULT_TIMEOUT_MS (write endpoints need more headroom)', async () => {
    const { DEFAULT_TIMEOUT_MS, REGISTER_TIMEOUT_MS, ANALYZE_TIMEOUT_MS } = await import('./apiClient')
    expect(REGISTER_TIMEOUT_MS).toBeGreaterThan(DEFAULT_TIMEOUT_MS)
    expect(REGISTER_TIMEOUT_MS).toBeLessThanOrEqual(ANALYZE_TIMEOUT_MS)
    expect(REGISTER_TIMEOUT_MS).toBe(30_000)
  })
})
