import { describe, expect, it } from 'vitest'
import {
  COMPETITION_COINS,
  COMPETITION_QUESTION_TEMPLATES,
  pickCompetitionQuestion,
} from './competitionQuestionPicker'

describe('competition question picker', () => {
  it('uses the five-asset coin source of truth', () => {
    expect(COMPETITION_COINS).toEqual(['BTC', 'ETH', 'SOL', 'BNB', 'XRP'])
  })

  it('uses an injectable deterministic random source to pick exactly one question', () => {
    expect(pickCompetitionQuestion('SOL', () => 0)).toEqual({
      id: 'competition-sol-1',
      coin: 'SOL',
      query: `請分析 SOL：${COMPETITION_QUESTION_TEMPLATES[0]}`,
    })
    expect(pickCompetitionQuestion('XRP', () => 0.999).query).toContain(COMPETITION_QUESTION_TEMPLATES.at(-1))
  })

  it('rejects invalid random sources and empty banks', () => {
    expect(() => pickCompetitionQuestion('BTC', () => 0, [])).toThrow(/must not be empty/)
    expect(() => pickCompetitionQuestion('BTC', () => 1)).toThrow(RangeError)
  })
})
