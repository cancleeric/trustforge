// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import { getComparisonSnapshot } from '../lib/endpoints'
import ComparePage from './ComparePage'

vi.mock('../lib/endpoints', () => ({
  getComparisonSnapshot: vi.fn(),
  registerAnalysisComparison: vi.fn(),
}))

vi.mock('../components/AnalysisReportView', () => ({
  default: ({ data, heading }: { data: { report: { asset_intrinsic_assessment?: unknown } }; heading: string }) => (
    <div aria-label={heading}>{JSON.stringify(data.report.asset_intrinsic_assessment)}</div>
  ),
}))

function renderPage(initialUrl = '/compare') {
  return render(
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[initialUrl]}>
        <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
          <ComparePage />
        </BridgeHologramProvider>
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

/** 基本 comparison_report payload（normal dimensions）。 */
function baseReportPayload() {
  return {
    ok: true as const,
    data: {
      version: 'test',
      report_a: { coin: 'BTC', calibrated_confidence: 0.5 },
      report_b: { coin: 'BNB', calibrated_confidence: 0.5 },
      evidence_a: [], evidence_b: [],
      trust_components_aggregate_a: { reputation: null, corroboration: null, recency: null, manipulation: null },
      trust_components_aggregate_b: { reputation: null, corroboration: null, recency: null, manipulation: null },
      trust_radar_a: {}, trust_radar_b: {}, price_provenance_a: {}, price_provenance_b: {},
      execution_log: [],
      comparison_report: {
        coin_a: 'BTC',
        coin_b: 'BNB',
        query: 'test',
        conclusion: 'BTC 整體優於 BNB',
        dimensions: [
          { dimension: '價格動能', decision: 'normal', reasoning: 'BTC 動能較強', confidence: 0.85, evidence_refs: [] },
          { dimension: '鏈上活動', decision: 'insufficient', reasoning: 'BNB 資料不足', confidence: 0.3, evidence_refs: [] },
        ],
        confidence: 0.72,
        limits: ['樣本有限'],
        could_flip: ['若 BNB 推出重大升級'],
        generated_at: '2026-07-28T00:00:00Z',
      },
    },
  }
}

describe('ComparePage · 同層 Peer 比較入口（模組③ Wave 3）', () => {
  it('提供連到獨立 /peer-metrics 頁的連結，不把 Peer 比較掛在 COIN_POOL 雙幣表單上', () => {
    renderPage()
    const link = screen.getByRole('link', { name: /查看同層 Peer 比較/ })
    expect(link).toHaveAttribute('href', '/peer-metrics')
  })

  it('passes each independent intrinsic assessment to its matching comparison report', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue({
      ok: true,
      data: {
        version: 'test',
        report_a: { coin: 'BTC', calibrated_confidence: 0.5, asset_intrinsic_assessment: { asset_id: 'asset:btc' } },
        report_b: { coin: 'BNB', calibrated_confidence: 0.5, asset_intrinsic_assessment: { asset_id: 'asset:bnb' } },
        evidence_a: [], evidence_b: [],
        trust_components_aggregate_a: { reputation: null, corroboration: null, recency: null, manipulation: null },
        trust_components_aggregate_b: { reputation: null, corroboration: null, recency: null, manipulation: null },
        trust_radar_a: {}, trust_radar_b: {}, price_provenance_a: {}, price_provenance_b: {},
        execution_log: [],
      },
    } as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByLabelText('幣種 A · BTC')).toHaveTextContent('asset:btc'))
    expect(screen.getByLabelText('幣種 B · BNB')).toHaveTextContent('asset:bnb')
  })

  it('顯示 ComparisonReportView 當 comparison_report 存在，並保留摺疊的雙幣詳細分析', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue(baseReportPayload() as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByTestId('comparison-report-view')).toBeInTheDocument())
    expect(screen.getByTestId('comparison-report-view')).toHaveTextContent('BTC 整體優於 BNB')
    expect(screen.getByText('查看各幣詳細分析')).toBeInTheDocument()
    expect(screen.getByLabelText('幣種 A · BTC')).toBeInTheDocument()
    expect(screen.getByLabelText('幣種 B · BNB')).toBeInTheDocument()
  })

  // ── CA-07 補測：loading / partial / error / desktop / mobile ─────────────────

  it('test_comparison_report_loading_state：載入中顯示 skeleton', () => {
    // Promise 不 resolve，讓 loading 保持 true
    vi.mocked(getComparisonSnapshot).mockImplementation(() => new Promise<never>(() => {}))
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    // loading 狀態會同步渲染 LoadingState（setLoading(true) 在 effect 內）
    expect(screen.getByRole('status')).toHaveTextContent(/讀取/)
  })

  it('test_comparison_report_partial_state：部分 dimension 為 abstain，abstain cards 正確顯示', async () => {
    const payload = { ...baseReportPayload() }
    ;(payload.data.comparison_report as Record<string, unknown>).dimensions = [
      { dimension: '價格動能', decision: 'normal', reasoning: '動能強', confidence: 0.85, evidence_refs: [] },
      { dimension: '鏈上活動', decision: 'abstain', reasoning: '無可用資料', confidence: 0.0, evidence_refs: [], abstain_reason: 'BSC 節點無回應' },
      { dimension: '社群熱度', decision: 'insufficient', reasoning: '社群資料不足', confidence: 0.3, evidence_refs: [] },
      { dimension: '監管風險', decision: 'abstain', reasoning: '無法評估', confidence: 0.0, evidence_refs: [], abstain_reason: '管轄區無資料' },
    ]
    vi.mocked(getComparisonSnapshot).mockResolvedValue(payload as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByTestId('comparison-report-view')).toBeInTheDocument())

    // abstain cards 有 — 棄權 label
    expect(screen.getAllByText('— 棄權')).toHaveLength(2)
    // abstain_reason 顯示
    expect(screen.getByText('BSC 節點無回應')).toBeInTheDocument()
    expect(screen.getByText('管轄區無資料')).toBeInTheDocument()
    // normal / insufficient cards 也同時存在
    expect(screen.getByText('✅ 可判定')).toBeInTheDocument()
    expect(screen.getByText('⚠️ 資訊不足')).toBeInTheDocument()
    // 結論仍顯示
    expect(screen.getByTestId('comparison-report-view')).toHaveTextContent('BTC 整體優於 BNB')
  })

  it('test_comparison_report_partial_state：全 abstain 時顯示棄權提示', async () => {
    const payload = { ...baseReportPayload() }
    ;(payload.data.comparison_report as Record<string, unknown>).dimensions = [
      { dimension: '價格動能', decision: 'abstain', reasoning: '無資料', confidence: 0.0, evidence_refs: [] },
      { dimension: '鏈上活動', decision: 'abstain', reasoning: '無資料', confidence: 0.0, evidence_refs: [] },
    ]
    vi.mocked(getComparisonSnapshot).mockResolvedValue(payload as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByTestId('comparison-report-view')).toBeInTheDocument())

    // 全 abstain 時顯示棄權提示
    expect(screen.getByText('所有面向均棄權')).toBeInTheDocument()
    expect(screen.getByText('無法進行有效比較')).toBeInTheDocument()
  })

  it('test_comparison_report_error_state：error 時顯示 ErrorState', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue({
      ok: false,
      error: { code: 'analysis_failed', message: 'An unexpected error occurred' },
    } as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    // ErrorState 顯示 error code（在 aria-hidden 的 code 行中）
    expect(screen.getByText('analysis_failed')).toBeInTheDocument()
  })

  it('test_comparison_report_desktop_layout：desktop 渲染正常不 crash', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue(baseReportPayload() as never)
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByTestId('comparison-report-view')).toBeInTheDocument())

    // 正常渲染：結論、維度卡片、limits、could_flip 都存在
    expect(screen.getByText('綜合比較結論')).toBeInTheDocument()
    expect(screen.getByText('面向分析')).toBeInTheDocument()
    expect(screen.getByText('價格動能')).toBeInTheDocument()
    expect(screen.getByText('鏈上活動')).toBeInTheDocument()
    expect(screen.getByText('已知限制（1）')).toBeInTheDocument()
    expect(screen.getByText('可能推翻結論的條件（1）')).toBeInTheDocument()
    // 桌面 responsive grid 具有 sm:grid-cols-2
    const grid = document.querySelector('.grid.sm\\:grid-cols-2')
    expect(grid).not.toBeNull()
  })

  it('test_comparison_report_mobile_responsive：mobile viewport 不 overflow', async () => {
    vi.mocked(getComparisonSnapshot).mockResolvedValue(baseReportPayload() as never)
    const originalWidth = window.innerWidth
    window.innerWidth = 375
    renderPage('/compare?coin=BTC&coin2=BNB&q=test')
    await waitFor(() => expect(screen.getByTestId('comparison-report-view')).toBeInTheDocument())

    // 確認 ComparisonReportView root 存在
    const root = screen.getByTestId('comparison-report-view')
    expect(root).toBeInTheDocument()
    // mobile viewport 下 grid 有 grid-cols-1 基礎 class
    const grid = root.querySelector('.grid.grid-cols-1')
    expect(grid).not.toBeNull()

    // cleanup: 恢復 window size
    window.innerWidth = originalWidth
  })
})
