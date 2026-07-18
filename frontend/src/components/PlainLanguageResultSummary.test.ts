import { describe, expect, it } from 'vitest'
import { deriveResultReadiness } from '../lib/resultReadiness'
import type { AnalyzeData } from '../lib/types'

function result(decisionState: AnalyzeData['report']['decision_state'], evidenceCount: number, limits: string[] = []) {
  return {
    report: { decision_state: decisionState, limits },
    evidence: Array.from({ length: evidenceCount }, () => ({})),
  } as unknown as AnalyzeData
}

describe('deriveResultReadiness', () => {
  it('never treats no evidence as a ready result', () => expect(deriveResultReadiness(result('normal', 0))).toBe('insufficient'))
  it('marks abstention as insufficient', () => expect(deriveResultReadiness(result('abstain', 3))).toBe('insufficient'))
  it('marks low confidence or explicit limits as limited', () => {
    expect(deriveResultReadiness(result('low_confidence', 3))).toBe('limited')
    expect(deriveResultReadiness(result('normal', 3, ['coverage gap']))).toBe('limited')
  })
  it('only marks supported normal results as ready', () => expect(deriveResultReadiness(result('normal', 3))).toBe('ready'))
})
