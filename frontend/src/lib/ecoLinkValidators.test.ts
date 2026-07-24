// codex-review PR #655（誠實/correctness）：`isEcoLinkResponseData` 對
// `verdict` 必須是嚴格白名單，不能只驗 `typeof === 'string'`——否則畸形
// /未知 verdict 會被當成合法資料往下游塞，`EcoLinkImpactPanel` 沒有對應
// 分支時會 fall through 到 `possible_relation` 的正面結論渲染，等於把
// 「不確定」誤報成「可能相關」。

import { describe, expect, it } from 'vitest'
import { isEcoLinkResponseData } from './validators'

function baseImpactPath() {
  return {
    event_id: 'upgrade:arb:stylus',
    path: ['asset:arb', 'asset:eth'],
    direction: 'mixed',
    confidence: 0.85,
    official_source_url: 'https://arbitrum.foundation/upgrade/stylus',
  }
}

describe('isEcoLinkResponseData — verdict 嚴格白名單', () => {
  it('verdict: possible_relation 且 illustrative:true 時視為合法', () => {
    expect(
      isEcoLinkResponseData({
        illustrative: true,
        verdict: 'possible_relation',
        message: '可能相關',
        impact_paths: [baseImpactPath()],
      }),
    ).toBe(true)
  })

  it('verdict: insufficient_data 時視為合法', () => {
    expect(
      isEcoLinkResponseData({
        illustrative: true,
        verdict: 'insufficient_data',
        message: '資料不足，無法判定',
        impact_paths: [],
      }),
    ).toBe(true)
  })

  it('verdict 為未知字串時一律 parse_error（不得 fall through 到 possible_relation）', () => {
    expect(
      isEcoLinkResponseData({
        illustrative: true,
        verdict: 'unexpected_future_value',
        message: '某種未知結論',
        impact_paths: [],
      }),
    ).toBe(false)
  })

  it('illustrative 缺席或非 true 時一律 parse_error', () => {
    expect(
      isEcoLinkResponseData({
        verdict: 'possible_relation',
        message: '可能相關',
        impact_paths: [baseImpactPath()],
      }),
    ).toBe(false)
    expect(
      isEcoLinkResponseData({
        illustrative: false,
        verdict: 'possible_relation',
        message: '可能相關',
        impact_paths: [baseImpactPath()],
      }),
    ).toBe(false)
  })
})
