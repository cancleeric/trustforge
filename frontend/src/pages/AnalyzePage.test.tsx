// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { getAnalysisJob, getAnalyze, registerAnalysisQuestion } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import AnalyzePage from './AnalyzePage'

function LocaleSwitcher({ to }: { to: 'en' | 'zh-TW' }) {
  const { setLocale } = useHermesI18n()
  setLocale(to)
  return null
}

vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data }: { data: AnalyzeData }) => <div aria-label="analysis report">{data.report.coin}</div>,
}))

vi.mock('../lib/endpoints', () => ({
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'timeout' } }),
  currentNarrativeLocale: vi.fn(() => 'zh-Hant'),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: formalReceipt() }),
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
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
          <AnalyzePage embedded={embedded} />
        </BridgeHologramProvider>
      </MemoryRouter>
    </HermesI18nProvider>,
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

function formalReceipt(questionId = 'question-1', jobId = 'flow-1') {
  return {
    schema_version: 'formal-run-receipt/v1' as const,
    receipt_id: `frc_${questionId}`,
    question_id: questionId,
    job_id: jobId,
    result_id: `result_${jobId}`,
    state: 'accepted' as const,
    origin: 'manual' as const,
    disposition: 'created' as const,
    locale: 'zh-Hant' as const,
    created_at: '2026-07-30T08:00:00Z',
    expires_at: null,
    fingerprint_version: 'analysis-question/v1' as const,
  }
}

