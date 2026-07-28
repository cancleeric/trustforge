// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import MultiAngleOverview from './MultiAngleOverview'

vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data }: { data: { report: { coin: string } } }) => (
    <div data-testid="inline-analysis-report">{data.report.coin} inline detail</div>
  ),
}))

vi.mock('../lib/multiAngleEndpoints', async (original) => {
  const actual = await original<typeof import('../lib/multiAngleEndpoints')>()
  return {
    ...actual,
    fetchMultiAngleReport: vi.fn().mockResolvedValue({
      multi_angle: {
        coin: 'BTC', snapshot_id: 'snap-1', consensus: '偏多',
        decision_state: 'normal', consensus_confidence: 0.7,
        evidence_independence: 1, conflicts: [], agreement_matrix: {},
        synthesis_summary: 'summary', limits: [], generated_at: '2026-01-01',
        angles: [{
          angle: 'risk', qtype: 'multi_source', direction: '偏多',
          calibrated_confidence: 0.7, decision_state: 'normal',
          key_basis: [], evidence_sources: ['a'], evidence_count: 1,
          market_judgment: 'ok', snapshot_id: 'snap-1', job_id: 'job-risk',
          question: 'risk question',
        }],
      },
    }),
  }
})

vi.mock('../lib/endpoints', () => ({
  getAnalysisJob: vi.fn().mockResolvedValue({
    ok: true,
    data: { state: 'completed', result: { report: { coin: 'BTC' } } },
  }),
}))

describe('MultiAngleOverview drilldown', () => {
  it('expands the selected same-snapshot job inline and collapses it', async () => {
    render(<HermesI18nProvider><MultiAngleOverview coin="BTC" /></HermesI18nProvider>)
    const [row] = await screen.findAllByRole('button', { name: /風險評估 詳細/ })
    fireEvent.click(row)
    expect(await screen.findByTestId('inline-analysis-report')).toHaveTextContent('BTC inline detail')
    fireEvent.click(row)
    await waitFor(() => expect(screen.queryByTestId('inline-analysis-report')).not.toBeInTheDocument())
  })
})
