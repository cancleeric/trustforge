// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getAnalysisSnapshot, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import AnalyzePage from './AnalyzePage'

vi.mock('../lib/endpoints', () => ({
  getAnalysisSnapshot: vi.fn().mockResolvedValue({ ok: false, error: { code: 'snapshot_pending', message: 'pending' } }),
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'timeout' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { question_id: 'q1', job_id: 'j1', state: 'queued' } }),
}))

function renderAnalyze(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <AnalyzePage />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

describe('AnalyzePage production-safe execution', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('reads snapshots and queues missing production analysis instead of starting /api/analyze', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

    await waitFor(() => expect(getAnalysisSnapshot).toHaveBeenCalledWith(
      'BTC',
      'risk',
      expect.any(AbortSignal),
      '分析BTC近期市場狀況',
    ))
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledWith(
      'BTC',
      'risk',
      '分析BTC近期市場狀況',
      expect.any(AbortSignal),
    ))
    expect(getAnalyze).not.toHaveBeenCalled()
  })

  it('shows queue errors instead of loading forever when production automation is disabled', async () => {
    vi.mocked(registerAnalysisQuestion).mockResolvedValueOnce({
      ok: false,
      error: { code: 'automation_disabled', message: 'Hermes 自動工作已關閉（production_default）' },
    })
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

    expect(await screen.findByText(/automation_disabled/)).toBeInTheDocument()
    expect(screen.getByText('Hermes 自動工作已關閉（production_default）')).toBeInTheDocument()
  })

  it('does not start another snapshot poll while a snapshot read is still in flight', async () => {
    vi.useFakeTimers()
    let resolveSnapshot: ((value: Awaited<ReturnType<typeof getAnalysisSnapshot>>) => void) | undefined
    vi.mocked(getAnalysisSnapshot).mockImplementationOnce(() => new Promise((resolve) => {
      resolveSnapshot = resolve
    }))

    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

    await vi.waitFor(() => expect(getAnalysisSnapshot).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(15_000)
    expect(getAnalysisSnapshot).toHaveBeenCalledTimes(1)

    resolveSnapshot?.({ ok: false, error: { code: 'snapshot_pending', message: 'pending' } })
    await vi.waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
  })

  it('keeps sample mode as an immediate local/demo analysis path', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&q=demo&sample=1')

    await waitFor(() => expect(getAnalyze).toHaveBeenCalledWith(
      { coin: 'BTC', type: 'multi_source', q: 'demo', sample: '1' },
      expect.any(AbortSignal),
    ))
    expect(getAnalysisSnapshot).not.toHaveBeenCalled()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  })
})
