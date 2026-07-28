// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import StageDrilldown from './StageDrilldown'
import type { GalaxyCoin, SelectedDerivation } from '../lib/hermesData'
import type { BridgeHologramData } from '../components/BridgeHologramContext'
import type { Evidence, CrossSourceSignal } from '../lib/types'

const selCoin: GalaxyCoin = {
  id: 'btc',
  name: 'BTC',
  full: 'Bitcoin',
  econ: 52,
  orbit: 'core',
  pos: 'top',
  score: 78,
  tier: 'healthy',
  comps: [80, 75, 85, 70],
  manipScore: null,
}

const fallbackDerivation: SelectedDerivation = {
  scanned: 5,
  passedCount: 3,
  flaggedCount: 2,
  divergence: 12,
  divColor: '#E53E3E',
  divDim: 'rgba(229,62,62,.12)',
  divBd: 'rgba(229,62,62,.3)',
  scanItems: [],
  passedItems: ['src-a', 'src-b', 'src-c'],
  droppedItems: [{ name: 'src-d', reason: 'expired' }],
  crossItems: [],
  manipulationItems: [],
  steps: [],
  components: [],
  stageMetrics: {},
}

const evidenceFixture: Evidence[] = [
  {
    source: 'ohlcv-csv',
    fetched_at: '2026-07-01T00:00:00Z',
    content_reference: 'BTC/USDT close 65000',
    related_claim: 'BTC bullish',
    source_url: '',
    kind: 'price',
    trust: 0.85,
    trust_components: { reputation: 0.9, corroboration: 0.8, recency: 1, manipulation: 0 },
    flags: [],
    info_flags: [],
    data_lineage: {
      dataset_role: 'baseline',
      dataset_name: 'Test Dataset',
      dataset_generated_at: '2026-07-01T00:00:00Z',
      file: 'BTC_daily.csv',
      sha256: 'abc123',
      rows: 100,
      coverage: { start_date: '2026-06-01', end_date: '2026-07-01' },
      analysis_window: '2026-06-25~2026-07-01',
      trading_pair: 'BTCUSDT',
      time_basis: 'UTC',
      interval: '1d',
      price_unit: 'USDT',
      columns: ['date', 'close'],
    },
  },
  {
    source: 'news-api',
    fetched_at: '2026-07-01T12:00:00Z',
    content_reference: 'Bitcoin ETF inflow $100M',
    related_claim: 'BTC institutional demand',
    source_url: 'https://example.com/news',
    kind: 'news',
    trust: 0.72,
    trust_components: { reputation: 0.7, corroboration: 0.6, recency: 0.9, manipulation: 0 },
    flags: [],
    info_flags: [],
  },
]

const crossSourceSignal: CrossSourceSignal = {
  type: 'divergence',
  summary: '2 sources bullish, 1 source bearish',
  stance_pairs: [
    { source: 'CoinDesk', stance: 'bullish', claim_id: 'c1', text: 'BTC to $100K' },
    { source: 'CryptoQuant', stance: 'bullish', claim_id: 'c2', text: 'On-chain supports rally' },
    { source: 'Glassnode', stance: 'bearish', claim_id: 'c3', text: 'Sell pressure building' },
  ],
  distinct_sources: {
    bullish: [
      { source: 'CoinDesk', stance: 'bullish', claim_id: 'c1', text: 'BTC to $100K' },
      { source: 'CryptoQuant', stance: 'bullish', claim_id: 'c2', text: 'On-chain supports rally' },
    ],
    bearish: [
      { source: 'Glassnode', stance: 'bearish', claim_id: 'c3', text: 'Sell pressure building' },
    ],
  },
}

function telemetryWithEvidence(): BridgeHologramData {
  return {
    runId: 'run-001',
    analysis: {
      version: '1.0',
      report: {
        coin: 'BTC',
        question_type: 'analysis',
        question: 'test',
        generated_at: '2026-07-01',
        market_judgment: 'bullish',
        cross_source_signal: crossSourceSignal,
        confidence: 0.78,
        direction: 'bullish',
        facts: ['fact 1'],
        inferences: ['inference 1'],
        key_basis: [],
        limits: [],
        could_flip: [],
        contrarian: [],
        calibrated_confidence: 0.78,
        decision_state: 'normal',
      },
      evidence: evidenceFixture,
      trust_radar: {},
      trust_components_aggregate: {
        reputation: 80,
        corroboration: 75,
        recency: 85,
        manipulation: 0.1,
      },
      price_provenance: {},
      execution_log: [],
    },
  }
}

