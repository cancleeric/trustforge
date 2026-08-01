// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Evidence } from '../lib/types'
import ProConPanel from './ProConPanel'
import { HermesI18nProvider } from '../hermes/hermesI18n'

function evidence(direction: string, trust: number): Evidence {
  return {
    source: `${direction}-source`, fetched_at: '2026-08-01T00:00:00Z',
    content_reference: 'evidence', related_claim: 'claim', source_url: '', kind: 'news',
    direction, trust, trust_components: {}, flags: [], info_flags: [],
  }
}

describe('ProConPanel', () => {
  it('renders pro/con trust averages from claim direction', () => {
    render(
      <HermesI18nProvider><ProConPanel
        facts={['ETF 資金流入']}
        contrarian={['交易所供給增加']}
        evidence={[evidence('bullish', 0.8), evidence('bullish', 0.6), evidence('bearish', 0.4)]}
        signal={null}
      /></HermesI18nProvider>,
    )
    expect(screen.getByText('ETF 資金流入')).toBeInTheDocument()
    expect(screen.getByText('交易所供給增加')).toBeInTheDocument()
    expect(screen.getByText('平均信任 0.70')).toBeInTheDocument()
    expect(screen.getByText('平均信任 0.40')).toBeInTheDocument()
  })

  it('places divergence and insufficient insights in unresolved area', () => {
    render(
      <HermesI18nProvider><ProConPanel
        facts={[]}
        contrarian={[]}
        evidence={[]}
        signal={{ type: 'divergence', summary: '價格與情緒背離' }}
        insights={[{
          insight_type: 'coverage', title: '樣本不足', summary: '只有一個來源',
          direction: 'ambiguous', strength: 0, coverage: 'insufficient',
          coverage_reason: 'too few sources', contributions: [], claim_ids: [],
        }]}
      /></HermesI18nProvider>,
    )
    expect(screen.getByLabelText('矛盾與未決')).toBeInTheDocument()
    expect(screen.getByText('價格與情緒背離')).toBeInTheDocument()
    expect(screen.getByText('樣本不足：只有一個來源')).toBeInTheDocument()
  })
})