describe('AnalyzePage manual execution', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    window.sessionStorage.clear()
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
      'BTC', 'risk', '分析BTC近期市場狀況',
      expect.stringMatching(/^tf1\.\d{6}\.[A-Za-z0-9_-]{22}$/),
      false,
      expect.any(AbortSignal),
      'zh-Hant',
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

  it('retries a 503 server_busy submission with exponential backoff and eventually succeeds', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(registerAnalysisQuestion)
        .mockResolvedValueOnce({ ok: false, error: { code: 'server_busy', message: '伺服器忙碌' } })
        .mockResolvedValueOnce({ ok: true, data: formalReceipt('question-2', 'flow-2') })
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-2', state: 'completed', current_stage: 'report_delivery', coin: 'BTC', mode: 'risk',
          question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: null,
          result: analysisResult('BTC', 'flow-2'),
        },
      })

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

      await act(async () => { await vi.runAllTimersAsync() })

      // one initial attempt + exactly one retry after the 503 — no more, no less.
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
      expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC')
      // the retry path must not leave the UI stuck — loading indicator gone, no error banner.
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null },
      })
      vi.mocked(registerAnalysisQuestion).mockResolvedValue({ ok: true, data: formalReceipt() })
    }
  })

  it('falls into a visible error state with a working retry button once 503 retries are exhausted', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(registerAnalysisQuestion).mockResolvedValue({ ok: false, error: { code: 'server_busy', message: '伺服器忙碌' } })

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

      await act(async () => { await vi.runAllTimersAsync() })

      // initial attempt (0) + 5 retries (1..5) = 6 calls, then it must give up and
      // surface a real error state instead of leaving the UI stuck in loading forever.
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(6)
      const retryButton = screen.getByRole('button', { name: /重新嘗試/ })
      expect(retryButton).toBeInTheDocument()
      expect(screen.queryByLabelText('analysis report')).not.toBeInTheDocument()

      // N2: loading and error must be mutually exclusive — once the error
      // banner is showing, there must be no lingering loading indicator and
      // the composer's submit button must be back to its normal, enabled
      // "resubmit" state, not stuck on "still submitting".
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /立即重新分析/ })).not.toBeDisabled()

      // the retry button must actually be able to re-submit and succeed once the
      // backend recovers — not just be decorative.
      vi.mocked(registerAnalysisQuestion).mockResolvedValueOnce({
        ok: true, data: formalReceipt('question-recovered', 'flow-recovered'),
      })
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-recovered', state: 'completed', current_stage: 'report_delivery', coin: 'BTC', mode: 'risk',
          question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: null,
          result: analysisResult('BTC', 'flow-recovered'),
        },
      })
      fireEvent.click(retryButton)
      await act(async () => { await vi.runAllTimersAsync() })

      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(7)
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[6][3])
        .toBe(vi.mocked(registerAnalysisQuestion).mock.calls[0][3])
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[6][4]).toBe(false)
      expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC')
    } finally {
      vi.useRealTimers()
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null },
      })
      vi.mocked(registerAnalysisQuestion).mockResolvedValue({ ok: true, data: formalReceipt() })
    }
  })

  it('registers exactly once when explicitly resubmitting the same URL', async () => {
    vi.mocked(getAnalysisJob).mockResolvedValueOnce({ ok: true, data: { job_id: 'flow-1', state: 'failed', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: 'same', error: 'test', origin: 'manual', priority: 0, queue_position: null, result: null } })
    renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=same')
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    const submit = screen.getByRole('button', { name: /立即重新分析/ })
    await waitFor(() => expect(submit).not.toBeDisabled())

    fireEvent.click(submit)
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
    const first = vi.mocked(registerAnalysisQuestion).mock.calls[0]
    const second = vi.mocked(registerAnalysisQuestion).mock.calls[1]
    expect(first[4]).toBe(false)
    expect(second[4]).toBe(true)
    expect(second[3]).not.toBe(first[3])
  })

  it('reuses one formal key across bounded transport retries', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(registerAnalysisQuestion)
        .mockResolvedValueOnce({ ok: false, error: { code: 'server_busy', message: 'busy' } })
        .mockResolvedValueOnce({ ok: true, data: formalReceipt() })

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=retry-key')
      await act(async () => { await vi.advanceTimersByTimeAsync(2100) })

      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
      const first = vi.mocked(registerAnalysisQuestion).mock.calls[0]
      const retry = vi.mocked(registerAnalysisQuestion).mock.calls[1]
      expect(retry[3]).toBe(first[3])
      expect(retry[4]).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('resumes an unresolved explicit fresh intent after reload with the same key and fresh flag', async () => {
    try {
      const path = '/analyze?coin=BTC&type=multi_source&mode=risk&q=fresh-reload'
      vi.mocked(registerAnalysisQuestion)
        .mockResolvedValueOnce({ ok: true, data: formalReceipt('old', 'old-job') })
        .mockResolvedValue({ ok: false, error: { code: 'timeout', message: 'response lost' } })
      vi.mocked(getAnalysisJob).mockResolvedValueOnce({
        ok: true,
        data: { job_id: 'old-job', state: 'failed', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: 'fresh-reload', error: 'old failed', origin: 'manual', priority: 0, queue_position: null, result: null },
      })

      const firstView = renderAnalyze(path)
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
      const freshCall = vi.mocked(registerAnalysisQuestion).mock.calls[1]
      expect(freshCall[4]).toBe(true)
      firstView.unmount()

      renderAnalyze(path)
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(3))
      const resumed = vi.mocked(registerAnalysisQuestion).mock.calls[2]
      expect(resumed[3]).toBe(freshCall[3])
      expect(resumed[4]).toBe(true)
    } finally {
      vi.mocked(registerAnalysisQuestion).mockResolvedValue({ ok: true, data: formalReceipt() })
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null },
      })
    }
  })

  it('does not register processed URLs again on browser back or forward', async () => {
    render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=first']}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage /><HistoryControls />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
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
      <HermesI18nProvider>
        <MemoryRouter initialEntries={['/analyze?coin=OLD&type=multi_source&mode=risk&q=old&job=job-old']}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage /><JobControls />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
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

  it('N7: localizes the workspace title/subtitle and loading label to EN instead of hard-coded zh-TW', async () => {
    // N7 regression guard: the analyze workspace shell (title, subtitle,
    // manual-priority/creating-job loading label) was hard-coded zh-TW and
    // never respected the EN/zh-TW toggle. Render under the EN locale and
    // assert the copy is English, not the Chinese literals.
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況']}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
    )

    expect(screen.getByRole('heading', { name: /Analysis workspace/ })).toBeInTheDocument()
    expect(screen.getByText(/Each run is a single, fixed execution/)).toBeInTheDocument()
    expect(screen.queryByText('分析工作區')).not.toBeInTheDocument()
    expect(screen.queryByText(/每次執行固定一個 run/)).not.toBeInTheDocument()

    await waitFor(() => expect(screen.getByText(/Hermes is creating a manual analysis job for BTC/)).toBeInTheDocument())
    expect(screen.queryByText(/Hermes 正在建立 BTC 的手動分析工作/)).not.toBeInTheDocument()
  })

  it('N8: keeps polling a job that is still running past the old 120s cliff instead of declaring a false timeout', async () => {
    // N8 regression guard: the previous implementation gave up and showed
    // `analysis_timeout` once `Date.now() - pollStartedAt >= 120_000`,
    // *regardless* of whether the backend was still actively reporting a
    // live `state`/`current_stage`. Termination must be driven by `state`
    // (completed/failed), not a hard-coded clock.
    vi.useFakeTimers()
    try {
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-1', state: 'running', current_stage: 'trust_reasoning',
          coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
          origin: 'manual', priority: 0, queue_position: null, result: null,
        },
      })

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

      // Push well past the old 120_000ms cliff (poll interval is 1200ms).
      await act(async () => { await vi.advanceTimersByTimeAsync(150_000) })
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)

      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.queryByText(/trust_reasoning/)).toBeInTheDocument()
      expect(screen.queryByLabelText('analysis report')).not.toBeInTheDocument()

      // Now the job actually finishes — the UI must still pick it up.
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-1', state: 'completed', current_stage: 'report_delivery',
          coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
          origin: 'manual', priority: 0, queue_position: null,
          result: analysisResult('BTC', 'flow-1'),
        },
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })

      expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC')
    } finally {
      vi.useRealTimers()
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion',
          coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
          origin: 'manual', priority: 0, queue_position: 1, result: null,
        },
      })
    }
  }, 20_000)

  it('N8: gives up only after sustained no-response from the backend', async () => {
    // The 10-minute inactivity fuse must still exist as a safety net for a
    // genuinely dead backend — this is the one case where surfacing
    // `analysis_timeout` is correct.
    vi.useFakeTimers()
    try {
      vi.mocked(getAnalysisJob).mockResolvedValue({ ok: false, error: { code: 'network_error', message: 'network down' } })

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況')

      await act(async () => { await vi.runAllTimersAsync() })
      await act(async () => {})

      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
      expect(screen.getByRole('alert')).toHaveTextContent('analysis_timeout')
    } finally {
      vi.useRealTimers()
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion',
          coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
          origin: 'manual', priority: 0, queue_position: 1, result: null,
        },
      })
    }
  }, 20_000)

  it('N9: reconnects to the same manual job via sessionStorage after a reload, without resubmitting', async () => {
    // N9 regression guard: without this, reloading the page mid-analysis (or
    // after completion) lost track of the job entirely and either
    // resubmitted a brand-new one or showed "no analysis data" forever for a
    // job that had actually already completed.
    const path = '/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況'
    const first = renderAnalyze(path)
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal)))
    first.unmount()

    vi.mocked(getAnalysisJob).mockResolvedValue({
      ok: true,
      data: {
        job_id: 'flow-1', state: 'completed', current_stage: 'report_delivery',
        coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
        origin: 'manual', priority: 0, queue_position: null,
        result: analysisResult('BTC', 'flow-1'),
      },
    })

    // Simulate a page reload: a brand-new mount, same URL, still no
    // explicit `?job=` param — it must reattach via sessionStorage instead
    // of firing a second `registerAnalysisQuestion` POST.
    renderAnalyze(path)
    await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
    expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal))
  })

  it('N13: bumping resubmitSignal with an unchanged same-question URL fires a new POST', async () => {
    // N13 regression guard: HermesDashboard's own left-rail "立即重新分析"
    // button drives the shared URL search params directly instead of going
    // through AnalyzePage's `handleSubmit`. When the question text is
    // unchanged, that produces a byte-identical query string, so React
    // Router never re-renders with new param *values* and the polling
    // effect's dependency array never changes — no new POST would fire and
    // the screen would silently keep showing the previous run's stale
    // report. `resubmitSignal` must force a real resubmit even though the
    // URL content is identical.
    const path = '/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況'
    const view = render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage embedded resubmitSignal={0} />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal)))

    // Re-render with the exact same URL/question, only bumping the signal —
    // this is what the host does on every explicit button click.
    view.rerender(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage embedded resubmitSignal={1} />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
    )

    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
  })

  it('N13: a reload (fresh mount, resubmitSignal reset to its initial value) still reconnects instead of resubmitting', async () => {
    // Companion negative-path guard for N13: the fix must not turn every
    // mount into a forced resubmit. A real page reload remounts the whole
    // React tree, so the host's counter (and the `resubmitSignal` prop it
    // passes in) restarts fresh — it must NOT be treated as "the value
    // changed", or N9's reload-reconnects-without-resubmitting guarantee
    // would break for the embedded host too.
    const path = '/analyze?coin=BTC&type=multi_source&mode=risk&q=分析BTC近期市場狀況'
    const first = render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage embedded resubmitSignal={0} />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal)))
    first.unmount()

    vi.mocked(getAnalysisJob).mockResolvedValue({
      ok: true,
      data: {
        job_id: 'flow-1', state: 'completed', current_stage: 'report_delivery',
        coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null,
        origin: 'manual', priority: 0, queue_position: null,
        result: analysisResult('BTC', 'flow-1'),
      },
    })

    render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={[path]}>
          <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
            <AnalyzePage embedded resubmitSignal={0} />
          </BridgeHologramProvider>
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
  })
})
