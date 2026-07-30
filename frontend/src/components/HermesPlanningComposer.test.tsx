// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import { previewAnalysisPlan } from '../lib/analysisPlan'
import HermesPlanningComposer from './HermesPlanningComposer'

vi.mock('../lib/analysisPlan', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('../lib/analysisPlan')>()
  return { ...original, previewAnalysisPlan: vi.fn() }
})

const previewMock = vi.mocked(previewAnalysisPlan)

const readyPlan = {
  outcome: 'ready' as const,
  detected_assets: ['BTC'],
  intent_shape: 'single' as const,
  intents: [{ label: '風險', rationale: '檢查市場風險' }],
  source_classes: ['market_price' as const, 'news' as const],
  strategy_summary: '先交叉檢查來源，再整理不確定性。',
  clarifications: [],
  warnings: [],
  confidence: { level: 'high' as const, rationale: '問題範圍清楚' },
  provenance: { planner: 'hermes' as const, provider: 'aws-bedrock' as const, policy_version: 'v1' },
}

function renderComposer() {
  return render(<HermesI18nProvider><HermesPlanningComposer /></HermesI18nProvider>)
}

function LocaleSwitch() {
  const { setLocale } = useHermesI18n()
  return <button type="button" onClick={() => setLocale('en')}>English</button>
}

function submit(question = '分析 BTC 近期風險') {
  fireEvent.change(screen.getByLabelText('你想分析什麼？'), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: /預覽分析計畫/ }))
}

beforeEach(() => {
  previewMock.mockReset()
  window.localStorage.clear?.()
  document.cookie = 'trustforge_hermes_locale=; Max-Age=0; Path=/'
})

