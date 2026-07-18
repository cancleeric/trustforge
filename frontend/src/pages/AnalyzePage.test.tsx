// @vitest-environment jsdom

import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import AnalyzePage from './AnalyzePage'

vi.mock('../lib/endpoints', () => ({
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'timeout' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { question_id: 'question-1', job_id: 'flow-1', state: 'queued', origin: 'manual' } }),
  getAnalysisJob: vi.fn().mockResolvedValue({ ok: true, data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null } }),
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

describe('AnalyzePage manual execution', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('creates a high-priority durable manual job instead of calling the inline endpoint', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledWith(
      'BTC', 'risk', '分析BTC近期市場狀況', expect.any(AbortSignal),
    ))
    await waitFor(() => expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal)))
    expect(getAnalyze).not.toHaveBeenCalled()
  })

  it('keeps sample mode as an immediate local/demo analysis path', async () => {
    renderAnalyze('/analyze?coin=BTC&type=multi_source&q=demo&sample=1')

    await waitFor(() => expect(getAnalyze).toHaveBeenCalledWith(
      { coin: 'BTC', type: 'multi_source', q: 'demo', sample: '1' },
      expect.any(AbortSignal),
    ))
  })
})
