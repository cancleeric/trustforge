// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getAnalysisQuestionContext, registerAnalysisQuestion } from '../lib/endpoints'
import { buildGalaxyModel, deriveSelected, type HermesTier } from '../lib/hermesData'
import type { CrossSourceSignal } from '../lib/types'
import HermesRightRail from '../hermes/HermesRightRail'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import HermesDashboard from './HermesDashboard'

vi.mock('../lib/endpoints', () => ({
  getOverview: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getCosts: vi.fn().mockResolvedValue({ ok: true, data: { total_cost_usd: 0 } }),
  getHealth: vi.fn().mockResolvedValue({ ok: true, data: { version: 'dev' } }),
  getAgentCoreStatus: vi.fn().mockResolvedValue({
    ok: true,
    data: {
      provider: 'builtin',
      selected: false,
      runtime_configured: false,
      state: 'inactive',
    },
  }),
  getAnalysisSnapshot: vi.fn().mockResolvedValue({ ok: false, error: { code: 'snapshot_pending', message: 'pending' } }),
  getAnalysisFlow: vi.fn().mockResolvedValue({ ok: true, data: { agent: 'hermes', state: 'continuous', stages: [], updated_at: 'now' } }),
  getAnalysisJourney: vi.fn().mockResolvedValue({ ok: true, data: { jobs: [], dead_letters: [], updated_at: 'now' } }),
  getAnalysisQuestionContext: vi.fn().mockResolvedValue({ ok: true, data: { query: '', matches: [], conversation: [], retrieval: 'test' } }),
  getHermesUpgrades: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'no_request', message: 'no request' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { accepted: true } }),
  getWhaleSummary: vi.fn().mockResolvedValue({ ok: true, data: { coin: 'BTC', period_hours: 24, total_count: 0, total_usd: 0, net_exchange_flow_usd: 0, exchange_inflow_usd: 0, exchange_outflow_usd: 0, max_single_usd: 0, whale_transfer_count: 0, exchange_inflow_count: 0, exchange_outflow_count: 0 } }),
}))

function DashboardHistoryControls() {
  const navigate = useNavigate()
  return (
    <>
      <button onClick={() => navigate('/?qa=1&coin=SOL')}>plain entry</button>
      <button onClick={() => navigate('/?qa=1&workspace=compare')}>compare entry</button>
    </>
  )
}

function LocationProbe() {
  return <output aria-label="location">{useLocation().search}</output>
}

function RightRailTruthHarness({ tier = 'moderate', crossSignal }: { tier?: HermesTier; crossSignal?: CrossSourceSignal }) {
  const fallback = buildGalaxyModel(null).coins[0]
  const score = tier === 'healthy' ? 90 : tier === 'moderate' ? 70 : 40
  const coin = { ...fallback, score, tier, comps: [score, score, score, score] }
  const derivation = deriveSelected(coin)
  return (
    <HermesRightRail
      selCoin={coin}
      components={derivation.components}
      derivation={derivation}
      crossSignal={crossSignal}
      onOpenComposite={vi.fn()}
      onOpenDivergence={vi.fn()}
    />
  )
}

function LocaleSwitcher() {
  const { setLocale } = useHermesI18n()
  return (
    <>
      <button type="button" onClick={() => setLocale('en')}>use English</button>
      <button type="button" onClick={() => setLocale('zh-TW')}>使用中文</button>
    </>
  )
}

