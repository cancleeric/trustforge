// @vitest-environment jsdom
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiEnvelope, PeerMetricsResponseData } from '../lib/types'
import { getPeerMetrics } from '../lib/endpoints'
import { BridgeHologramProvider } from '../components/BridgeHologramContext'
import ComparePage from './ComparePage'

vi.mock('../lib/endpoints', () => ({
  getComparisonSnapshot: vi.fn(),
  registerAnalysisComparison: vi.fn(),
  getPeerMetrics: vi.fn(),
}))

function renderPage(initialUrl = '/compare') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <BridgeHologramProvider value={{ data: null, setData: vi.fn() }}>
        <ComparePage />
      </BridgeHologramProvider>
    </MemoryRouter>,
  )
}

describe('ComparePage · Peer 同層比較（模組③ Wave 3）', () => {
  it('依「幣種 A」查詢 peer-metrics，並渲染同層比較表', async () => {
    const response: ApiEnvelope<PeerMetricsResponseData> = {
      ok: true,
      data: {
        snapshot: {
          asset_id: 'asset:btc',
          observed_tps: { value: 7, unit: 'count/s', method: 'observed', source: 'fixture' },
        },
        peers: [
          {
            snapshot: { asset_id: 'asset:eth', observed_tps: { value: 14.2, unit: 'count/s', method: 'observed', source: 'fixture' } },
            comparable: true,
            reason: null,
          },
        ],
      },
    }
    vi.mocked(getPeerMetrics).mockResolvedValueOnce(response)
    renderPage('/compare?coin=BTC&coin2=ETH')
    expect(getPeerMetrics).toHaveBeenCalledWith('asset:btc', expect.anything())
    expect(await screen.findByText('同層比較')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/asset:eth/).length).toBeGreaterThan(0))
  })

  it('查無資料時（snapshot: null）顯示空狀態，不視為錯誤', async () => {
    vi.mocked(getPeerMetrics).mockResolvedValueOnce({ ok: true, data: { snapshot: null, peers: [] } })
    renderPage('/compare?coin=BTC&coin2=ETH')
    expect(await screen.findByText(/目前無 asset:btc 的同層比較資料。/)).toBeInTheDocument()
  })
})
