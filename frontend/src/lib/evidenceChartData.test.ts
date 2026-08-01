import { describe, expect, it } from 'vitest'
import type { Evidence } from './types'
import { sourceTrustAverages } from './evidenceChartData'

const evidence = (source: string, trust: number): Evidence => ({
  source, trust, fetched_at: '2026-08-01T00:00:00Z', content_reference: source,
  related_claim: 'claim', source_url: '', kind: 'news', trust_components: {}, flags: [], info_flags: [],
})

describe('sourceTrustAverages', () => {
  it('renders one truthful average per source instead of duplicate evidence bars', () => {
    expect(sourceTrustAverages([evidence('coindesk', 0.4), evidence('coindesk', 0.8), evidence('f2pool', 0.9)]))
      .toEqual([{ name: 'F2pool', trust: 90, count: 1 }, { name: 'CoinDesk', trust: 60, count: 2 }])
  })
})
