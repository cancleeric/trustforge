// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import AnalyzePage from './AnalyzePage'

vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data }: { data: AnalyzeData }) => <div aria-label="analysis report">{data.report.coin}</div>,
}))

vi.mock('../lib/endpoints', () => ({
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'timeout' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { question_id: 'question-1', job_id: 'flow-1', state: 'queued', origin: 'manual' } }),
  getAnalysisJob: vi.fn().mockResolvedValue({ ok: true, data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null } }),
}))

let mediaMatches = false
const mediaListeners = new Set<() => void>()

function setMobileComposer(matches: boolean) {
  mediaMatches = matches
  mediaListeners.forEach((listener) => listener())
}

function renderAnalyze(path: string, embedded = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <AnalyzePage embedded={embedded} />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

function HistoryControls() {
  const navigate = useNavigate()
  return <><button onClick={() => navigate('/analyze?coin=ETH&type=multi_source&mode=risk&q=second')}>second</button><button onClick={() => navigate(-1)}>back</button><button onClick={() => navigate(1)}>forward</button></>
}

function JobControls() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/analyze?coin=NEW&type=multi_source&mode=risk&q=new&job=job-new')}>new job</button>
}

function analysisResult(coin: string, runId: string): AnalyzeData {
  return {
    report: { coin, generated_at: runId, calibrated_confidence: 0.5, confidence: 0.5, decision_state: 'normal' },
    evidence: [],
    execution_log: [],
    trust_components_aggregate: { reputation: 0.5, corroboration: 0.5, recency: 0.5, manipulation: 0 },
    execution: { run_id: runId, nodes: [] },
  } as unknown as AnalyzeData
}

describe('AnalyzePage manual execution', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    mediaMatches = false
    mediaListeners.clear()
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      get matches() { return mediaMatches },
      media: '(max-width: 560px)',
      onchange: null,
      addEventListener: (_event: string, listener: () => void) => mediaListeners.add(listener),
      removeEventListener: (_event: string, listener: () => void) => mediaListeners.delete(listener),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
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

  it('does not mount QueryConsole in an embedded desktop workspace', () => {
    renderAnalyze('/analyze', true)
    expect(screen.queryByLabelText('問題')).not.toBeInTheDocument()
  })

  it('mounts exactly one composer in embedded mobile and standalone contexts', () => {
    setMobileComposer(true)
    const mobile = renderAnalyze('/analyze', true)
    expect(screen.getAllByLabelText('問題')).toHaveLength(1)
    mobile.unmount()

    setMobileComposer(false)
    renderAnalyze('/analyze')
    expect(screen.getAllByLabelText('問題')).toHaveLength(1)
  })

  it('registers exactly once for one explicit submit', async () => {
    renderAnalyze('/analyze')
    fireEvent.change(screen.getByLabelText('問題'), { target: { value: '只送出一次' } })
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
  })

  it('ignores a rapid second submit while the first job is loading', async () => {
    renderAnalyze('/analyze')
    const submit = screen.getByRole('button', { name: /立即重新分析/ })
    fireEvent.click(submit)
    fireEvent.click(submit)

    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    expect(submit).toBeDisabled()
  })

  it('registers exactly once when explicitly resubmitting the same URL', async () => {
    vi.mocked(getAnalysisJob).mockResolvedValueOnce({ ok: true, data: { job_id: 'flow-1', state: 'failed', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: 'same', error: 'test', origin: 'manual', priority: 0, queue_position: null, result: null } })
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=same')
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    const submit = screen.getByRole('button', { name: /立即重新分析/ })
    await waitFor(() => expect(submit).not.toBeDisabled())

    fireEvent.click(submit)
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
  })

  it('does not register processed URLs again on browser back or forward', async () => {
    render(
      <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=first']}>
        <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
          <AnalyzePage /><HistoryControls />
        </BridgeHologramProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'second' }))
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('button', { name: 'back' }))
    await waitFor(() => expect(screen.getByLabelText('問題')).toHaveValue('first'))
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'forward' }))
    await waitFor(() => expect(screen.getByLabelText('問題')).toHaveValue('second'))
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
  })

  it('polls URL jobs without registering and switches away from the previous report', async () => {
    vi.mocked(getAnalysisJob).mockImplementation(async (job) => ({
      ok: true,
      data: {
        job_id: job,
        state: 'completed',
        current_stage: 'report_delivery',
        coin: job === 'job-old' ? 'OLD' : 'NEW',
        mode: 'risk',
        question: job === 'job-old' ? 'old' : 'new',
        error: null,
        origin: 'manual',
        priority: 0,
        queue_position: null,
        result: analysisResult(job === 'job-old' ? 'OLD' : 'NEW', job),
      },
    }))
    render(
      <MemoryRouter initialEntries={['/analyze?coin=OLD&type=multi_source&mode=risk&q=old&job=job-old']}>
        <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
          <AnalyzePage /><JobControls />
        </BridgeHologramProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('OLD'))
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    expect(getAnalysisJob).toHaveBeenCalledWith('job-old', expect.any(AbortSignal))

    fireEvent.click(screen.getByRole('button', { name: 'new job' }))
    await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('NEW'))
    expect(screen.queryByText('OLD')).not.toBeInTheDocument()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    expect(getAnalysisJob).toHaveBeenCalledWith('job-new', expect.any(AbortSignal))
  })

  it('fails closed when a URL job belongs to a different request', async () => {
    vi.mocked(getAnalysisJob).mockResolvedValueOnce({
      ok: true,
      data: {
        job_id: 'job-wrong', state: 'completed', current_stage: 'report_delivery',
        coin: 'ETH', mode: 'risk', question: 'other', error: null, origin: 'manual',
        priority: 0, queue_position: null, result: analysisResult('WRONG', 'job-wrong'),
      },
    })
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=expected&job=job-wrong')

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('analysis_job_mismatch'))
    expect(screen.queryByLabelText('analysis report')).not.toBeInTheDocument()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  })

  it('accepts server-canonical job metadata', async () => {
    vi.mocked(getAnalysisJob).mockResolvedValueOnce({
      ok: true,
      data: {
        job_id: 'job-canonical', state: 'completed', current_stage: 'report_delivery',
        coin: 'BTC', mode: 'risk', question: 'canonical question', error: null, origin: 'manual',
        priority: 0, queue_position: null, result: analysisResult('BTC', 'job-canonical'),
      },
    })
    renderAnalyze('/analyze?coin=btc&type=multi_source&mode=%20risk%20&q=%20canonical%20question%20&job=job-canonical')

    await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
    expect(screen.queryByText('analysis_job_mismatch')).not.toBeInTheDocument()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  })

  it('does not submit when the embedded breakpoint changes', () => {
    renderAnalyze('/analyze', true)
    act(() => setMobileComposer(true))

    expect(screen.getAllByLabelText('問題')).toHaveLength(1)
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  })

  it('supports legacy MediaQueryList listeners and rebuilds draft from URL ownership', () => {
    let listener: (() => void) | undefined
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      get matches() { return mediaMatches },
      media: '(max-width: 560px)',
      addEventListener: undefined,
      removeEventListener: undefined,
      addListener: (next: () => void) => { listener = next },
      removeListener: vi.fn(),
    })))
    renderAnalyze('/analyze?q=URL問題', true)
    mediaMatches = true
    act(() => listener?.())

    expect(screen.getByLabelText('問題')).toHaveValue('URL問題')
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
  })
})