describe('HermesDashboard workspace navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(registerAnalysisQuestion).mockResolvedValue({
      ok: true,
      data: { question_id: 'test-question', job_id: null, state: 'queued', origin: 'manual' },
    })
    vi.mocked(getAnalysisQuestionContext).mockResolvedValue({
      ok: true,
      data: { query: '', matches: [], conversation: [], retrieval: 'test' },
    })
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      media: '(max-width: 560px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  it('keeps Analyze workspace open after top-bar click', async () => {
    vi.useRealTimers()
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: '分析' }))

    expect(screen.getByRole('region', { name: 'analyze workspace' })).toBeInTheDocument()

    await new Promise((resolve) => window.setTimeout(resolve, 450))

    expect(screen.getByRole('region', { name: 'analyze workspace' })).toBeInTheDocument()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
  }, 15_000)

  it('closes an open stage drilldown when switching to a status-only workspace', async () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
        <DashboardHistoryControls />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /來源掃描/ }))
    expect(screen.getByRole('dialog', { name: /Bitcoin — 來源掃描/ })).toBeInTheDocument()

    fireEvent.click(screen.getByText('compare entry'))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '市場 A · 階段資料尚未提供，無法開啟明細' })).toBeDisabled()
  })

  it('renders only the dashboard composer when Analyze is embedded on desktop', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1&workspace=analyze']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.queryByLabelText('問題')).not.toBeInTheDocument()
  })

  it('fills a competition question without context network or submission', async () => {
    vi.useRealTimers()
    const random = vi.spyOn(Math, 'random').mockReturnValue(0)
    render(
      <MemoryRouter initialEntries={['/?qa=1&coin=SOL']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
        <LocationProbe />
      </MemoryRouter>,
    )
    await new Promise((resolve) => window.setTimeout(resolve, 300))
    vi.mocked(getAnalysisQuestionContext).mockClear()

    fireEvent.click(screen.getByRole('button', { name: '隨機競賽題目' }))
    expect((screen.getByLabelText('交付 Hermes 的任務') as HTMLTextAreaElement).value).toMatch(/^請分析 SOL：/)
    await new Promise((resolve) => window.setTimeout(resolve, 300))

    expect(getAnalysisQuestionContext).not.toHaveBeenCalled()
    expect(registerAnalysisQuestion).not.toHaveBeenCalled()
    expect(screen.getByLabelText('location')).toHaveTextContent('?qa=1&coin=SOL')

    // Same deterministic pick is a no-op. It must not leave the one-shot skip
    // armed for the next real edit.
    fireEvent.click(screen.getByRole('button', { name: '隨機競賽題目' }))
    fireEvent.change(screen.getByLabelText('交付 Hermes 的任務'), { target: { value: '手動輸入的新問題' } })
    await waitFor(() => expect(getAnalysisQuestionContext).toHaveBeenCalledTimes(1))
    expect(getAnalysisQuestionContext).toHaveBeenCalledTimes(1)
    random.mockRestore()
  }, 15_000)

  it('opens the divergence drilldown through the native button click', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    const drilldown = screen.getByRole('button', { name: /跨來源分歧：.*；點擊查看/ })
    expect(drilldown).toHaveProperty('tabIndex', 0)
    drilldown.focus()
    expect(drilldown).toHaveFocus()
    fireEvent.click(drilldown)

    expect(screen.getByRole('dialog')).toHaveTextContent('跨來源分歧')
  })

  it.each(['Enter', ' '])('does not manually open the drilldown on raw %s keydown', (key) => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    fireEvent.keyDown(screen.getByRole('button', { name: /跨來源分歧：.*；點擊查看/ }), { key })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens and escapes the divergence glossary without opening the drilldown', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    const glossary = screen.getAllByRole('button', { name: /跨來源分歧/ })
      .find((button) => button.hasAttribute('aria-expanded'))
    expect(glossary).toBeDefined()
    fireEvent.click(glossary!)

    expect(screen.getByRole('note')).toHaveTextContent('不同來源對同一問題得出互相衝突的訊號')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('uses the live divergence summary for both visible and accessible truth', () => {
    const summary = '鏈上活動與新聞情緒方向相反'
    render(
      <HermesI18nProvider>
        <RightRailTruthHarness crossSignal={{ type: 'divergence', summary }} />
      </HermesI18nProvider>,
    )

    const drilldown = screen.getByRole('button', { name: `跨來源分歧：${summary}；點擊查看` })
    expect(drilldown).toHaveTextContent(summary)
  })

  it.each([
    ['healthy', '來源一致性正常 · Δ 8%'],
    ['moderate', '持續監控分歧 · Δ 24%'],
    ['danger', '偵測到來源衝突 · Δ 54%'],
  ] as const)('uses the %s tier fallback as visible and accessible truth', (tier, summary) => {
    render(
      <HermesI18nProvider>
        <RightRailTruthHarness tier={tier} />
      </HermesI18nProvider>,
    )

    const drilldown = screen.getByRole('button', { name: `跨來源分歧：${summary}；點擊查看` })
    expect(drilldown).toHaveTextContent(summary)
  })

  it('keeps the tier fallback and accessible name localized from the same summary', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher />
        <RightRailTruthHarness tier="healthy" />
      </HermesI18nProvider>,
    )

    const zhDrilldown = screen.getByRole('button', { name: '跨來源分歧：來源一致性正常 · Δ 8%；點擊查看' })
    expect(zhDrilldown).toHaveTextContent('來源一致性正常 · Δ 8%')

    fireEvent.click(screen.getByRole('button', { name: 'use English' }))

    const enDrilldown = screen.getByRole('button', { name: 'CROSS-SOURCE DIVERGENCE：Alignment nominal · Δ 8%；tap to review' })
    expect(enDrilldown).toHaveTextContent('Alignment nominal · Δ 8%')
    fireEvent.click(screen.getByRole('button', { name: '使用中文' }))
  })

  // N70：角度不再是使用者選的下拉（改由 ?mode= 或題目文字推導），所以這條改成
  // 從深連結進來驗映射。fundamentals/catalyst → hypothesis 跟後端
  // `analysis_flow.MODES` 同源。
  it.each(['fundamentals', 'catalyst'])('maps %s to hypothesis', async (mode) => {
    render(
      <MemoryRouter initialEntries={[`/?qa=1&mode=${mode}`]}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider><LocationProbe />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))

    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent('type=hypothesis'))
  })

  it('N2: submit button leaves its loading label once the embedded AnalyzePage settles into an error (not just success)', async () => {
    // registerAnalysisQuestion resolves `ok:true` but without `job_id`
    // (see mock above) — AnalyzePage treats that as a failure and settles
    // into its error state. Before the fix, the left-rail submit button's
    // `phase` only reset to 'ready' when AnalyzePage produced *successful*
    // telemetry, so it stayed stuck on the loading label ("Hermes 自動分析中…")
    // forever on this error path.
    type RegistrationResult = Awaited<ReturnType<typeof registerAnalysisQuestion>>
    let settleRegistration!: (value: RegistrationResult) => void
    const registrationSettled = new Promise<RegistrationResult>((resolve) => {
      settleRegistration = resolve
    })
    vi.mocked(registerAnalysisQuestion).mockReturnValueOnce(registrationSettled)

    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
    expect(registerAnalysisQuestion).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Hermes 自動分析中…' })).toBeDisabled()

    await act(async () => {
      settleRegistration({
        ok: true,
        data: { question_id: 'test-question', job_id: null, state: 'queued', origin: 'manual' },
      })
      await registrationSettled
    })
    await waitFor(() => {
      const submit = screen.getByRole('button', { name: /立即重新分析/ })
      expect(submit).not.toBeDisabled()
    })
    expect(screen.queryByRole('button', { name: 'Hermes 自動分析中…' })).not.toBeInTheDocument()
  })

  it('resets missing mode and question on a history entry', async () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1&coin=ETH&mode=catalyst&q=舊問題']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
        <DashboardHistoryControls />
      </MemoryRouter>,
    )
    // N70：角度是推導值，不再有下拉可讀，改看左軌那條唯讀說明。
    const note = () => document.querySelector('.hermes-focus-derived')?.textContent ?? ''
    await waitFor(() => expect(note()).toContain('價格催化因子'))
    fireEvent.click(screen.getByRole('button', { name: 'plain entry' }))

    await waitFor(() => expect(note()).toContain('風險評估'))
    expect(screen.getByRole('textbox')).toHaveValue('分析SOL近期市場狀況，整合多源資料')
  })

  it('N7 (CEO round 2 retest): localizes the left-rail beginner intent-picker cards to EN, not the hard-coded zh-TW labels', () => {
    // CEO's round-2 retest flagged that the five "what do you want to know?"
    // intent cards in the beginner-mode left rail (default on) stayed
    // hard-coded zh-TW even when the UI locale was switched to EN — this was
    // the one concrete miss from N7 round 1's sweep.
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider>
          <LocaleSwitcher />
          <HermesDashboard />
        </HermesI18nProvider>
      </MemoryRouter>,
    )

    // baseline: default locale is zh-TW, card shows the Chinese label
    expect(screen.getByRole('button', { name: /這個幣現在可信嗎？/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'use English' }))

    expect(screen.getByRole('button', { name: /Is this coin trustworthy right now\?/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Any manipulation risk\?/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Is this news credible\?/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /What's moving the price\?/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Why did the trust score drop\?/ })).toBeInTheDocument()
    expect(screen.queryByText('這個幣現在可信嗎？')).not.toBeInTheDocument()
  })
})

