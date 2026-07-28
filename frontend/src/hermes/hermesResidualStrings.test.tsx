// @vitest-environment jsdom
//
// N26/N17/N19: cross-check that residual English (visible in zh-TW) and
// residual Chinese (visible in en, via aria-label) reported by CEO's browser
// scan are actually gone from the rendered DOM — not just present as dict
// keys. Each assertion renders the real component tree through
// HermesI18nProvider and reads screen text / accessible names, matching the
// "斷言要真的渲染頁面掃可見文字節點與 aria-label" rule.

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CurrencyGalaxy from './CurrencyGalaxy'
import StageBar from './StageBar'
import HermesRightRail from '../hermes/HermesRightRail'
import StageDrilldown from './StageDrilldown'
import { HermesI18nProvider, useHermesI18n } from './hermesI18n'
import { buildGalaxyModel, deriveSelected } from '../lib/hermesData'
import type { AnalysisFlowData, AnalysisJourneyData } from '../lib/endpoints'
import Header from '../components/Header'
import TrainingStatusCard from '../components/TrainingStatusCard'
import BridgeWorkspaceShell from '../components/BridgeWorkspaceShell'
import HermesTopBar from './HermesTopBar'
import HermesLeftRail from './HermesLeftRail'
import AssetContextLookupPage from '../pages/AssetContextLookupPage'
import PeerMetricsPage from '../pages/PeerMetricsPage'
import EcoLinkPage from '../pages/EcoLinkPage'
import HermesOnboarding from './HermesOnboarding'
import HermesUpgradeShip from './HermesUpgradeShip'
import PlainLanguageResultSummary from '../components/PlainLanguageResultSummary'
import { getAssetContext, getPeerMetrics, getEcoLink } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'

vi.mock('../lib/endpoints', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/endpoints')>()
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ ok: true, data: { version: 'dev' } }),
    getAssetContext: vi.fn(),
    getPeerMetrics: vi.fn(),
    getEcoLink: vi.fn(),
  }
})

function analyzeDataFixture(): AnalyzeData {
  return {
    report: {
      market_judgment: 'ARB looks stable',
      decision_state: 'normal',
      calibrated_confidence: 0.8,
      key_basis: [{ claim: 'reason one' }, { claim: 'reason two' }],
      limits: [],
    },
    evidence: [{}],
  } as unknown as AnalyzeData
}

function LocaleSwitcher({ to }: { to: 'en' | 'zh-TW' }) {
  const { setLocale } = useHermesI18n()
  return <button type="button" onClick={() => setLocale(to)}>go {to}</button>
}

function galaxyHarness() {
  const model = buildGalaxyModel(null)
  return { model, coin: model.coins[0] }
}

beforeEach(() => {
  // reset the locale cookie between tests — HermesI18nProvider reads it on
  // mount, and vitest/jsdom doesn't reset document.cookie between test
  // cases, so a `setLocale('en')` in one test would otherwise leak into
  // the next test's "starts in zh-TW" assumption.
  document.cookie = 'trustforge_hermes_locale=; Max-Age=0; Path=/'
})

