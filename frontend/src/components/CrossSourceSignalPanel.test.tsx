// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import CrossSourceSignalPanel from './CrossSourceSignalPanel'
import { apiFetch } from '../lib/apiClient'
import { isAnalyzeData } from '../lib/validators'
import type { AnalyzeData, CrossSourceSignal } from '../lib/types'

// issue #21（CISO-LOW）：情緒類（news/social）訊號僅有 1 個獨立來源時，
// 顯示「單一來源主導」透明徽章；多源時不顯示。核心信任分數計算不在本檔
// 測試範圍（見 tests/test_cross_source_signal.py 後端測試），這裡只驗證
// UI 依 `sentiment_source_count` 正確顯示/隱藏徽章。

function makeSignal(overrides: Partial<CrossSourceSignal> = {}): CrossSourceSignal {
  return {
    type: 'consensus',
    summary: '客觀與情緒同向偏多，訊號一致。',
    objective_direction: 'bullish',
    sentiment_direction: 'bullish',
    ...overrides,
  }
}

const BADGE_TEXT = '單一來源主導'

describe('CrossSourceSignalPanel', () => {
  it('signal 為 null 時顯示 fallback 文字，不顯示單源徽章', () => {
    render(<CrossSourceSignalPanel signal={null} />)
    expect(
      screen.getByText('目前未偵測到同議題、跨源、語意矛盾的顯著訊號。')
    ).toBeInTheDocument()
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('sentiment_source_count === 1（共識）時應顯示「單一來源主導」徽章', () => {
    const signal = makeSignal({ type: 'consensus', sentiment_source_count: 1 })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(BADGE_TEXT)).toBeInTheDocument()
    expect(screen.getByText(signal.summary)).toBeInTheDocument()
  })

  it('sentiment_source_count === 1（背離）時應顯示「單一來源主導」徽章', () => {
    const signal = makeSignal({
      type: 'divergence',
      summary: '客觀數據偏多、情緒類偏空，呈背離，建議交叉驗證、留意轉折。',
      sentiment_direction: 'bearish',
      sentiment_source_count: 1,
    })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(BADGE_TEXT)).toBeInTheDocument()
  })

  it('sentiment_source_count === 2（多源）時不應顯示徽章', () => {
    const signal = makeSignal({ sentiment_source_count: 2 })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('sentiment_source_count 缺欄位（如 _stance_pair_signal 備援分支/舊快照）時不應顯示徽章', () => {
    const signal = makeSignal({ sentiment_source_count: undefined })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('徽章不影響既有 summary / claim ids 渲染', () => {
    const signal = makeSignal({
      sentiment_source_count: 1,
      supporting_claim_ids: ['c1', 'c2'],
    })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(/佐證 claim_ids：c1、c2/)).toBeInTheDocument()
  })
})

// issue #21（CISO-LOW）對抗審 LOW：以上所有測試都是手餵 prop，繞過了
// `apiFetch` + `isAnalyzeData` 這條真實資料管線——若 validator 對
// `cross_source_signal.sentiment_source_count` 的接線鬆脫（如型別檢查漏
// 掉這個欄位、或欄位被 validator 意外剝離），手餵 prop 的測試完全不會
// 發現，因為它跳過了「後端 JSON → 信封解析 → payload schema 驗證」這一
// 段。這裡改走真實 API payload：`apiFetch` 收到的原始 JSON → `isAnalyzeData`
// parse → 取出 parse 後的 `report.cross_source_signal` → 餵進元件，確保
// validator 對這個欄位的接線真的有守住（不只是 TypeScript 編譯期型別宣告）。
describe('CrossSourceSignalPanel — 真實 API payload → validator → 元件整鏈', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function jsonResponse(body: unknown): Response {
    return { status: 200, json: () => Promise.resolve(body) } as unknown as Response
  }

  /** 最小合法的 AnalyzeData，對齊 `apiClient.test.ts` 的
   * `validAnalyzeData()`（後端 `_snapshot_dict()`/`/api/analyze` 真實信封
   * 形狀），僅補上本測試需要斷言的 `cross_source_signal`。 */
  function rawAnalyzePayload(crossSourceSignal: CrossSourceSignal): { ok: true; data: AnalyzeData } {
    return {
      ok: true,
      data: {
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
          cross_source_signal: crossSourceSignal,
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
      },
    }
  }

  it('真實 JSON payload（sentiment_source_count=1）經 apiFetch+isAnalyzeData parse 後餵進元件 → 顯示單源徽章', async () => {
    const payload = rawAnalyzePayload({
      type: 'consensus',
      summary: '客觀與情緒同向偏多，訊號一致。',
      objective_direction: 'bullish',
      sentiment_direction: 'bullish',
      sentiment_source_count: 1,
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (!result.ok) return

    render(<CrossSourceSignalPanel signal={result.data.report.cross_source_signal} />)
    expect(screen.getByText(BADGE_TEXT)).toBeInTheDocument()
  })

  it('真實 JSON payload（sentiment_source_count=2）經 apiFetch+isAnalyzeData parse 後餵進元件 → 不顯示單源徽章', async () => {
    const payload = rawAnalyzePayload({
      type: 'consensus',
      summary: '客觀與情緒同向偏多，訊號一致。',
      objective_direction: 'bullish',
      sentiment_direction: 'bullish',
      sentiment_source_count: 2,
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    const result = await apiFetch<AnalyzeData>('/api/analyze', undefined, isAnalyzeData)
    expect(result.ok).toBe(true)
    if (!result.ok) return

    render(<CrossSourceSignalPanel signal={result.data.report.cross_source_signal} />)
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })
})
