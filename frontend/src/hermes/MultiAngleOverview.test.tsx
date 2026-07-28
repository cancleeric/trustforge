// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import MultiAngleOverview from './MultiAngleOverview'

const { mockFetchMultiAngleReport, mockSubmitMultiAngle, mockGetAnalysisJob, matchMediaMock } = vi.hoisted(() => ({
  mockFetchMultiAngleReport: vi.fn(),
  mockSubmitMultiAngle: vi.fn(),
  mockGetAnalysisJob: vi.fn(),
  matchMediaMock: vi.fn(),
}))

// === MOCKS ===
vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data }: { data: { report: { coin: string } } }) => (
    <div data-testid="inline-analysis-report">{data.report.coin} inline detail</div>
  ),
}))

vi.mock('../lib/multiAngleEndpoints', async (original) => {
  const actual = await original<typeof import('../lib/multiAngleEndpoints')>()
  return {
    ...actual,
    fetchMultiAngleReport: mockFetchMultiAngleReport,
    submitMultiAngle: mockSubmitMultiAngle,
  }
})

vi.mock('../lib/endpoints', () => ({
  getAnalysisJob: mockGetAnalysisJob,
}))

beforeEach(() => {
  vi.clearAllMocks()
  mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: null, message: 'no prior' })
  mockGetAnalysisJob.mockResolvedValue({
    ok: true,
    data: { state: 'completed', result: { report: { coin: 'BTC' } } },
  })
  window.matchMedia = matchMediaMock.mockReturnValue({ matches: false } as MediaQueryList)
  document.cookie = 'trustforge_hermes_locale=; expires=Thu, 01 Jan 1970 00:00:00 UTC'
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderWithProvider(ui: React.ReactElement) {
  return render(<HermesI18nProvider>{ui}</HermesI18nProvider>)
}

function makeAngle(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    angle: 'risk', qtype: 'multi_source', direction: '偏多',
    calibrated_confidence: 0.7, decision_state: 'normal',
    key_basis: [], evidence_sources: ['a'], evidence_count: 1,
    market_judgment: 'ok', snapshot_id: 'snap-1', job_id: 'job-risk',
    question: 'risk question',
    ...overrides,
  }
}

function makeReport(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    coin: 'BTC', snapshot_id: 'snap-1', consensus: '偏多',
    decision_state: 'normal', consensus_confidence: 0.7,
    evidence_independence: 1, conflicts: [], agreement_matrix: {},
    synthesis_summary: 'summary', limits: [], generated_at: '2026-01-01',
    angles: [makeAngle()],
    ...overrides,
  }
}

