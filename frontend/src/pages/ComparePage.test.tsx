// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getComparisonSnapshot } from '../lib/endpoints'
import ComparePage from './ComparePage'

vi.mock('../lib/endpoints', () => ({
  getComparisonSnapshot: vi.fn(),
  registerAnalysisComparison: vi.fn(),
}))

vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data, heading }: { data: { report: { asset_intrinsic_assessment?: unknown } }; heading: string }) => (
    <div aria-label={heading}>{JSON.stringify(data.report.asset_intrinsic_assessment)}</div>
  ),
}))

function renderPage(initialUrl = '/compare') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <ComparePage />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

describe('ComparePage · 同層 Peer 比較入口（模組③ Wave 3）', () => {
  it('提供連到獨立 /peer-metrics 頁的連結，不把 Peer 比較掛在 COIN_POOL 雙幣表單上', () => {
    renderPage()
    const link = screen.getByRole('link', { name: /查看同層 Peer 比較/ })
    expect(link).toHaveAttribute('href', '/peer-metrics')
  })

  it('passes each independent intrinsic assessment to its matching comparison report', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue({
      ok: true,
      data: {
        version: 'test',
        report_a: { coin: 'BTC', calibrated_confidence: 0.5, asset_intrinsic_assessment: { asset_id: 'asset:btc' } },
        report_b: { coin: 'BNB', calibrated_confidence: 0.5, asset_intrinsic_assessment: { asset_id: 'asset:bnb' } },
        evidence_a: [], evidence_b: [],
        trust_components_aggregate_a: { reputation: null, corroboration: null, recency: null, manipulation: null },
        trust_components_aggregate_b: { reputation: null, corroboration: null, recency: null, manipulation: null },
        trust_radar_a: {}, trust_radar_b: {}, price_provenance_a: {}, price_provenance_b: {},
        execution_log: [],
      },
    } as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByLabelText('幣種 A · BTC')).toHaveTextContent('asset:btc'))
    expect(screen.getByLabelText('幣種 B · BNB')).toHaveTextContent('asset:bnb')
  })
})
