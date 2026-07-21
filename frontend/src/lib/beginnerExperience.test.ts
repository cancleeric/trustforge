import { describe, expect, it } from 'vitest'
import { beginnerQuestion, beginnerTypeForMode, recommendAnalysisMode } from './beginnerExperience'

describe('recommendAnalysisMode', () => {
  it.each([
    ['這則新聞是真的嗎？', 'news'],
    ['最近社群情緒如何', 'sentiment'],
    ['評估代幣經濟與營運基本面', 'fundamentals'],
    ['什麼事件會影響價格上漲', 'catalyst'],
    ['有沒有操縱風險', 'risk'],
  ])('recommends a mode for %s', (question, expected) => {
    expect(recommendAnalysisMode(question)).toBe(expected)
  })
})

describe('beginner task flow helpers', () => {
  it('maps beginner modes to hidden analysis types', () => {
    expect(beginnerTypeForMode('risk')).toBe('multi_source')
    expect(beginnerTypeForMode('news')).toBe('multi_source')
    expect(beginnerTypeForMode('fundamentals')).toBe('hypothesis')
    expect(beginnerTypeForMode('catalyst')).toBe('hypothesis')
  })

  it('builds an asset-scoped beginner question', () => {
    expect(beginnerQuestion('BTC', 'trust')).toContain('BTC')
    expect(beginnerQuestion('BTC', 'trust')).toContain('三個最重要')
  })
})
