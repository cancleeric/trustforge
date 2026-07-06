// #86 codex 點名補測：跨幣信任排行排序邏輯，含平手行為。
//
// codex 窮舉終審 LOW 修復（平手 rank）：`computeCompetitionRanks()` 的
// competition ranking（1224 制）測試。

import { describe, expect, it } from 'vitest'
import { computeCompetitionRanks, sortCoinsByTrustScoreDesc } from './sortCoins'
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

describe('computeCompetitionRanks — 平手用 1224 制（codex 窮舉終審 LOW 修復）', () => {
  it('四幣分數 [10, 8, 8, 5]（已降序）→ 名次 [1, 2, 2, 4]，不是奧運排名制的 [1,2,2,3]', () => {
    const coins = [
      coin({ coin: 'A', trust_score: 10 }),
      coin({ coin: 'B', trust_score: 8 }),
      coin({ coin: 'C', trust_score: 8 }),
      coin({ coin: 'D', trust_score: 5 }),
    ]
    expect(computeCompetitionRanks(coins)).toEqual([1, 2, 2, 4])
  })

  it('全部不同分數 → 名次就是 1..N', () => {
    const coins = [
      coin({ coin: 'A', trust_score: 0.9 }),
      coin({ coin: 'B', trust_score: 0.5 }),
      coin({ coin: 'C', trust_score: 0.1 }),
    ]
    expect(computeCompetitionRanks(coins)).toEqual([1, 2, 3])
  })

  it('全部同分 → 全部並列第 1 名', () => {
    const coins = [
      coin({ coin: 'A', trust_score: 0.5 }),
      coin({ coin: 'B', trust_score: 0.5 }),
      coin({ coin: 'C', trust_score: 0.5 }),
    ]
    expect(computeCompetitionRanks(coins)).toEqual([1, 1, 1])
  })

  it('平手發生在中間（非開頭）也正確接續下一個名次', () => {
    const coins = [
      coin({ coin: 'A', trust_score: 0.9 }),
      coin({ coin: 'B', trust_score: 0.6 }),
      coin({ coin: 'C', trust_score: 0.6 }),
      coin({ coin: 'D', trust_score: 0.6 }),
      coin({ coin: 'E', trust_score: 0.2 }),
    ]
    expect(computeCompetitionRanks(coins)).toEqual([1, 2, 2, 2, 5])
  })

  it('不改動輸入陣列', () => {
    const coins = [coin({ coin: 'A', trust_score: 0.9 }), coin({ coin: 'B', trust_score: 0.5 })]
    const original = [...coins]
    computeCompetitionRanks(coins)
    expect(coins).toEqual(original)
  })
})