/** N69：主入口不得再出現「官方題型」下拉。
 * `docs/competition/COMPETITION-OFFICIAL.md` 那節標題是「範例題型」，同文件
 * 又寫明現場才抽題、無法預知——三選一是把主辦方的例子當成可出題的範圍。
 * 使用者要打的是自由題目（後端 register_question 本來就收任意 1..1000 字）。
 * 這條守住它不被加回來；題型本身的正確性由 analysisTaxonomy.test.ts 顧。 */
describe('N69 官方題型下拉', () => {
  it('主入口不提供題型三選一（那三種是範例不是限制）', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(container.querySelector('#hermes-qtype')).toBeNull()
    const options = [...container.querySelectorAll('option')].map((o) => o.textContent?.trim())
    expect(options).not.toContain('多源整合')
    expect(options).not.toContain('假設驗證')
    // 負向對照的另一半：N70 之後角度那顆下拉也拿掉了，改成唯讀說明，所以這裡
    // 改用「輸入框還在」當存活證明——沒有它，整塊表單消失也會讓上面假綠。
    expect(container.querySelector('textarea')).not.toBeNull()
  })
})

/** N70（CEO：「分析角度 也不給使用者選」）：五個角度在後端只映射到兩種
 * QuestionType（analysis_flow.py:55-62），對一般使用者是無從判斷的選擇題。
 * 改由題目文字推導（recommendAnalysisMode），?mode= 深連結仍然優先。 */
