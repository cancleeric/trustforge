import type { Evidence } from './types'
import { sourceDisplayName } from './sourceBrand'

export function sourceTrustAverages(evidence: Evidence[]) {
  const sources = evidence.reduce<Record<string, { total: number; count: number }>>((result, item) => {
    const name = sourceDisplayName(item.source)
    const current = result[name] ?? { total: 0, count: 0 }
    result[name] = { total: current.total + item.trust, count: current.count + 1 }
    return result
  }, {})
  return Object.entries(sources)
    .map(([name, score]) => ({ name, trust: Math.round((score.total / score.count) * 100), count: score.count }))
    .sort((a, b) => b.trust - a.trust)
    .slice(0, 10)
}
