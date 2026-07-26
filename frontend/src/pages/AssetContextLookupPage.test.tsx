// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiEnvelope, AssetContext, AssetContextResponseData } from '../lib/types'
import { getAssetContext } from '../lib/endpoints'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import AssetContextLookupPage from './AssetContextLookupPage'

vi.mock('../lib/endpoints', () => ({
  getAssetContext: vi.fn(),
}))

function arbContext(): AssetContext {
  return {
    schema_version: '1.0.0',
    asset_id: 'asset:arb',
    symbol: 'ARB',
    name: 'Arbitrum',
    sector: 'l2',
    layer: 'layer_2',
    token_role: 'governance',
    market_cap_tier: 'large',
    ecosystem: 'ethereum',
    parent_asset_id: 'asset:eth',
    tags: ['rollup', 'optimistic'],
    settlement_chain: 'ethereum',
    gas_token: 'ETH',
    dependencies: ['ethereum_settlement', 'sequencer', 'canonical_bridge'],
  }
}

function renderPage(initialUrl = '/asset-context') {
  return render(
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[initialUrl]}>
        <AssetContextLookupPage />
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

describe('AssetContextLookupPage', () => {
  it('預設查詢 ARB 並渲染 SectorLayerCard', async () => {
    vi.mocked(getAssetContext).mockResolvedValueOnce({ ok: true, data: { asset_context: arbContext() } })

    renderPage()

    expect(await screen.findByText('[Layer 2]')).toBeInTheDocument()
    expect(getAssetContext).toHaveBeenCalledWith('ARB', expect.anything())
  })

  it('查無資料時顯示空狀態文案，不報錯不空白', async () => {
    vi.mocked(getAssetContext).mockResolvedValueOnce({ ok: true, data: { asset_context: null } })

    renderPage('/asset-context?symbol=BTC')

    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
  })

  it('API 打不通時顯示錯誤狀態而非白畫面', async () => {
    vi.mocked(getAssetContext).mockResolvedValueOnce({
      ok: false,
      error: { code: 'network_error', message: '連線異常' },
    })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('連線異常')
  })

  it('輸入資產代號送出後會查詢並更新卡片', async () => {
    vi.mocked(getAssetContext)
      .mockResolvedValueOnce({ ok: true, data: { asset_context: arbContext() } })
      .mockResolvedValueOnce({ ok: true, data: { asset_context: null } })

    renderPage()

    expect(await screen.findByText('[Layer 2]')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('資產代號'), { target: { value: 'BTC' } })
    fireEvent.click(screen.getByRole('button', { name: '查詢' }))

    await waitFor(() => {
      expect(getAssetContext).toHaveBeenLastCalledWith('BTC', expect.anything())
    })
    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
  })

  it('切換查詢時不殘留上一個資產的卡片（新查詢未回來前先清空）', async () => {
    let resolveSecond: ((value: ApiEnvelope<AssetContextResponseData>) => void) | undefined
    vi.mocked(getAssetContext)
      .mockResolvedValueOnce({ ok: true, data: { asset_context: arbContext() } })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve
          }),
      )

    renderPage()

    expect(await screen.findByText('[Layer 2]')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('資產代號'), { target: { value: 'BTC' } })
    fireEvent.click(screen.getByRole('button', { name: '查詢' }))

    await waitFor(() => {
      expect(screen.queryByText('[Layer 2]')).not.toBeInTheDocument()
    })

    resolveSecond?.({ ok: true, data: { asset_context: null } })
    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
  })
})
