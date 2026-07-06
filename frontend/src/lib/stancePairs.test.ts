// #13：跨源分歧「來源數」按 source 去重——CrossSourceSignalPanel 用的
// `groupByStance` 單元測試。規格見 docs/plans/PLAN-next-worldfirst-depth.md §1。

import { describe, expect, it } from 'vitest'
import { groupByStance } from './stancePairs'
import type { StancePair } from './types'

function pair(source: string, stance: string, claim_id: string): StancePair {
  return { source, stance, claim_id, text: `text-${claim_id}` }
}

describe('groupByStance', () => {
  it('優先使用後端 distinct_sources，不再對 stance_pairs 重複計數', () => {
    // 後端 distinct_sources 已依 source 去重；即使 stance_pairs 內同來源
    // 出現兩筆，也必須以 distinct_sources 為準。
    const signal = {
      stance_pairs: [
        pair('coindesk', 'bullish', 'a1'),
        pair('coindesk', 'bullish', 'a2'), // 同來源、同陣營、不同 claim
        pair('decrypt', 'bearish', 'b1'),
      ],
      distinct_sources: {
        bullish: [pair('coindesk', 'bullish', 'a1')],
        bearish: [pair('decrypt', 'bearish', 'b1')],
      },
    }
    const { bullish, bearish } = groupByStance(signal)
    expect(bullish).toHaveLength(1)
    expect(bearish).toHaveLength(1)
    expect(bullish[0].source).toBe('coindesk')
  })

  it('無 distinct_sources 時 fallback：前端自行依 source 去重同一陣營', () => {
    const signal = {
      stance_pairs: [
        pair('coindesk', 'bullish', 'a1'),
        pair('coindesk', 'bullish', 'a2'), // 同來源、同陣營 → 去重後只留一筆
        pair('decrypt', 'bearish', 'b1'),
      ],
    }
    const { bullish, bearish } = groupByStance(signal)
    expect(bullish).toHaveLength(1)
    expect(bullish[0].claim_id).toBe('a1') // 保留第一筆出現的代表
    expect(bearish).toHaveLength(1)
  })

  it('跨陣營不去重：同來源分屬 bullish/bearish（自我矛盾）各自保留', () => {
    const signal = {
      stance_pairs: [
        pair('coindesk', 'bullish', 'a1'),
        pair('coindesk', 'bearish', 'a2'),
      ],
    }
    const { bullish, bearish } = groupByStance(signal)
    expect(bullish).toHaveLength(1)
    expect(bearish).toHaveLength(1)
    expect(bullish[0].source).toBe('coindesk')
    expect(bearish[0].source).toBe('coindesk')
  })

  it('不同來源各一則 → 行為不變（回歸鎖）', () => {
    const signal = {
      stance_pairs: [
        pair('coindesk', 'bullish', 'a1'),
        pair('decrypt', 'bearish', 'b1'),
      ],
    }
    const { bullish, bearish } = groupByStance(signal)
    expect(bullish).toHaveLength(1)
    expect(bearish).toHaveLength(1)
  })

  it('signal 為 null/undefined 或無 stance_pairs → 回傳空陣列', () => {
    expect(groupByStance(null)).toEqual({ bullish: [], bearish: [] })
    expect(groupByStance(undefined)).toEqual({ bullish: [], bearish: [] })
    expect(groupByStance({})).toEqual({ bullish: [], bearish: [] })
  })

  it('fallback 去重正規化大小寫/空白：" CoinDesk "/"coindesk"/"COINDESK" 收斂成 1', () => {
    const signal = {
      stance_pairs: [
        pair(' CoinDesk ', 'bullish', 'a1'),
        pair('coindesk', 'bullish', 'a2'),
        pair('COINDESK', 'bullish', 'a3'),
        pair('decrypt', 'bearish', 'b1'),
      ],
    }
    const { bullish, bearish } = groupByStance(signal)
    expect(bullish).toHaveLength(1)
    expect(bullish[0].source).toBe(' CoinDesk ') // 顯示保留第一筆出現的原始字串
    expect(bearish).toHaveLength(1)
    expect(bearish[0].source).toBe('decrypt') // 真實不同來源不受影響
  })
})