describe('N26: zh-TW mode has no residual English strings', () => {
  it('CurrencyGalaxy: viewport eyebrow + title switch to Chinese under zh-TW', () => {
    const { model, coin } = galaxyHarness()
    render(
      <HermesI18nProvider>
        <CurrencyGalaxy model={model} selectedId={coin.id} hoveredId={null} focusPulse={false} onSelect={vi.fn()} onHover={vi.fn()} />
      </HermesI18nProvider>,
    )
    expect(screen.queryByText('BRIDGE MAIN VIEWPORT')).not.toBeInTheDocument()
    expect(screen.queryByText('Global Currency Galaxy')).not.toBeInTheDocument()
    expect(screen.getByText('主橋接視埠')).toBeInTheDocument()
    expect(screen.getByText('全域幣種星系')).toBeInTheDocument()
  })

  it('StageBar: engine activity strip + engine badge are localized under zh-TW', () => {
    const { coin } = galaxyHarness()
    const derivation = deriveSelected(coin)
    render(
      <HermesI18nProvider>
        <StageBar selCoin={coin} derivation={derivation} selectedStage={null} onSelectStage={vi.fn()} activity={{ status: 'ready', coin: coin.name, mode: 'risk', question: 'q' }} />
      </HermesI18nProvider>,
    )
    // engine activity line: mode is raw 'risk' and must not leak untranslated
    expect(screen.queryByText('risk')).not.toBeInTheDocument()
    expect(screen.getByText('風險評估')).toBeInTheDocument()
    // engine badge
    expect(screen.queryByText('ENGINE · CONTINUOUS')).not.toBeInTheDocument()
    expect(screen.getByText('引擎 · 持續運作')).toBeInTheDocument()
    // continuous state label (no active work)
    expect(screen.queryByText('CONTINUOUS')).not.toBeInTheDocument()
    expect(screen.getByText('持續運作')).toBeInTheDocument()
  })

  it('HermesRightRail: CONTINUOUS ENGINE / RUNNING / "<mode> · <state>" are localized under zh-TW', () => {
    const { coin } = galaxyHarness()
    const derivation = deriveSelected(coin)
    const journey: AnalysisJourneyData = {
      jobs: [{
        job_id: 'job-1', coin: coin.name, mode: 'risk', question: 'q', snapshot_id: 's1', state: 'completed',
        current_stage: 'composite', retry_count: 0, error: null, updated_at: 0, attempts: [], stages: [],
      }],
      dead_letters: [],
      updated_at: 'now',
    }
    render(
      <HermesI18nProvider>
        <HermesRightRail
          selCoin={coin}
          components={derivation.components}
          derivation={derivation}
          journey={journey}
          onOpenComposite={vi.fn()}
          onOpenDivergence={vi.fn()}
        />
      </HermesI18nProvider>,
    )
    expect(screen.queryByText('CONTINUOUS ENGINE')).not.toBeInTheDocument()
    expect(screen.getByText('持續運作引擎')).toBeInTheDocument()
    expect(screen.queryByText(/RUNNING/)).not.toBeInTheDocument()
    expect(screen.getByText(/運作中/)).toBeInTheDocument()
    // "risk · completed" must not leak — both halves localized
    expect(screen.queryByText('risk · completed')).not.toBeInTheDocument()
    expect(screen.getByText('風險評估 · 已完成')).toBeInTheDocument()
  })

  it('StageDrilldown: live worker debug row localizes the raw mode string', () => {
    const { coin } = galaxyHarness()
    const derivation = deriveSelected(coin)
    const flow: AnalysisFlowData = {
      agent: 'hermes', state: 'active', updated_at: 'now',
      stages: [{
        id: 'scan', queued: 0,
        current: { coin: coin.name, mode: 'risk', question: 'q', snapshot_id: 's1', started_at: 0, retry_count: 0, error: null },
      }],
    }
    render(
      <HermesI18nProvider>
        <StageDrilldown selCoin={coin} derivation={derivation} selectedStage="scan" onClose={vi.fn()} flow={flow} />
      </HermesI18nProvider>,
    )
    expect(screen.queryByText(/模式：risk/)).not.toBeInTheDocument()
    expect(screen.getByText(/模式：風險評估/)).toBeInTheDocument()
  })

  it('HermesTopBar: "HERMES: <status>" active-state badge uses a localized separator under zh-TW', () => {
    render(
      <HermesI18nProvider>
        <HermesTopBar />
      </HermesI18nProvider>,
    )
    // the raw ASCII "HERMES:" (half-width colon) must not leak into zh-TW —
    // only the fully-localized "HERMES：" (full-width colon) form may appear.
    expect(screen.queryByText(/HERMES: /)).not.toBeInTheDocument()
    expect(screen.getByText(/HERMES：/)).toBeInTheDocument()
  })
})