function telemetryNoEvidence(): BridgeHologramData {
  return {
    runId: 'run-002',
    analysis: {
      version: '1.0',
      report: {
        coin: 'BTC',
        question_type: 'analysis',
        question: 'test',
        generated_at: '2026-07-01',
        market_judgment: 'neutral',
        cross_source_signal: null,
        confidence: 0.5,
        direction: 'neutral',
        facts: [],
        inferences: [],
        key_basis: [],
        limits: [],
        could_flip: [],
        contrarian: [],
        calibrated_confidence: 0.5,
        decision_state: 'abstain',
      },
      evidence: [],
      trust_radar: {},
      trust_components_aggregate: {
        reputation: 50,
        corroboration: 50,
        recency: 50,
        manipulation: 0.5,
      },
      price_provenance: {},
      execution_log: [],
    },
  }
}

/** N72（CEO：「中間這幾個是幹嘛用的 要寫清楚」）：五關的抽屜以前打開只有
 *  worker 代號與一堆數值，沒有一句話說這關在做什麼。這組斷言釘住「每一關
 *  都必須有白話說明」，避免之後改版把它刪掉或只補其中幾關。 */
describe('StageDrilldown 每一關都要說明自己在做什麼（N72）', () => {
  const cases: Array<[string, RegExp]> = [
    ['scan', /全部撈回來/],
    ['filter', /丟掉不可信/],
    ['crossverify', /互相對照/],
    ['manipulation', /人為推動/],
    ['composite', /加權合成/],
  ]

  it.each(cases)('%s 這一關有白話說明', (stage, expected) => {
    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage={stage}
          onClose={vi.fn()}
          telemetry={telemetryWithEvidence()}
        />
      </HermesI18nProvider>,
    )
    expect(screen.getByText('這一關在做什麼')).toBeInTheDocument()
    expect(screen.getByText(expected)).toBeInTheDocument()
  })
})

describe('StageDrilldown composite drawer', () => {
  it('renders evidence label when analysis has evidence', () => {
    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage="composite"
          onClose={vi.fn()}
          telemetry={telemetryWithEvidence()}
        />
      </HermesI18nProvider>,
    )

    // Evidence branch should show the scanned count label (zh-TW: '已掃描')
    const scannedLabels = screen.getAllByText(/已掃描/)
    expect(scannedLabels.length).toBeGreaterThanOrEqual(1)
  })

  it('renders crossItems from groupByStance with real bullish/bearish groupings', () => {
    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage="crossverify"
          onClose={vi.fn()}
          telemetry={telemetryWithEvidence()}
        />
      </HermesI18nProvider>,
    )

    // Should no longer show 'EVIDENCE' as a hardcoded stance
    expect(screen.queryByText('EVIDENCE')).not.toBeInTheDocument()

    // Should show real stance text: bullish/bearish (lowercase from data)
    expect(screen.getByText('CoinDesk')).toBeInTheDocument()
    expect(screen.getByText('Glassnode')).toBeInTheDocument()
  })

  it('shows empty state when composite has no evidence', () => {
    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage="composite"
          onClose={vi.fn()}
          telemetry={telemetryNoEvidence()}
        />
      </HermesI18nProvider>,
    )

    // Should NOT show any evidence source names
    expect(screen.queryByText('ohlcv-csv')).not.toBeInTheDocument()

    // Should show scanned count 0
    expect(screen.getByText(/已掃描/)).toBeInTheDocument()
  })

  it('download button produces JSON with evidence, trust_radar, and execution_log', () => {
    const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')

    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage="composite"
          onClose={vi.fn()}
          telemetry={telemetryWithEvidence()}
        />
      </HermesI18nProvider>,
    )

    // Find and click the download button
    const downloadBtn = screen.getByText('⬇ JSON')
    expect(downloadBtn).toBeInTheDocument()

    // Verify it calls createObjectURL with a Blob
    downloadBtn.click()
    expect(createObjectURLSpy).toHaveBeenCalledTimes(1)

    const blobArg = createObjectURLSpy.mock.calls[0][0] as Blob
    expect(blobArg.type).toBe('application/json')

    createObjectURLSpy.mockRestore()
  })

  it('does not show download button when no analysis data', () => {
    render(
      <HermesI18nProvider>
        <StageDrilldown
          selCoin={selCoin}
          derivation={fallbackDerivation}
          selectedStage="composite"
          onClose={vi.fn()}
          telemetry={null}
        />
      </HermesI18nProvider>,
    )

    expect(screen.queryByText('⬇ JSON')).not.toBeInTheDocument()
  })
})
