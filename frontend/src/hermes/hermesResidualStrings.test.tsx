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

vi.mock('../lib/endpoints', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/endpoints')>()
  return { ...actual, getHealth: vi.fn().mockResolvedValue({ ok: true, data: { version: 'dev' } }) }
})

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

describe('N17: en mode has no residual Chinese aria-labels — HermesTopBar', () => {
  it('home logo button aria-label switches to English', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <HermesTopBar />
      </HermesI18nProvider>,
    )
    expect(screen.getByRole('button', { name: 'HERMES 主頁' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('button', { name: 'HERMES 主頁' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'HERMES home' })).toBeInTheDocument()
  })

  it('reduced-motion toggle aria-label AND title both switch to English', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <HermesTopBar />
      </HermesI18nProvider>,
    )
    const zhButton = screen.getByRole('button', { name: '啟用低動態模式' })
    expect(zhButton).toHaveAttribute('title', '啟用低動態模式')
    fireEvent.click(screen.getByRole('button', { name: 'go en' }))
    expect(screen.queryByRole('button', { name: '啟用低動態模式' })).not.toBeInTheDocument()
    const enButton = screen.getByRole('button', { name: 'Enable reduced motion' })
    expect(enButton).toHaveAttribute('title', 'Enable reduced motion')
  })

  it('help toggle aria-label switches to English', () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher to="en" />
        <HermesTopBar />
      </HermesI18nProvider>,
    )
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