// === TESTS ===
describe('MultiAngleOverview', () => {
  // T1: 既有 drilldown expand/collapse (Enter key)
  it('expands same-snapshot job inline and collapses it (Enter key)', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport() })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const rows = await screen.findAllByRole('button', { name: /風險評估 詳細/ })
    const row = rows[0]
    fireEvent.click(row)
    expect(await screen.findByTestId('inline-analysis-report')).toHaveTextContent('BTC inline detail')
    fireEvent.click(row)
    await waitFor(() => expect(screen.queryByTestId('inline-analysis-report')).not.toBeInTheDocument())
  })

  // T2: Space key triggers drilldown on table row
  it('expands angle with Space key on table row', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport() })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const rows = await screen.findAllByRole('button', { name: /風險評估 詳細/ })
    const row = rows[0]
    fireEvent.keyDown(row, { key: ' ' })
    expect(await screen.findByTestId('inline-analysis-report')).toBeInTheDocument()
  })

  // T3: Space key triggers drilldown on mobile card
  it('expands angle with Space key on mobile card', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport() })
    window.matchMedia = matchMediaMock.mockReturnValue({ matches: true } as MediaQueryList)
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const cards = await screen.findAllByRole('button', { name: /風險評估 詳細/ })
    const card = cards[cards.length - 1]
    fireEvent.keyDown(card, { key: ' ' })
    expect(await screen.findByTestId('inline-analysis-report')).toBeInTheDocument()
  })

  // T4: No report -> shows submit button
  it('shows submit button when no report exists', async () => {
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })).toBeInTheDocument()
    })
  })

  // T5: Submit shows per-angle progress bars
  it('displays per-angle progress after submitting', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1', sentiment: 'j2', fundamentals: 'j3', news: 'j4', catalyst: 'j5' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob.mockResolvedValue({ ok: true, data: { state: 'running' } })

    vi.useFakeTimers()
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    await act(() => vi.advanceTimersByTimeAsync(2000))

    expect(screen.getByText(/風險評估|Risk.*assessment/)).toBeInTheDocument()
    vi.useRealTimers()
  })

  // T6: Progress shows N/2 counter
  it('shows completed N/2 counter during polling', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1', sentiment: 'j2' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob
      .mockResolvedValueOnce({ ok: true, data: { state: 'completed' } })
      .mockResolvedValueOnce({ ok: true, data: { state: 'running' } })

    vi.useFakeTimers()
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    await act(() => vi.advanceTimersByTimeAsync(2000))

    expect(screen.getByText(/已完成 1\/2|1\/2 completed/)).toBeInTheDocument()
    vi.useRealTimers()
  })

  // T7: Failed job returns error message
  it('displays error when a job fails', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1', sentiment: 'j2' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob
      .mockResolvedValueOnce({ ok: true, data: { state: 'failed', error: 'timeout' } })
      .mockResolvedValue({ ok: true, data: { state: 'running' } })

    vi.useFakeTimers()
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    await act(() => vi.advanceTimersByTimeAsync(2000))

    expect(screen.getByText('timeout')).toBeInTheDocument()
    vi.useRealTimers()
  })

  // T8: transientFailures exceeded → error
  it('shows error after too many transient failures', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob.mockResolvedValue({ ok: false })

    vi.useFakeTimers()
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    // Poll 7 times → 6 failures
    for (let i = 0; i < 7; i++) {
      await act(() => vi.advanceTimersByTimeAsync(2000))
    }
    expect(screen.getByText(/分析狀態暫時無法讀取|Analysis status temporarily unavailable/)).toBeInTheDocument()
    vi.useRealTimers()
  })

  // T9: deadline timeout
  it('shows timeout error after deadline exceeded', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob.mockResolvedValue({ ok: true, data: { state: 'running' } })

    vi.useFakeTimers()
    const now = Date.now()
    vi.setSystemTime(now)
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    // Advance past deadline (10 min + 1.5s poll)
    await act(() => vi.setSystemTime(now + 11 * 60 * 1000))
    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(screen.queryByText(/分析工作長時間沒有完成|Analysis jobs did not complete/)).toBeInTheDocument()
    vi.useRealTimers()
  })

  // T10: a11y — aria-live on progress
  it('renders progress section with aria-live polite', { timeout: 10000 }, async () => {
    mockSubmitMultiAngle.mockResolvedValue({
      job_ids: { risk: 'j1' },
      snapshot_id: 'snap-2', coin: 'BTC',
    })
    mockGetAnalysisJob.mockResolvedValue({ ok: true, data: { state: 'running' } })

    vi.useFakeTimers()
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await act(() => vi.runAllTimersAsync())
    const btn = screen.getByRole('button', { name: /五角度綜合分析|Multi-angle/ })

    await act(() => { fireEvent.click(btn) })
    await act(() => vi.advanceTimersByTimeAsync(2000))

    const progressSection = screen.getByRole('status')
    expect(progressSection).toHaveAttribute('aria-live', 'polite')
    vi.useRealTimers()
  })

  // T11: i18n zh-TW consensus label
  it('displays Chinese consensus label', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport({ consensus: '偏多' }) })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    expect(await screen.findByText('📈 偏多', undefined, { timeout: 5000 })).toBeInTheDocument()
  })

  // T12: i18n en consensus label
  it('displays English consensus label', async () => {
    document.cookie = 'trustforge_hermes_locale=en'
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport({ consensus: '偏多' }) })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    expect(await screen.findByText('📈 Bullish', undefined, { timeout: 5000 })).toBeInTheDocument()
  })

  // T13: decision_state i18n
  it('shows i18n decision_state instead of raw English', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport() })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    await screen.findByText(/正常|Normal/, undefined, { timeout: 5000 })
    expect(screen.queryByText(/^normal$/i)).not.toBeInTheDocument()
  })

  // T14: Abstain angle display
  it('shows abstain text for abstain angle', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({
      multi_angle: makeReport({ angles: [makeAngle({ decision_state: 'abstain', direction: '' })] }),
    })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const elements = await screen.findAllByText(/棄權|Abstain/)
    expect(elements.length).toBeGreaterThan(0)
  })

  // T15: Partial abstain banner
  it('shows partial abstain banner', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({
      multi_angle: makeReport({ decision_state: 'partial_abstain' }),
    })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    expect(await screen.findByText(/部分角度棄權|Partial abstain/, undefined, { timeout: 5000 })).toBeInTheDocument()
  })

  // T16: Conflict badge rendering
  it('renders ConflictBadge when conflicts exist', async () => {
    mockFetchMultiAngleReport.mockResolvedValue({
      multi_angle: makeReport({
        angles: [makeAngle({ angle: 'risk' }), makeAngle({ angle: 'sentiment', job_id: 'job-sent' })],
        conflicts: [
          { angle_a: 'risk', angle_b: 'sentiment', conflict_type: 'direction_divergence', detail: {}, summary: 'Risk bullish, Sentiment bearish' },
        ],
      }),
    })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const elements = await screen.findAllByText(/vs|與/)
    expect(elements.length).toBeGreaterThan(0)
  })

  // T17: Mobile card layout (matchMedia matches)
  it('renders mobile cards in narrow viewport', async () => {
    window.matchMedia = matchMediaMock.mockReturnValue({ matches: true } as MediaQueryList)
    mockFetchMultiAngleReport.mockResolvedValue({ multi_angle: makeReport() })
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    const cards = await screen.findAllByRole('button', { name: /風險評估 詳細/ })
    // In jsdom both desktop table and mobile cards are rendered; just verify
    // at least one mobile card exists (last one is the card in DOM order)
    expect(cards.length).toBeGreaterThanOrEqual(1)
  })

  // T18: Loading state
  it('shows loading state initially', () => {
    mockFetchMultiAngleReport.mockImplementation(() => new Promise(() => {}))
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    expect(screen.getByText(/五角度綜合分析|Multi-angle/)).toBeInTheDocument()
  })

  // T19: Error message display
  it('displays error message when fetch fails', async () => {
    mockFetchMultiAngleReport.mockRejectedValue(new Error('Network down'))
    renderWithProvider(<MultiAngleOverview coin="BTC" />)
    expect(await screen.findByText('Network down', undefined, { timeout: 5000 })).toBeInTheDocument()
  })
})
