// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import type { AnalyzeData } from '../lib/types'
import AnalysisReportView from './AnalysisReportView'

vi.mock('./TrustTrendSection', () => ({ default: () => null }))
vi.mock('./TrustRadarChart', () => ({ default: () => <div>radar-summary</div> }))
vi.mock('./EvidenceDistributionCharts', () => ({ default: () => <div>evidence-charts</div> }))

function makeData(): AnalyzeData {
  return {
    version: 'test',
    report: {
      coin: 'BTC', question_type: 'risk', question: 'test', market_judgment: '當前市場偏向震盪',
      facts: [], inferences: [], key_basis: [], confidence: 0.6, limits: [], could_flip: [],
      contrarian: [], generated_at: '2026-08-01T00:00:00Z', direction: 'neutral',
      cross_source_signal: null, calibrated_confidence: 0.6, decision_state: 'normal',
    },
    evidence: [{ source: 'coindesk', fetched_at: '2026-08-01T00:00:00Z', content_reference: 'summary', related_claim: 'claim', source_url: '', kind: 'news', trust: 0.7, trust_components: {}, flags: [], info_flags: [] }],
    trust_radar: {},
    trust_components_aggregate: { reputation: null, corroboration: null, recency: null, manipulation: null },
    price_provenance: {},
    execution_log: [],
  }
}

function renderWithCompact(compact: boolean) {
  return render(
    <HermesI18nProvider>
      <MemoryRouter>
        <AnalysisReportView data={makeData()} heading="幣種 A · BTC" compact={compact} />
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

describe('AnalysisReportView compact 模式（比較頁）', () => {
  it('compact=true 時標題用 text-base 而非 text-xl', () => {
    const { container } = renderWithCompact(true)
    const h2 = container.querySelector('h2')!
    expect(h2.className).toContain('text-base')
    expect(h2.className).not.toContain('text-xl')
  })

  it('compact=false 時標題用 text-xl', () => {
    const { container } = renderWithCompact(false)
    const h2 = container.querySelector('h2')!
    expect(h2.className).toContain('text-xl')
    expect(h2.className).not.toContain('text-base')
  })

  it('compact=true 時信心儀表 grid 不含 xl:grid-cols 類別', () => {
    const { container } = renderWithCompact(true)
    // 找到包含 ConfidenceGauge 的 grid div（第一個 grid-cols-1 且直接子元素有 svg 或 canvas）
    const grids = Array.from(container.querySelectorAll('.grid.grid-cols-1'))
    const gaugeGrid = grids.find((el) => el.querySelector('[aria-label]'))
    expect(gaugeGrid).toBeTruthy()
    expect(gaugeGrid!.className).not.toContain('xl:grid-cols')
  })

  it('compact=false 時信心儀表 grid 包含 xl:grid-cols', () => {
    const { container } = renderWithCompact(false)
    const grids = Array.from(container.querySelectorAll('.grid.grid-cols-1'))
    const gaugeGrid = grids.find((el) => el.className.includes('xl:grid-cols'))
    expect(gaugeGrid).toBeTruthy()
  })

  it('keeps the radar in the collapsed L3 trust tab', () => {
    const { container } = renderWithCompact(false)
    expect(screen.getByText('radar-summary').closest('#technical-analysis')).not.toBeNull()
    expect((container.querySelector('#technical-analysis') as HTMLDetailsElement).open).toBe(false)
  })

  it('shows the four-layer dashboard labels', () => {
    renderWithCompact(false)
    expect(screen.getByText(/L1 · Executive Summary/)).toBeInTheDocument()
    expect(screen.getByText(/L2 · 正反證據對照/)).toBeInTheDocument()
    expect(screen.getByText(/L3 · 深度面板/)).toBeInTheDocument()
    expect(screen.getByText(/L4 ·/)).toBeInTheDocument()
  })

  it('shows relative freshness and evidence-kind counts in L1', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-01T00:07:00Z'))
    try {
      renderWithCompact(false)
      expect(screen.getByText('更新於 7 分鐘前')).toBeInTheDocument()
      expect(screen.getByText('news 1')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not mount lazy evidence charts until the evidence details are opened', async () => {
    const { container } = renderWithCompact(false)
    const details = container.querySelector('#evidence-list') as HTMLDetailsElement
    expect(screen.queryByText('evidence-charts')).toBeNull()
    details.open = true
    fireEvent(details, new Event('toggle'))
    expect(await screen.findByText('evidence-charts')).toBeInTheDocument()
  })
})
