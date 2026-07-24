// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiEnvelope, EcoLinkResponseData } from '../lib/types'
import { getEcoLink } from '../lib/endpoints'
import EcoLinkPage from './EcoLinkPage'

vi.mock('../lib/endpoints', () => ({
  getEcoLink: vi.fn(),
}))

function renderPage(initialUrl = '/eco-link') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <EcoLinkPage />
    </MemoryRouter>,
  )
}

describe('EcoLinkPage', () => {
  it('預設查詢 asset:arb，verdict: possible_relation 時渲染影響路徑面板', async () => {
    const response: ApiEnvelope<EcoLinkResponseData> = {
      ok: true,
      data: {
        verdict: 'possible_relation',
        message: 'asset:arb 與 asset:eth 可能相關',
        impact_paths: [
          {
            event_id: 'upgrade:arb:stylus',
            path: ['asset:arb', 'asset:eth'],
            direction: 'mixed',
            confidence: 0.85,
            official_source_url: 'https://arbitrum.foundation/upgrade/stylus',
          },
        ],
      },
    }
    vi.mocked(getEcoLink).mockResolvedValueOnce(response)
    renderPage()
    expect(getEcoLink).toHaveBeenCalledWith('asset:arb', expect.anything())
    expect(await screen.findByText('asset:arb → asset:eth')).toBeInTheDocument()
  })

  it('verdict: insufficient_data 時顯示「資料不足，無法判定」', async () => {
    vi.mocked(getEcoLink).mockResolvedValueOnce({
      ok: true,
      data: { verdict: 'insufficient_data', message: '資料不足，無法判定', impact_paths: [] },
    })
    renderPage('/eco-link?asset=asset:op')
    expect(await screen.findByText('資料不足，無法判定。')).toBeInTheDocument()
  })

  it('API 錯誤時顯示 ErrorState', async () => {
    vi.mocked(getEcoLink).mockResolvedValueOnce({
      ok: false,
      error: { code: 'network_error', message: '連線異常，請稍後再試' },
    })
    renderPage()
    expect(await screen.findByText('連線異常')).toBeInTheDocument()
  })

  it('點快速建議可切換查詢資產並帶入 URL', async () => {
    vi.mocked(getEcoLink).mockResolvedValue({
      ok: true,
      data: { verdict: 'insufficient_data', message: '資料不足，無法判定', impact_paths: [] },
    })
    renderPage()
    await screen.findByText('資料不足，無法判定。')
    fireEvent.click(screen.getByRole('button', { name: 'asset:op' }))
    expect(getEcoLink).toHaveBeenCalledWith('asset:op', expect.anything())
  })
})
