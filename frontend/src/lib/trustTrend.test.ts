// #89：AnalysisReportView 信任趨勢區塊「今日對比昨日」變化量——沿用
// `sortCoins.test.ts`/`manipRisk.test.ts` 既有慣例，純函式直接測，不需要
// element/DOM。
//
// #62 護欄條款回歸測試重點：這裡的 fixture 只構造 `TrustHistoryEntry[]`
// （對應 `/api/history` 回傳形狀），確保 `computeTrendDelta` 的簽名本身
// 就不可能吃到 `/api/analyze`／`/api/overview` 的即時值——不是「這次沒
// 傳」而是「型別上就沒有管道傳進去」。

import { describe, expect, it } from 'vitest'
import { computeTrendDelta, trendComparisonLabel } from './trustTrend'
import type { TrustHistoryEntry } from './types'

function entry(overrides: Partial<TrustHistoryEntry> & { date: string; trust_score: number }): TrustHistoryEntry {
  return {
    coin: 'BTC',
    direction: '偏多',
    calibrated_confidence: 0.6,
    decision_state: 'normal',
    generated_at: `${overrides.date}T00:00:00Z`,
    ...overrides,
  }
}

describe('computeTrendDelta', () => {
  it('0 筆 → 尚無對比基準', () => {
    expect(computeTrendDelta([])).toEqual({ kind: 'no-baseline' })
  })

  it('1 筆（單天資料）→ 尚無對比基準', () => {
    const history = [entry({ date: '2026-07-06', trust_score: 0.72 })]
    expect(computeTrendDelta(history)).toEqual({ kind: 'no-baseline' })
  })

  it('2 筆，今日較昨日上升 → delta 為正、latest/previous 對應正確', () => {
    const yesterday = entry({ date: '2026-07-05', trust_score: 0.69 })
    const today = entry({ date: '2026-07-06', trust_score: 0.72 })
    const result = computeTrendDelta([yesterday, today])
    expect(result.kind).toBe('delta')
    if (result.kind === 'delta') {
      expect(result.latest).toEqual(today)
      expect(result.previous).toEqual(yesterday)
      expect(result.delta).toBeCloseTo(0.03, 10)
    }
  })

  it('今日較昨日下降 → delta 為負', () => {
    const yesterday = entry({ date: '2026-07-05', trust_score: 0.8 })
    const today = entry({ date: '2026-07-06', trust_score: 0.75 })
    const result = computeTrendDelta([yesterday, today])
    expect(result.kind).toBe('delta')
    if (result.kind === 'delta') {
      expect(result.delta).toBeCloseTo(-0.05, 10)
    }
  })

  it('今日與昨日相同 → delta 為 0（不是「尚無基準」，是真實算出 0）', () => {
    const yesterday = entry({ date: '2026-07-05', trust_score: 0.5 })
    const today = entry({ date: '2026-07-06', trust_score: 0.5 })
    const result = computeTrendDelta([yesterday, today])
    expect(result.kind).toBe('delta')
    if (result.kind === 'delta') {
      expect(result.delta).toBe(0)
    }
  })

  it('3 筆以上 → 只取陣列最後兩筆（最新兩天），不是頭兩筆', () => {
    const history = [
      entry({ date: '2026-07-01', trust_score: 0.1 }),
      entry({ date: '2026-07-05', trust_score: 0.4 }),
      entry({ date: '2026-07-06', trust_score: 0.5 }),
    ]
    const result = computeTrendDelta(history)
    expect(result.kind).toBe('delta')
    if (result.kind === 'delta') {
      expect(result.latest.date).toBe('2026-07-06')
      expect(result.previous.date).toBe('2026-07-05')
      expect(result.delta).toBeCloseTo(0.1, 10)
    }
  })
})

// codex 複審 MEDIUM 修復（CEO 終批方案一）：computeTrendDelta 取陣列最後
// 兩筆時不驗證日期是否真的相鄰——排程失敗/缺漏會讓 latest/previous 實際
// 相隔數天，這裡鎖住 trendComparisonLabel 的兩種分支，避免文案一律寫死
// 「較昨日」誤導使用者。
describe('trendComparisonLabel', () => {
  it('相鄰日（差 1 天）→ 特例顯示「較昨日」', () => {
    const previous = entry({ date: '2026-07-05', trust_score: 0.6 })
    const latest = entry({ date: '2026-07-06', trust_score: 0.65 })
    expect(trendComparisonLabel(previous, latest)).toBe('較昨日')
  })

  it('排程缺漏、非相鄰日（例如差 3 天）→ 誠實標「較前次快照（日期）」，不得裝作較昨日', () => {
    const previous = entry({ date: '2026-07-03', trust_score: 0.6 })
    const latest = entry({ date: '2026-07-06', trust_score: 0.65 })
    expect(trendComparisonLabel(previous, latest)).toBe('較前次快照（2026-07-03）')
  })

  it('跨月相鄰日（7/31 → 8/01，差 1 天）→ 仍正確判定為「較昨日」', () => {
    const previous = entry({ date: '2026-07-31', trust_score: 0.5 })
    const latest = entry({ date: '2026-08-01', trust_score: 0.52 })
    expect(trendComparisonLabel(previous, latest)).toBe('較昨日')
  })
})
