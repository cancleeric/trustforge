// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SectorLayerCard from './SectorLayerCard'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import type { AssetContext } from '../lib/types'

function makeContext(overrides: Partial<AssetContext> = {}): AssetContext {
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
    ...overrides,
  }
}

describe('SectorLayerCard', () => {
  it('渲染 [Layer 2] badge、settlement_chain、gas_token、token_role、依賴清單與白話說明', () => {
    const { container } = render(<HermesI18nProvider><SectorLayerCard context={makeContext()} /></HermesI18nProvider>)
    expect(screen.getByText('[Layer 2]')).toBeInTheDocument()
    expect(screen.getAllByText('ethereum').length).toBeGreaterThan(0)
    expect(screen.getByText('ETH')).toBeInTheDocument()
    expect(screen.getByText('治理')).toBeInTheDocument()
    expect(screen.getByText('ethereum_settlement')).toBeInTheDocument()
    expect(screen.getByText('sequencer')).toBeInTheDocument()
    expect(screen.getByText('canonical_bridge')).toBeInTheDocument()
    expect(container.textContent).toMatch(/依附於 Ethereum/)
  })

  it('gas_token 與資產本身不同時標示手續費警示（並以 AnnotatedText 呈現 glossary 詞，可點出 ⚠️ risk_note）', () => {
    const { container } = render(<HermesI18nProvider><SectorLayerCard context={makeContext()} /></HermesI18nProvider>)
    expect(container.textContent).toMatch(/轉帳手續費\??需 ETH/)
    // 「手續費」為 gas_fee glossary 詞，需被 AnnotatedText 標註成可點的 GlossaryTerm
    expect(screen.getAllByText('手續費').length).toBeGreaterThan(0)
  })

  it('點擊「手續費」glossary 詞可展開 popover 並看到 ⚠️ risk_note（模組②可見）', () => {
    render(<HermesI18nProvider><SectorLayerCard context={makeContext()} /></HermesI18nProvider>)
    fireEvent.click(screen.getAllByText('手續費')[0])
    expect(screen.getByText(/A token usually cannot be used to pay its own transfer fees/)).toBeInTheDocument()
  })

  it('gas_token 等於資產本身時不顯示警示（不誤標）', () => {
    render(<HermesI18nProvider><SectorLayerCard context={makeContext({ symbol: 'ETH', gas_token: 'ETH' })} /></HermesI18nProvider>)
    expect(screen.queryByText(/轉帳手續費需/)).not.toBeInTheDocument()
  })

  it('symbol 與 gas_token 僅大小寫不同時不顯示警示（大小寫不敏感比對）', () => {
    render(<HermesI18nProvider><SectorLayerCard context={makeContext({ symbol: 'eth', gas_token: 'ETH' })} /></HermesI18nProvider>)
    expect(screen.queryByText(/轉帳手續費需/)).not.toBeInTheDocument()
  })

  it('未知欄位誠實顯示 unknown，不猜值', () => {
    render(
      <HermesI18nProvider>
        <SectorLayerCard
          context={makeContext({
            settlement_chain: undefined,
            gas_token: undefined,
            ecosystem: null,
          })}
        />
      </HermesI18nProvider>
    )
    expect(screen.getAllByText('unknown').length).toBeGreaterThan(0)
    expect(screen.queryByText(/依附於/)).not.toBeInTheDocument()
  })

  it('dependencies 為空陣列時顯示「無已知上下游關聯」而非空白區塊', () => {
    render(<HermesI18nProvider><SectorLayerCard context={makeContext({ dependencies: [] })} /></HermesI18nProvider>)
    expect(screen.getByText('無已知上下游關聯')).toBeInTheDocument()
  })
})