describe('HermesPlanningComposer', () => {
  it('calls the typed preview client and renders a ready plan as text', async () => {
    previewMock.mockResolvedValue({ ok: true, data: readyPlan })
    renderComposer()
    fireEvent.change(screen.getByLabelText('資產提示（選填）'), { target: { value: 'BTC, ETH' } })
    submit()

    await screen.findByText('計畫已就緒')
    expect(previewMock).toHaveBeenCalledWith(
      {
        question: '分析 BTC 近期風險',
        locale: 'zh-TW',
        asset_hints: ['BTC', 'ETH'],
        client_request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      },
      expect.any(AbortSignal),
    )
    expect(screen.getByText(readyPlan.strategy_summary)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '分析策略' })).toHaveFocus()
  })

  it('cancels an in-flight request and ignores its late response', async () => {
    let settle!: (value: { ok: true; data: typeof readyPlan }) => void
    previewMock.mockImplementation(() => new Promise((resolve) => { settle = resolve }))
    renderComposer()
    submit()
    fireEvent.click(await screen.findByRole('button', { name: '取消' }))
    settle({ ok: true, data: readyPlan })

    await screen.findByText('已取消規劃預覽。')
    expect(screen.queryByText('計畫已就緒')).not.toBeInTheDocument()
  })

  it('marks a rendered result stale after the draft changes', async () => {
    previewMock.mockResolvedValue({ ok: true, data: readyPlan })
    renderComposer()
    submit()
    await screen.findByText('計畫已就緒')
    fireEvent.change(screen.getByLabelText('你想分析什麼？'), { target: { value: '改問 ETH' } })
    expect(screen.getByText(/以下仍是上一版預覽/)).toBeInTheDocument()
  })

  it('renders clarification and server error states without creating a formal job', async () => {
    previewMock
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ...readyPlan,
          outcome: 'needs_clarification',
          clarifications: [{ id: 'scope', question: '要看哪個期間？', options: ['7 天', '30 天'] }],
        },
      })
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'plan_temporarily_unavailable', message: '暫時不可用', retryable: true },
      })
    renderComposer()
    submit()
    await screen.findByText('需要先釐清')
    expect(screen.getByText('要看哪個期間？')).toBeInTheDocument()
    expect(screen.getByText('7 天')).toBeInTheDocument()
    expect(screen.getByText('30 天')).toBeInTheDocument()
    expect(screen.getByText('hermes · aws-bedrock · v1')).toBeInTheDocument()
    expect(screen.getByText(/不是信任分數，也不是校準後資訊完整度/)).toBeInTheDocument()
    expect(screen.getByText(/不會建立正式分析工作/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /預覽分析計畫/ }))
    await screen.findByRole('alert')
    expect(screen.getByText('Hermes 規劃暫時不可用，請稍後再試。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '再試一次' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveFocus()
  })

  it('rejects invalid asset hints before calling the client', async () => {
    renderComposer()
    fireEvent.change(screen.getByLabelText('你想分析什麼？'), { target: { value: '分析 BTC' } })
    fireEvent.change(screen.getByLabelText('資產提示（選填）'), { target: { value: 'btc, BTC' } })
    fireEvent.click(screen.getByRole('button', { name: /預覽分析計畫/ }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('資產提示須為大寫代號'))
    expect(previewMock).not.toHaveBeenCalled()
  })

  it('renders model output as plain text rather than HTML', async () => {
    previewMock.mockResolvedValue({
      ok: true,
      data: { ...readyPlan, strategy_summary: '<img src=x onerror=alert(1)>' },
    })
    const { container } = renderComposer()
    submit()

    await screen.findByText('<img src=x onerror=alert(1)>')
    expect(container.querySelector('img')).toBeNull()
  })

  it('never exposes a raw server error and Back to edit restores textarea focus', async () => {
    previewMock.mockResolvedValue({
      ok: false,
      error: {
        code: 'network_error',
        message: 'fetch https://secret.internal failed with token=abc',
      },
    })
    renderComposer()
    submit()

    await screen.findByText('目前無法連線至 Hermes 規劃服務。')
    expect(screen.queryByText(/secret\.internal|token=abc/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回編輯' }))
    await waitFor(() => expect(screen.getByLabelText('你想分析什麼？')).toHaveFocus())
  })

  it('re-localizes an existing safe error when the locale changes', async () => {
    previewMock.mockResolvedValue({
      ok: false,
      error: { code: 'plan_temporarily_unavailable', message: 'raw', retryable: true },
    })
    render(
      <HermesI18nProvider>
        <LocaleSwitch />
        <HermesPlanningComposer />
      </HermesI18nProvider>,
    )
    submit()
    await screen.findByText('Hermes 規劃暫時不可用，請稍後再試。')

    fireEvent.click(screen.getByRole('button', { name: 'English' }))

    expect(screen.getByText('Hermes planning is temporarily unavailable. Try again shortly.')).toBeInTheDocument()
  })

  it('submits with Enter, preserves Shift+Enter, and ignores Enter during IME composition', async () => {
    previewMock.mockResolvedValue({ ok: true, data: readyPlan })
    renderComposer()
    const textarea = screen.getByLabelText('你想分析什麼？')
    fireEvent.change(textarea, { target: { value: '分析 BTC' } })

    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })
    expect(previewMock).not.toHaveBeenCalled()
    fireEvent.keyDown(textarea, { key: 'Enter' })
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(1))
  })

  it('single-flights same-tick duplicate submissions', () => {
    previewMock.mockImplementation(() => new Promise(() => undefined))
    renderComposer()
    const textarea = screen.getByLabelText('你想分析什麼？')
    fireEvent.change(textarea, { target: { value: '分析 BTC' } })

    fireEvent.keyDown(textarea, { key: 'Enter' })
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(previewMock).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['多源整合', '分析 SOL 過去兩週', 'SOL'],
    ['假設驗證', 'BTC 短期將盤整', 'BTC'],
    ['比較分析', '比較 BTC 與 ETH', 'BTC, ETH'],
  ])('official example %s only fills the draft', (label, question, hints) => {
    renderComposer()

    fireEvent.click(screen.getByRole('button', { name: label }))

    expect((screen.getByLabelText('你想分析什麼？') as HTMLTextAreaElement).value).toContain(question)
    expect(screen.getByLabelText('資產提示（選填）')).toHaveValue(hints)
    expect(previewMock).not.toHaveBeenCalled()
  })

  it.each([
    ['multiple', [
      { label: '比較流動性', rationale: '比較兩個資產' },
      { label: '檢查監管', rationale: '檢查共同風險' },
    ]],
    ['unknown', [{ label: '開放問題', rationale: '保留未分類意圖' }]],
  ] as const)('renders %s intent plans without a client whitelist', async (intentShape, intents) => {
    previewMock.mockResolvedValue({
      ok: true,
      data: { ...readyPlan, intent_shape: intentShape, intents: [...intents] },
    })
    renderComposer()
    submit('比較 BTC 與 ETH，並找出尚未分類的共同風險')

    await screen.findByText(intents[0].label)
    expect(previewMock).toHaveBeenCalledTimes(1)
  })
})