// N70（CEO：「能按的都移到左邊欄」）：主頁鈕／低動態／說明這三顆已從頂欄搬進
// 左軌（HermesLeftRail 的 `hermes-rail-controls`）。這裡把 render 目標換成左軌，
// 斷言本身一字未改——語系切換與 aria 規範的要求跟著控制項走，不因搬家而放寬。
describe('N17: en mode has no residual Chinese aria-labels — 左軌控制項', () => {
  const railProps = {
    hermesMessage: '',
    hasOrder: false,
    focus: 'risk' as const,
    query: '',
    submitLabel: 'go',
    onQuery: () => {},
    onSubmit: () => {},
  }
  const renderRail = (withSwitcher = true) =>
    render(
      <MemoryRouter>
        <HermesI18nProvider>
          {withSwitcher ? <LocaleSwitcher to="en" /> : null}
          <HermesLeftRail {...railProps} onHome={() => {}} onHelp={() => {}} onReducedMotionToggle={() => {}} />
        </HermesI18nProvider>
      </MemoryRouter>,
    )

  it('home logo button aria-label switches to English', () => {
    renderRail()
    expect(screen.getByRole('button', { name: 'HERMES 主頁' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('button', { name: 'HERMES 主頁' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'HERMES home' })).toBeInTheDocument()
  })

  // N62：原本這條斷言 aria-label 也跟著切語言。aria-label 已經拿掉——它會
  // 「取代」可見文字，讓螢幕閱讀器唸的名稱跟眼睛看到的對不上（WCAG 2.5.3）。
  // 這條的用意（兩種語言都要換掉、title 也要換）原封不動保留，只是可及名稱
  // 現在來自可見文字本身。
  it('reduced-motion toggle label AND title both switch to English', () => {
    renderRail()
    const zhButton = screen.getByRole('button', { name: '動態' })
    expect(zhButton).toHaveAttribute('title', '一般動態（點擊開啟低動態）')
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('button', { name: '動態' })).not.toBeInTheDocument()
    const enButton = screen.getByRole('button', { name: 'Motion' })
    expect(enButton).toHaveAttribute('title', 'Full motion (click to reduce)')
  })

  // 可及名稱必須「包含」可見文字，否則語音操作使用者照著螢幕唸就點不到。
  // 順便釘住狀態只由 aria-pressed 承載，標籤裡不得再混入 ▶/⏸ 這種動作符號
  // ——播放器圖示讀作動作、文字讀作狀態，兩套語意指向相反方向，正是使用者
  // 看到「兩個狀態反了」的原因。
  it('reduced-motion toggle: accessible name equals its visible label, state lives in aria-pressed', () => {
    renderRail(false)
    const btn = screen.getByRole('button', { name: '動態' })
    expect(btn).not.toHaveAttribute('aria-label')
    expect(btn.textContent?.trim()).toBe('動態')
    expect(btn).toHaveAttribute('aria-pressed', 'false')
    expect(btn.textContent).not.toMatch(/[▶⏸]/)
  })

  it('help toggle aria-label switches to English', () => {
    renderRail()
    expect(screen.getByRole('button', { name: '開啟新手說明' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('button', { name: '開啟新手說明' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open beginner help' })).toBeInTheDocument()
  })
})

describe('N17: en mode has no residual Chinese aria-labels (beyond the locked HermesTopBar.tsx)', () => {
  it('Header: main nav landmark aria-label switches to English', () => {
    render(
      <MemoryRouter>
        <HermesI18nProvider>
          <LocaleSwitcher to="en" />
          <Header />
        </HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('navigation', { name: '主導覽' })).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })

  it('TrainingStatusCard: status-light aria-label switches to English', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({
        ok: true,
        data: {
          training_data: { total_records: 10, has_direction: 5, direction_ratio: 0.5, per_coin: {} },
          backfill: null,
          upgrade_threshold: { target: 100, current: 10, met: false, pct: 10 },
        },
      }),
    }) as unknown as typeof fetch)
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <TrainingStatusCard />
      </HermesI18nProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(await screen.findByLabelText('Status: In progress')).toBeInTheDocument()
    expect(screen.queryByLabelText('Status: 進行中')).not.toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it('BridgeWorkspaceShell: side-rail + engine-deck landmark aria-labels switch to English', () => {
    render(
      <MemoryRouter>
        <HermesI18nProvider>
          <LocaleSwitcher to="en" />
          <BridgeWorkspaceShell><div /></BridgeWorkspaceShell>
        </HermesI18nProvider>
      </MemoryRouter>,
    )
    expect(screen.getByRole('complementary', { name: '艦橋系統狀態' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: '艦橋遙測' })).toBeInTheDocument()
    expect(screen.getByRole('contentinfo', { name: 'Hermes 工作流能量匯流' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'go en' }))

    expect(screen.queryByRole('complementary', { name: '艦橋系統狀態' })).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: '艦橋遙測' })).not.toBeInTheDocument()
    expect(screen.queryByRole('contentinfo', { name: 'Hermes 工作流能量匯流' })).not.toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Bridge system status' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Bridge telemetry' })).toBeInTheDocument()
    expect(screen.getByRole('contentinfo', { name: 'Hermes workflow energy conduit' })).toBeInTheDocument()
  })
})

