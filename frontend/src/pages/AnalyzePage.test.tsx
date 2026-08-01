// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, useNavigate, useSearchParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

vi.mock('../lib/analysisWip', () => ({ ANALYSIS_FORMAL_WIP: false }))
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

// #1186: a fresh mount whose URL already carries an explicit, non-sample
// `?q=` with no reconnectable job (no `?job=`, nothing matching in
// sessionStorage) is now gated behind the same FormalRunConfirmDialog as
// every other formal-run entry point (see AnalyzePage.tsx's `deeplink`
// pending kind). Tests that use a `renderAnalyze('/analyze?...q=...')`
// (or an equivalent raw `render`) purely as setup for otherwise-unrelated
// polling/dedup/idempotency behavior call this right after render to get
// past that gate exactly like a real user confirming the dialog once.
function confirmDeepLinkRun() {
  fireEvent.click(screen.getByRole('button', { name: /^(確認執行|Confirm run)$/ }))
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
    confirmDeepLinkRun()

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
    // #940：正式送出現在先經確認對話框，確認後才真正註冊。
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '確認執行' }))
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
  })

  it('ignores a rapid second submit while the first job is loading', async () => {
    renderAnalyze('/analyze')
    const submit = screen.getByRole('button', { name: /立即重新分析/ })
    fireEvent.click(submit)
    // #940：確認對話框現在是送出的閘門；連點第二下 composer 只會重設同一個
    // pending intent，不會送出第二次。
    fireEvent.click(submit)
    fireEvent.click(screen.getByRole('button', { name: '確認執行' }))

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
      confirmDeepLinkRun()

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
      confirmDeepLinkRun()

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
    confirmDeepLinkRun()
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    const submit = screen.getByRole('button', { name: /立即重新分析/ })
    await waitFor(() => expect(submit).not.toBeDisabled())

    fireEvent.click(submit)
    fireEvent.click(screen.getByRole('button', { name: '確認執行' }))
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
      confirmDeepLinkRun()
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

  it('resumes an unresolved explicit fresh intent after reload with the same key and fresh flag, once each reload is confirmed', async () => {
    // #1186: this used to auto-run on every mount with no confirmation at
    // all (the exact deep-link/reload bypass fixed by PR #1186). Every one
    // of these mounts now requires its own explicit confirm before the
    // idempotency-resume machinery below (same key, same fresh flag) even
    // gets a chance to run.
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
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
      confirmDeepLinkRun()
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
      fireEvent.click(screen.getByRole('button', { name: '確認執行' }))
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
      const freshCall = vi.mocked(registerAnalysisQuestion).mock.calls[1]
      expect(freshCall[4]).toBe(true)
      firstView.unmount()

      renderAnalyze(path)
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
      confirmDeepLinkRun()
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
    confirmDeepLinkRun()
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'second' }))
    // #1186: navigating to a different, not-yet-confirmed q re-opens the gate.
    confirmDeepLinkRun()
    await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('button', { name: 'back' }))
    await waitFor(() => expect(screen.getByLabelText('問題')).toHaveValue('first'))
    // back navigates to an already-confirmed, already-processed q — no dialog, no re-registration.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'forward' }))
    await waitFor(() => expect(screen.getByLabelText('問題')).toHaveValue('second'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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
    confirmDeepLinkRun()
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
    confirmDeepLinkRun()

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
      confirmDeepLinkRun()

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
      confirmDeepLinkRun()

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
    confirmDeepLinkRun()
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
    confirmDeepLinkRun()
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

    // #940: the forced resubmit is now gated behind the same confirm dialog as
    // every formal run. The signal still forces a real new POST, but only after
    // the user confirms — a byte-identical URL no longer silently fires.
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: /確認執行|Confirm run/ }))
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
    confirmDeepLinkRun()
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

  describe('#940 formal-run confirmation + reconnect/partial UI', () => {
    beforeEach(() => {
      // N7 switches the locale cookie to 'en' and never restores it; isolate
      // these tests back to the default zh-TW so localized button/banner copy
      // is deterministic regardless of where this block runs in the suite.
      document.cookie = 'trustforge_hermes_locale=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
    })

    afterEach(() => {
      // `vi.clearAllMocks()` (outer beforeEach) resets call history but NOT a
      // mock implementation set via mockImplementation — so a test that overrides
      // getAnalysisJob to return a *completed* job (the partial-result test)
      // would leak that shape into a later test and trigger an unrelated render
      // crash. Restore the factory defaults after every test in this block.
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null },
      })
      vi.mocked(registerAnalysisQuestion).mockResolvedValue({ ok: true, data: formalReceipt() })
    })

    it('cancel on the pre-submit confirmation dialog does not submit', async () => {
      renderAnalyze('/analyze')
      fireEvent.change(screen.getByLabelText('問題'), { target: { value: '取消不送' } })
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
      // dialog opens; the formal run is held pending, no registration yet.
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    })

    it('confirm on the pre-submit dialog submits the formal run exactly once', async () => {
      renderAnalyze('/analyze')
      fireEvent.change(screen.getByLabelText('問題'), { target: { value: '確認送出' } })
      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
      fireEvent.click(screen.getByRole('button', { name: '確認執行' }))

      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
      // a confirmed run must reflect the committed question, not a stale one.
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[0][2]).toBe('確認送出')
    })

    it('Esc dismisses the confirmation dialog without submitting', async () => {
      renderAnalyze('/analyze')
      fireEvent.change(screen.getByLabelText('問題'), { target: { value: 'Esc 取消' } })
      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
      expect(screen.getByRole('dialog')).toBeInTheDocument()

      fireEvent.keyDown(document.body, { key: 'Escape' })
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    })

    it('keeps keyboard focus inside the confirmation dialog', () => {
      renderAnalyze('/analyze')
      fireEvent.change(screen.getByLabelText('問題'), { target: { value: '鍵盤焦點測試' } })
      fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))

      const cancel = screen.getByRole('button', { name: '取消' })
      const confirm = screen.getByRole('button', { name: '確認執行' })
      expect(confirm).toHaveFocus()

      fireEvent.keyDown(document, { key: 'Tab' })
      expect(cancel).toHaveFocus()
      fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
      expect(confirm).toHaveFocus()
    })

    it('renders a reconnecting banner when reattaching to an in-progress URL job', async () => {
      vi.useFakeTimers()
      try {
        vi.mocked(getAnalysisJob).mockResolvedValue({
          ok: true,
          data: {
            job_id: 'job-run', state: 'running', current_stage: 'trust_reasoning',
            coin: 'BTC', mode: 'risk', question: '接回測試', error: null, origin: 'manual',
            priority: 0, queue_position: null, result: null,
          },
        })
        const view = renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=接回測試&job=job-run')
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })

        // a URL job that is still running must surface a distinct reconnecting
        // banner (not a fresh-submit loading label) and must not re-register.
        expect(screen.getByText('接回既有分析工作')).toBeInTheDocument()
        expect(registerAnalysisQuestion).not.toHaveBeenCalled()
        expect(screen.queryByLabelText('analysis report')).not.toBeInTheDocument()
        view.unmount()
      } finally {
        vi.useRealTimers()
        vi.mocked(getAnalysisJob).mockResolvedValue({
          ok: true,
          data: { job_id: 'flow-1', state: 'queued', current_stage: 'source_ingestion', coin: 'BTC', mode: 'risk', question: '分析BTC近期市場狀況', error: null, origin: 'manual', priority: 0, queue_position: 1, result: null },
        })
      }
    })

    it('renders a partial-result banner for a degraded completed report and not for a normal one', async () => {
      const normal = analysisResult('BTC', 'run-normal')
      const partial = analysisResult('BTC', 'run-partial')
      ;(partial.report as { decision_state: string }).decision_state = 'abstain'

      vi.mocked(getAnalysisJob).mockImplementation(async (job) => ({
        ok: true,
        data: {
          job_id: job, state: 'completed', current_stage: 'report_delivery',
          coin: 'BTC', mode: 'risk', question: job === 'run-partial' ? '部分完成' : '正常完成',
          error: null, origin: 'manual', priority: 0, queue_position: null,
          result: job === 'run-partial' ? partial : normal,
        },
      }))

      const partialView = renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=部分完成&job=run-partial')
      await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
      expect(screen.getByText(/best-effort/)).toBeInTheDocument()
      partialView.unmount()

      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=正常完成&job=run-normal')
      await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
      expect(screen.queryByText(/best-effort/)).not.toBeInTheDocument()
    })

    it('#940: the beginner focus-start button gates behind the confirm dialog before committing', async () => {
      // focusMode shows the first-run card; clicking 開始第一次分析 is a non-sample
      // formal run, so it must open the confirm dialog instead of committing the
      // run directly (it previously called setSearchParams + bumped the nonce).
      renderAnalyze('/analyze?focus=1')
      const startButton = screen.getByRole('button', { name: /開始第一次分析/ })

      fireEvent.click(startButton)
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()

      // cancel: no submit, dialog closes.
      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()

      // confirm: the focus run commits (focus=1 preserved) and registers once.
      fireEvent.click(startButton)
      fireEvent.click(screen.getByRole('button', { name: '確認執行' }))
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
    })

    it('#940: an embedded resubmit that also changes the URL params is held until confirm', async () => {
      // The host (HermesDashboard.onSubmit) writes new params to the URL AND bumps
      // resubmitSignal in the same tick. The URL change alone would make the
      // polling effect submit immediately, bypassing a signal-only gate — so the
      // gate must hold the run until the user confirms.
      function HostShell() {
        const [signal, setSignal] = useState(0)
        const navigate = useNavigate()
        return (
          <>
            <AnalyzePage embedded resubmitSignal={signal} />
            <button onClick={() => {
              navigate('/analyze?coin=ETH&type=multi_source&mode=risk&q=changed-question&workspace=analyze')
              setSignal((value) => value + 1)
            }}>host-resubmit</button>
          </>
        )
      }
      const view = render(
        <HermesI18nProvider>
          <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=first-question&workspace=analyze']}>
            <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
              <HostShell />
            </BridgeHologramProvider>
          </MemoryRouter>
        </HermesI18nProvider>,
      )
      confirmDeepLinkRun()
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[0][0]).toBe('BTC')

      fireEvent.click(screen.getByRole('button', { name: 'host-resubmit' }))
      // dialog opens; the URL already moved to ETH/changed-question but NO submit.
      expect(await screen.findByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByRole('button', { name: '確認執行' }))
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(2))
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[1][0]).toBe('ETH')
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[1][2]).toBe('changed-question')
      view.unmount()
    })

    it('#940 修1: cancelling an embedded resubmit restores the URL so reload cannot auto-run the cancelled request', async () => {
      // The host writes the new (unconfirmed) request to the URL in the same tick
      // it bumps resubmitSignal. Cancelling the confirm dialog used to leave that
      // unconfirmed q in the URL, so a reload/remount would read it and fire an
      // irreversible formal run with NO confirmation. Cancel must restore the URL
      // to the pre-resubmit state so the cancelled request cannot auto-run.
      vi.mocked(getAnalysisJob).mockResolvedValue({
        ok: true,
        data: {
          job_id: 'flow-1', state: 'completed', current_stage: 'report_delivery',
          coin: 'BTC', mode: 'risk', question: 'first-question', error: null, origin: 'manual',
          priority: 0, queue_position: null, result: analysisResult('BTC', 'flow-1'),
        },
      })
      function HostShell() {
        const [signal, setSignal] = useState(0)
        const navigate = useNavigate()
        const [search] = useSearchParams()
        return (
          <>
            <AnalyzePage embedded resubmitSignal={signal} />
            <button onClick={() => {
              navigate('/analyze?coin=ETH&type=multi_source&mode=risk&q=changed-question&workspace=analyze')
              setSignal((value) => value + 1)
            }}>host-resubmit</button>
            <div data-testid="url-probe">{search.toString()}</div>
          </>
        )
      }
      const view = render(
        <HermesI18nProvider>
          <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=first-question&workspace=analyze']}>
            <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
              <HostShell />
            </BridgeHologramProvider>
          </MemoryRouter>
        </HermesI18nProvider>,
      )
      confirmDeepLinkRun()
      // first-question runs exactly once and lands.
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
      await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))

      // host resubmit -> URL already moved to the unconfirmed changed-question, dialog holds it.
      fireEvent.click(screen.getByRole('button', { name: 'host-resubmit' }))
      expect(await screen.findByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)

      // cancel -> the unconfirmed request must NOT linger in the URL.
      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
      await waitFor(() => {
        const probe = screen.getByTestId('url-probe').textContent ?? ''
        expect(probe).not.toContain('changed-question')
      })
      const probeAfterCancel = screen.getByTestId('url-probe').textContent ?? ''
      // restored to the previously-confirmed request, pinned to its existing job so the
      // restored URL reconnects (poll-only) rather than firing a fresh formal run.
      expect(probeAfterCancel).toContain('first-question')
      expect(probeAfterCancel).toContain('job=flow-1')

      // simulate a real page reload: brand-new mount with the restored URL. The
      // cancelled request must NOT auto-run; it must only reconnect to flow-1.
      const restoredPath = '/analyze?' + probeAfterCancel
      view.unmount()
      renderAnalyze(restoredPath, true)
      await waitFor(() => expect(screen.getByLabelText('analysis report')).toHaveTextContent('BTC'))
      expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1)
      expect(getAnalysisJob).toHaveBeenCalledWith('flow-1', expect.any(AbortSignal))
    })

    it('#940: a sample demo run commits immediately without raising the confirm dialog', async () => {
      renderAnalyze('/analyze?coin=BTC&type=multi_source&q=demo&sample=1')
      // sample is the local demo path — it must NOT raise the formal-run dialog.
      await waitFor(() => expect(getAnalyze).toHaveBeenCalled())
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    })

    it('#1186: a deep link/reload with no reconnectable job shows the confirm dialog and does not auto-run', async () => {
      // #1186 (HIGH, adversarial review of PR #940): navigating or
      // reloading directly to a URL that already carries `?q=...` with no
      // `?job=` to reconnect to used to fall straight through to
      // `registerAnalysisQuestion` with zero confirmation — the exact
      // bypass this fix closes. It must now be held behind the same
      // FormalRunConfirmDialog as every other formal-run entry point.
      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=deep-link-question')
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
      expect(getAnalysisJob).not.toHaveBeenCalled()
    })

    it('#1186: confirming a deep link/reload run submits it exactly once', async () => {
      renderAnalyze('/analyze?coin=BTC&type=multi_source&mode=risk&q=deep-link-confirm')
      confirmDeepLinkRun()
      await waitFor(() => expect(registerAnalysisQuestion).toHaveBeenCalledTimes(1))
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[0][0]).toBe('BTC')
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[0][1]).toBe('risk')
      expect(vi.mocked(registerAnalysisQuestion).mock.calls[0][2]).toBe('deep-link-confirm')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('#1186: cancelling a deep link/reload run does not submit and does not loop back into the same prompt on the next reload', async () => {
      function UrlProbe() {
        const [search] = useSearchParams()
        return <div data-testid="url-probe">{search.toString()}</div>
      }
      const view = render(
        <HermesI18nProvider>
          <MemoryRouter initialEntries={['/analyze?coin=BTC&type=multi_source&mode=risk&q=deep-link-cancel']}>
            <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
              <AnalyzePage />
              <UrlProbe />
            </BridgeHologramProvider>
          </MemoryRouter>
        </HermesI18nProvider>,
      )
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()

      // nothing was ever registered for the cancelled request, so there is
      // no existing job to pin/reconnect to — cancel must simply drop the
      // unconfirmed `q` from the URL.
      const probe = screen.getByTestId('url-probe').textContent ?? ''
      expect(probe).not.toContain('deep-link-cancel')
      view.unmount()

      // Simulate a real page reload with the URL exactly as cancel left it:
      // a brand-new mount must NOT re-show the same confirm prompt or
      // auto-run — i.e. cancelling must not create an infinite reload loop
      // back into the same dialog.
      renderAnalyze('/analyze?' + probe)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    })
  })
})
