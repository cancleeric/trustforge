// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import type { AnalyzeData } from '../lib/types'
import AnalysisReportView from './AnalysisReportView'

vi.mock('./TrustTrendSection', () => ({ default: () => null }))
vi.mock('./TrustRadarChart', () => ({ default: () => null }))

function reportData(assessment?: unknown): AnalyzeData {
  return {
    version: 'test',
    report: {
      coin: 'BTC', question_type: 'risk', question: 'test', market_judgment: 'neutral',
      facts: [], inferences: [], key_basis: [], confidence: 0.5, limits: [], could_flip: [],
      contrarian: [], generated_at: '2026-07-28T00:00:00Z', direction: 'neutral',
      cross_source_signal: null, calibrated_confidence: 0.5, decision_state: 'normal',
      asset_intrinsic_assessment: assessment,
    },
    evidence: [],
    trust_radar: {},
    trust_components_aggregate: { reputation: null, corroboration: null, recency: null, manipulation: null },
    price_provenance: {},
    execution_log: [],
  }
}

function renderReport(assessment?: unknown) {
  return render(
    <HermesI18nProvider>
      <MemoryRouter>
        <AnalysisReportView data={reportData(assessment)} />
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

describe('AnalysisReportView intrinsic shadow integration', () => {
  it('keeps legacy reports isolated when the optional field is absent', () => {
    renderReport()
    expect(screen.queryByText(/SHADOW/)).not.toBeInTheDocument()
  })

  it('contains malformed optional payload and keeps the official report visible', () => {
    renderReport({ mode: 'shadow', affects_official_score: true })
    expect(screen.getByText('BTC')).toBeInTheDocument()
    expect(screen.getByText(/Shadow 資料格式不相容/)).toBeInTheDocument()
    expect(screen.queryByText('已驗證')).not.toBeInTheDocument()
  })
})
