// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { registerAnalysisQuestion } from '../lib/endpoints'
import { buildGalaxyModel, deriveSelected, type HermesTier } from '../lib/hermesData'
import type { CrossSourceSignal } from '../lib/types'
import HermesRightRail from '../hermes/HermesRightRail'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import HermesDashboard from './HermesDashboard'

vi.mock('../lib/endpoints', () => ({
  getOverview: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getCosts: vi.fn().mockResolvedValue({ ok: true, data: { total_cost_usd: 0 } }),
  getHealth: vi.fn().mockResolvedValue({ ok: true, data: { version: 'dev' } }),
  getAnalysisSnapshot: vi.fn().mockResolvedValue({ ok: false, error: { code: 'snapshot_pending', message: 'pending' } }),
  getAnalysisFlow: vi.fn().mockResolvedValue({ ok: true, data: { agent: 'hermes', state: 'continuous', stages: [], updated_at: 'now' } }),
  getAnalysisJourney: vi.fn().mockResolvedValue({ ok: true, data: { jobs: [], dead_letters: [], updated_at: 'now' } }),
  getAnalysisQuestionContext: vi.fn().mockResolvedValue({ ok: true, data: { query: '', matches: [], conversation: [], retrieval: 'test' } }),
  getHermesUpgrades: vi.fn().mockResolvedValue({ ok: false, error: { code: 'offline', message: 'offline' } }),
  getAnalyze: vi.fn().mockResolvedValue({ ok: false, error: { code: 'no_request', message: 'no request' } }),
  registerAnalysisQuestion: vi.fn().mockResolvedValue({ ok: true, data: { accepted: true } }),
}))

function DashboardHistoryControls() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/?qa=1&coin=SOL')}>plain entry</button>
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

  it('renders only the dashboard composer when Analyze is embedded on desktop', () => {
    render(
      <MemoryRouter initialEntries={['/?qa=1&workspace=analyze']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.queryByLabelText('問題')).not.toBeInTheDocument()
  })

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

  // 方案 B 後：角度選單以 id 為值（不再是翻譯後的 label），且題型獨立一顆下拉。
  // 使用者沒動過題型時，角度仍會帶出既有的預設題型映射。
  it.each(['fundamentals', 'catalyst'])('maps %s to hypothesis', async (mode) => {
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider><LocationProbe />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByLabelText('分析角度（選填）')).toHaveValue('risk'))
    fireEvent.change(screen.getByLabelText('分析角度（選填）'), { target: { value: mode } })
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
    render(
      <MemoryRouter initialEntries={['/?qa=1']}>
        <HermesI18nProvider><HermesDashboard /></HermesI18nProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /立即重新分析/ }))
    expect(registerAnalysisQuestion).toHaveBeenCalled()

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
    await waitFor(() => expect(screen.getByLabelText('分析角度（選填）')).toHaveValue('catalyst'))
    fireEvent.click(screen.getByRole('button', { name: 'plain entry' }))

    await waitFor(() => expect(screen.getByLabelText('分析角度（選填）')).toHaveValue('risk'))
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
