// #86 codex 點名補測：跨幣信任排行排序邏輯，含平手行為。

import { describe, expect, it } from 'vitest'
import { sortCoinsByTrustScoreDesc } from './sortCoins'
import type { OverviewCoin } from './types'

function coin(overrides: Partial<OverviewCoin> & { coin: string; trust_score: number }): OverviewCoin {
  return {
    direction: '中性',
    calibrated_confidence: 0.5,
    decision_state: 'normal',
    generated_at: '2026-07-01T00:00:00Z',
    fetched_at_epoch: 1783114801,
    ...overrides,
  }
}

describe('sortCoinsByTrustScoreDesc', () => {
  it('依 trust_score 降序排列', () => {
    const coins = [
      coin({ coin: 'ETH', trust_score: 0.5 }),
      coin({ coin: 'BTC', trust_score: 0.8 }),
      coin({ coin: 'SOL', trust_score: 0.2 }),
    ]
    expect(sortCoinsByTrustScoreDesc(coins).map((c) => c.coin)).toEqual(['BTC', 'ETH', 'SOL'])
  })

  it('平手時維持原始（呼叫端傳入陣列）相對順序，不隨機打亂', () => {
    const coins = [
      coin({ coin: 'BNB', trust_score: 0.5 }),
      coin({ coin: 'XRP', trust_score: 0.5 }),
      coin({ coin: 'BTC', trust_score: 0.9 }),
    ]
    // BTC 最高分排第一；BNB/XRP 同分，維持原陣列裡 BNB 在 XRP 前面的順序。
    expect(sortCoinsByTrustScoreDesc(coins).map((c) => c.coin)).toEqual(['BTC', 'BNB', 'XRP'])
  })

  it('全部平手時完全維持原始順序', () => {
    const coins = [
      coin({ coin: 'ETH', trust_score: 0.5 }),
      coin({ coin: 'BTC', trust_score: 0.5 }),
      coin({ coin: 'SOL', trust_score: 0.5 }),
    ]
    expect(sortCoinsByTrustScoreDesc(coins).map((c) => c.coin)).toEqual(['ETH', 'BTC', 'SOL'])
  })

  it('不就地改動原始陣列（回傳新陣列）', () => {
    const coins = [coin({ coin: 'ETH', trust_score: 0.5 }), coin({ coin: 'BTC', trust_score: 0.8 })]
    const original = [...coins]
    sortCoinsByTrustScoreDesc(coins)
    expect(coins).toEqual(original)
  })
})
