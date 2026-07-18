import { normalizeDecisionState, type AnalyzeData } from './types'

export type ResultReadiness = 'ready' | 'limited' | 'insufficient'

export function deriveResultReadiness(data: AnalyzeData): ResultReadiness {
  const state = normalizeDecisionState(data.report.decision_state)
  if (state === 'abstain' || data.evidence.length === 0) return 'insufficient'
  if (state === 'low_confidence' || data.report.limits.length > 0) return 'limited'
  return 'ready'
}
