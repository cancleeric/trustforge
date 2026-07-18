import { describe, expect, it } from 'vitest'
import { recommendAnalysisMode } from './beginnerExperience'

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
