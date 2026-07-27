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


function renderPage(initialUrl = '/asset-context') {
  return render(
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[initialUrl]}>
        <AssetContextLookupPage />
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

// 預設幣種改成比賽指定的 COIN_POOL[0]（BTC）後，預設載入的是 L1 卡片；
// ARB 保留作為「切換到另一個資產」的第二次查詢對象（範圍外 L2 範例）。
function btcContext(): AssetContext {
  return {
    schema_version: '1.0.0',
    asset_id: 'asset:btc',
    symbol: 'BTC',
    name: 'Bitcoin',
    sector: 'l1',
    layer: 'layer_1',
    token_role: 'gas',
    market_cap_tier: 'large',
    ecosystem: 'bitcoin',
    parent_asset_id: null,
    tags: ['pow', 'store_of_value'],
    settlement_chain: 'bitcoin',
    gas_token: 'BTC',
    dependencies: ['pow_consensus', 'mining_hashrate', 'utxo_ledger'],
  }
}

describe('AssetContextLookupPage', () => {
  it('預設查詢比賽幣種 BTC（不是範圍外的 ARB）並渲染 SectorLayerCard', async () => {
    vi.mocked(getAssetContext).mockResolvedValueOnce({ ok: true, data: { asset_context: btcContext() } })

    renderPage()

    expect(await screen.findByText('[Layer 1]')).toBeInTheDocument()
    expect(getAssetContext).toHaveBeenCalledWith('BTC', expect.anything())
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
      .mockResolvedValueOnce({ ok: true, data: { asset_context: btcContext() } })
      .mockResolvedValueOnce({ ok: true, data: { asset_context: null } })

    renderPage()

    expect(await screen.findByText('[Layer 1]')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('資產代號'), { target: { value: 'ARB' } })
    fireEvent.click(screen.getByRole('button', { name: '查詢' }))

    await waitFor(() => {
      expect(getAssetContext).toHaveBeenLastCalledWith('ARB', expect.anything())
    })
    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
  })

  it('切換查詢時不殘留上一個資產的卡片（新查詢未回來前先清空）', async () => {
    let resolveSecond: ((value: ApiEnvelope<AssetContextResponseData>) => void) | undefined
    vi.mocked(getAssetContext)
      .mockResolvedValueOnce({ ok: true, data: { asset_context: btcContext() } })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve
          }),
      )

    renderPage()

    expect(await screen.findByText('[Layer 1]')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('資產代號'), { target: { value: 'ARB' } })
    fireEvent.click(screen.getByRole('button', { name: '查詢' }))

    await waitFor(() => {
      expect(screen.queryByText('[Layer 1]')).not.toBeInTheDocument()
    })

    resolveSecond?.({ ok: true, data: { asset_context: null } })
    expect(await screen.findByText('目前無此資產的脈絡資料。')).toBeInTheDocument()
  })
})
