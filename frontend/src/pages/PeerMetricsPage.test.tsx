// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiEnvelope, PeerMetricsResponseData } from '../lib/types'
import { getPeerMetrics } from '../lib/endpoints'
import PeerMetricsPage from './PeerMetricsPage'

vi.mock('../lib/endpoints', () => ({
  getPeerMetrics: vi.fn(),
}))

function renderPage(initialUrl = '/peer-metrics') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <PeerMetricsPage />
    </MemoryRouter>,
  )
}

const metric = (value: number | null) => ({ value, unit: 'count/s', method: 'observed', source: 'fixture' })

describe('PeerMetricsPage', () => {
  it('預設查詢 asset:arb（獨立於 COIN_POOL 五幣選單，L2 資產也查得到），渲染同層比較表', async () => {
    const response: ApiEnvelope<PeerMetricsResponseData> = {
      ok: true,
      data: {
        illustrative: true,
        snapshot: { asset_id: 'asset:arb', observed_tps: metric(18.5) },
        peers: [
          { asset_id: 'asset:op', snapshot: { asset_id: 'asset:op', observed_tps: metric(15.9) }, comparable: true, reason: null },
        ],
      },
    }
    vi.mocked(getPeerMetrics).mockResolvedValueOnce(response)
    renderPage()
    expect(getPeerMetrics).toHaveBeenCalledWith('asset:arb', expect.anything())
    expect(await screen.findByText('同層比較')).toBeInTheDocument()
    expect(screen.getAllByText(/asset:op/).length).toBeGreaterThan(0)
  })

  it('查無資料時（snapshot: null）顯示空狀態，不視為錯誤', async () => {
    vi.mocked(getPeerMetrics).mockResolvedValueOnce({
      ok: true,
      data: { illustrative: true, snapshot: null, peers: [] },
    })
    renderPage('/peer-metrics?asset=asset:unknown')
    expect(await screen.findByText(/目前無 asset:unknown 的同層比較資料。/)).toBeInTheDocument()
  })

  it('可用輸入框查詢任意資產識別碼（包含 COIN_POOL 涵蓋不到的 L2 資產）', async () => {
    vi.mocked(getPeerMetrics).mockResolvedValue({
      ok: true,
      data: { illustrative: true, snapshot: null, peers: [] },
    })
    renderPage()
    await screen.findByText(/目前無 asset:arb 的同層比較資料。/)
    fireEvent.change(screen.getByLabelText('資產識別碼'), { target: { value: 'asset:matic' } })
    fireEvent.click(screen.getByRole('button', { name: '查詢' }))
    expect(getPeerMetrics).toHaveBeenCalledWith('asset:matic', expect.anything())
  })

  it('API 錯誤時顯示 ErrorState', async () => {
    vi.mocked(getPeerMetrics).mockResolvedValueOnce({
      ok: false,
      error: { code: 'network_error', message: '連線異常，請稍後再試' },
    })
    renderPage()
    expect(await screen.findByText('連線異常')).toBeInTheDocument()
  })
})