describe('N70 分析角度', () => {
  it('主入口不提供角度下拉，改成告訴使用者這次用哪個角度', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    // 負向對照：改動前這行抓得到 <select id="hermes-focus">，會 fail。
    expect(container.querySelector('#hermes-focus')).toBeNull()
    const note = container.querySelector('.hermes-focus-derived')
    expect(note).not.toBeNull()
    expect(note?.textContent).toContain('Risk assessment')
  })

  it('?mode= 仍然決定角度（排程回連與既有深連結不能斷）', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1&mode=fundamentals']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(container.querySelector('.hermes-focus-derived')?.textContent).toContain('Fundamentals')
  })
})

/** N70（CEO：「能按的都移到左邊欄」）：頂欄只留顯示。 */
describe('N70 控制項位置', () => {
  it('導覽與模式控制項都在左軌，不在頂欄', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    const topbar = container.querySelector('.hermes-topbar')
    const rail = container.querySelector("[data-region='left-rail']")
    expect(topbar).not.toBeNull()
    expect(rail).not.toBeNull()
    // 負向對照：改動前這五顆全都在頂欄，這個 expect 會 fail。
    expect(topbar?.querySelectorAll('.hermes-nav-item').length).toBe(0)
    expect(rail?.querySelectorAll('.hermes-nav-item').length).toBeGreaterThan(0)
    // 頂欄只留兩顆：遙測膠囊（狀態摘要，點了展開）與語言切換。
    // 語言切換是 N72 由 CEO 直接指定的例外（「中文 英文 放右上，不要拿到
    // 左邊很怪」）——它是全域偏好、慣例位置就在右上角，不屬於「分析功能」。
    // 除了這兩顆以外任何按鈕都不該回到頂欄。
    const topbarButtons = [...(topbar?.querySelectorAll('button') ?? [])]
    expect(topbarButtons.map((b) => b.className).sort()).toEqual(
      ['hermes-telemetry-chip', 'hermes-topbar-lang'],
    )
  })

  // N70（CEO：「使用者要按要點的功能統一到最左邊的選單欄中」）：新手模式是預設
  // 值，導覽曾經在新手模式只留「分析」。角度改由題目推導後，推導器不會產生
  // comparison，所以左軌那顆「比較」是唯一入口——藏起來等於新手沒有比較功能。
  // 負向對照：改動前這條會 fail（filter 只留 analyze，比較/歷史趨勢/來源狀態/
  // 成本 四顆都不在 DOM 裡）。
  it('新手模式（預設）也看得到全部五項導覽', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    const rail = container.querySelector("[data-region='left-rail']")
    const labels = [...(rail?.querySelectorAll('.hermes-nav-item') ?? [])].map((el) => el.textContent?.trim())
    // 這條排在 LocaleSwitcher 測試之後，語系 cookie 已被寫成 en，所以斷言用
    // 英文標籤（跟同檔其他測試一樣依位置決定語系）。
    for (const label of ['ANALYZE', 'COMPARE', 'HISTORY', 'SOURCES', 'COSTS']) {
      expect(labels).toContain(label)
    }
  })

  // N70（CEO：「一樣改到左邊選單」）：艦體升級也是能按的功能，新手模式不該藏。
  // 負向對照：改動前它包在 `{!beginnerMode && …}` 裡，預設新手模式下
  // `.hermes-ship-toggle` 為 null——下面的 expect 會 fail。
  it('新手模式也看得到艦體升級', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    const rail = container.querySelector("[data-region='left-rail']")
    expect(rail?.querySelector('.hermes-ship-toggle')).not.toBeNull()
  })

  // N70（CEO：「新手模式不要動選單，動作是切回首頁、中間顯示新手板」）：
  // 停在工作區時切進新手模式，畫面必須回到首頁並顯示新手板。
  // 負向對照：改動前 workspace 參數不會被清掉，`beginnerMode && !activeModule`
  // 不成立，新手板不會出現——下面兩條 expect 都會 fail。
  it('切進新手模式會回首頁並顯示新手板', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1&workspace=compare&experience=full']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    const rail = container.querySelector("[data-region='left-rail']")
    const toggle = rail?.querySelector('.hermes-mode-toggle') as HTMLButtonElement
    expect(toggle).not.toBeNull()
    if (toggle.getAttribute('aria-pressed') === 'true') fireEvent.click(toggle)
    expect(container.querySelector('.hermes-beginner-narrative')).toBeNull()
    fireEvent.click(toggle)
    expect(container.querySelector('.hermes-beginner-narrative')).not.toBeNull()
  })

  it('比較工作區開啟時保留不可互動的低干擾星系背景', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/?qa=1&workspace=compare&experience=full']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )
    const galaxy = container.querySelector("[data-region='galaxy']")
    expect(galaxy).not.toBeNull()
    expect(galaxy).toHaveClass('hermes-galaxy-background')
    expect(galaxy).toHaveAttribute('aria-hidden', 'true')
    expect(galaxy).toHaveAttribute('inert')
  })
})
