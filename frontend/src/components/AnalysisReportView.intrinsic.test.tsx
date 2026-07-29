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

// N71（CEO：「手動分析的報告要在那裡下載？執行過程的 LOG 要在那裡下載、在那裡看」）：
// 三顆下載鈕原本只長在 `HermesExecutionPanel`，而它被包在 `<details
// id="technical-analysis">`（預設收合）裡——跑完分析根本看不到。這條測試守的是
// 「不展開 details 也要看得見下載」。負向對照：把報告抬頭那排 `<ReportDownloads>`
// 拿掉再跑，下面的 expect 會 fail（見對話紀錄的 RED）。
describe('AnalysisReportView 報告下載可及性', () => {
  it('不用展開技術細節就看得到三個下載動作', () => {
    const { container } = renderReport()
    const details = container.querySelector('#technical-analysis') as HTMLDetailsElement
    expect(details.open).toBe(false)
    const row = container.querySelector("[aria-label='報告下載與執行紀錄']")
    expect(row).not.toBeNull()
    const labels = Array.from(row!.querySelectorAll('button')).map((b) => b.textContent)
    expect(labels).toEqual(['下載報告', '下載證據', '下載執行紀錄', '看執行過程'])
  })

  it('看執行過程會展開技術細節', () => {
    const { container } = renderReport()
    const details = container.querySelector('#technical-analysis') as HTMLDetailsElement
    details.scrollIntoView = () => {}
    const row = container.querySelector("[aria-label='報告下載與執行紀錄']")!
    const btn = Array.from(row.querySelectorAll('button')).find((b) => b.textContent === '看執行過程')!
    btn.click()
    expect(details.open).toBe(true)
  })
})

describe('AnalysisReportView intrinsic shadow integration', () => {
  it('keeps legacy reports isolated when the optional field is absent', () => {
    renderReport()
    expect(screen.queryByText(/SHADOW/)).not.toBeInTheDocument()
  })

  it('contains malformed optional payload and keeps the official report visible', () => {
    renderReport({ mode: 'shadow', affects_official_score: true })
    expect(screen.getByText('BTC')).toBeInTheDocument()
    expect(screen.getByText(/資產結構資料格式不相容/)).toBeInTheDocument()
    expect(screen.queryByText('已驗證')).not.toBeInTheDocument()
  })
})