// N34-1 batch 1: AssetContextLookupPage / PeerMetricsPage / EcoLinkPage /
// HermesOnboarding / HermesUpgradeShip / PlainLanguageResultSummary had zero
// i18n coverage before this change (grep useHermesI18n → 0 hits). Each check
// below renders the real component through HermesI18nProvider in both
// locales and reads the actual DOM text — not dict-key presence.
describe('N34-1: batch 1 components render localized copy in both locales', () => {
  it('AssetContextLookupPage: title + empty state switch between zh-TW and en', async () => {
    vi.mocked(getAssetContext).mockResolvedValue({ ok: true, data: { asset_context: null } })
    render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={['/asset-context']}>
          <LocaleSwitcher to="en" />
          <AssetContextLookupPage />
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
    expect(screen.getByText('30 秒看懂一個代幣的定位')).toBeInTheDocument()
    expect(screen.queryByText('Understand a token in 30 seconds')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(await screen.findByText('No context data available for this asset yet.')).toBeInTheDocument()
    expect(screen.getByText('Understand a token in 30 seconds')).toBeInTheDocument()
    expect(screen.queryByText('30 秒看懂一個代幣的定位')).not.toBeInTheDocument()
    expect(screen.queryByText('目前無此資產的脈絡資料。')).not.toBeInTheDocument()
  })

  it('PeerMetricsPage: title switches between zh-TW and en', async () => {
    vi.mocked(getPeerMetrics).mockResolvedValue({ ok: true, data: { illustrative: true, snapshot: null, peers: [] } })
    render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={['/peer-metrics']}>
          <LocaleSwitcher to="en" />
          <PeerMetricsPage />
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    expect(await screen.findByText('目前無 asset:arb 的同層比較資料。')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Peer 同層比較' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(await screen.findByText('No peer comparison data available for asset:arb yet.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Peer Comparison' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Peer 同層比較' })).not.toBeInTheDocument()
  })

  it('EcoLinkPage: title switches between zh-TW and en', async () => {
    vi.mocked(getEcoLink).mockResolvedValue({
      ok: true,
      data: { illustrative: true, verdict: 'insufficient_data', message: '資料不足，無法判定', impact_paths: [] },
    })
    render(
      <HermesI18nProvider>
        <MemoryRouter initialEntries={['/eco-link']}>
          <LocaleSwitcher to="en" />
          <EcoLinkPage />
        </MemoryRouter>
      </HermesI18nProvider>,
    )
    expect(await screen.findByRole('heading', { name: 'EcoLink 影響路徑' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(await screen.findByRole('heading', { name: 'EcoLink Impact Path' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'EcoLink 影響路徑' })).not.toBeInTheDocument()
  })

  it('HermesOnboarding: step-1 copy switches between zh-TW and en', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <HermesOnboarding open onClose={vi.fn()} />
      </HermesI18nProvider>,
    )
    expect(screen.getByText('先選一個幣，再問一個問題')).toBeInTheDocument()
    expect(screen.queryByText('Pick a coin, then ask a question')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.getByText('Pick a coin, then ask a question')).toBeInTheDocument()
    expect(screen.queryByText('先選一個幣，再問一個問題')).not.toBeInTheDocument()
  })

  it('HermesUpgradeShip: header title + footer flow copy switch between zh-TW and en', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <HermesUpgradeShip data={null} loading={false} onClose={vi.fn()} onRefresh={vi.fn()} />
      </HermesI18nProvider>,
    )
    expect(screen.getByText('HERMES 升級控制台')).toBeInTheDocument()
    expect(screen.getByText('禁止遞回升級', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('HERMES UPGRADE CONTROL')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.getByText('HERMES UPGRADE CONTROL')).toBeInTheDocument()
    expect(screen.getByText('no recursive upgrades', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('HERMES 升級控制台')).not.toBeInTheDocument()
  })

  it('PlainLanguageResultSummary: reasons heading + disclaimer switch between zh-TW and en', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <PlainLanguageResultSummary data={analyzeDataFixture()} />
      </HermesI18nProvider>,
    )
    expect(screen.getByText('三個主要原因')).toBeInTheDocument()
    expect(screen.queryByText('Three key reasons')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.getByText('Three key reasons')).toBeInTheDocument()
    expect(screen.queryByText('三個主要原因')).not.toBeInTheDocument()
  })
})
