// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import InsightExplainabilityPanel from './InsightExplainabilityPanel'
import type { Insight } from '../lib/types'

function makeInsight(overrides: Partial<Insight> = {}): Insight {
  return {
    insight_type: 'smart_money_divergence',
    title: '聰明錢背離（鏈上吸籌 vs 價格下跌）',
    summary: 'BTC 價格下跌 3.3%，但成交量趨勢上升 +25%（鏈上吸籌代理訊號），呈聰明錢背離。',
    direction: 'bullish',
    strength: 0.42,
    coverage: 'covered',
    coverage_reason: '',
    contributions: [
      { source: 'ohlcv-csv', kind: 'price', claim_id: 'price-BTC-ret#0', text: 'BTC 報酬 -3.3%，呈下跌。', direction: 'bearish', trust: 0.9 },
      { source: 'ohlcv-csv', kind: 'price', claim_id: 'price-BTC-volume#0', text: 'BTC 成交量變化 +25%。', direction: 'bullish', trust: 0.9 },
    ],
    claim_ids: ['price-BTC-ret#0', 'price-BTC-volume#0'],
    ...overrides,
  }
}

describe('InsightExplainabilityPanel', () => {
  it('insights 為 undefined 時顯示 fallback 文字', () => {
    render(<InsightExplainabilityPanel insights={undefined} />)
    expect(screen.getByText('目前未偵測到非顯而易見、可驗證的獨特洞察。')).toBeInTheDocument()
  })

  it('covered 洞察顯示標題、強度、方向與兩個貢獻來源', () => {
    render(<InsightExplainabilityPanel insights={[makeInsight()]} />)
    expect(screen.getByText('聰明錢背離（鏈上吸籌 vs 價格下跌）')).toBeInTheDocument()
    expect(screen.getByText('洞察強度')).toBeInTheDocument()
    // 兩個貢獻來源皆來自 ohlcv-csv
    const sources = screen.getAllByText(/ohlcv-csv/)
    expect(sources.length).toBeGreaterThanOrEqual(2)
  })

  it('insufficient 洞察顯示「無法判定（樣本不足）」徽章，不顯示強度條', () => {
    render(
      <InsightExplainabilityPanel
        insights={[
          makeInsight({
            coverage: 'insufficient',
            direction: 'ambiguous',
            strength: 0.0,
            summary: 'BTC 價格下跌 3.3%，但缺少成交量趨勢訊號，無法判定是否伴隨鏈上吸籌。',
            coverage_reason: '缺少成交量趨勢事實，無法確認鏈上吸籌。',
            contributions: [
              { source: 'ohlcv-csv', kind: 'price', claim_id: 'price-BTC-ret#0', text: 'BTC 報酬 -3.3%。', direction: 'bearish', trust: 0.9 },
            ],
            claim_ids: ['price-BTC-ret#0'],
          }),
        ]}
      />
    )
    expect(screen.getByText('無法判定（樣本不足）')).toBeInTheDocument()
    expect(screen.queryByText('洞察強度')).not.toBeInTheDocument()
    expect(screen.getByText(/誠實閘：缺少成交量趨勢事實/)).toBeInTheDocument()
  })

  it('source_self_contradiction 洞察顯示「來源自我矛盾」徽章', () => {
    render(
      <InsightExplainabilityPanel
        insights={[
          makeInsight({
            insight_type: 'source_self_contradiction',
            title: '來源自我矛盾（不確定性信號）',
            summary: '來源 coindesk 同時出現看多與看空主張（1 則偏多 / 1 則偏空），構成自我矛盾。',
            direction: 'ambiguous',
            strength: 0.5,
            contributions: [
              { source: 'coindesk', kind: 'news', claim_id: 'coindesk-bullish#0', text: 'BTC 上看 70000', direction: 'bullish', trust: 0.6 },
              { source: 'coindesk', kind: 'news', claim_id: 'coindesk-bearish#0', text: 'BTC 恐跌至 50000', direction: 'bearish', trust: 0.6 },
            ],
            claim_ids: ['coindesk-bullish#0', 'coindesk-bearish#0'],
            meta: { source: 'coindesk', n_bullish: 1, n_bearish: 1 },
          }),
        ]}
      />
    )
    expect(screen.getByText('來源自我矛盾')).toBeInTheDocument()
  })

  it('covered 洞察顯示「數值溯源」深層回溯（meta 原始數值）', () => {
    render(
      <InsightExplainabilityPanel
        insights={[
          makeInsight({
            meta: { price_return_pct: -3.3, volume_trend_pct: 25, proxy_note: '代理說明' },
          }),
        ]}
      />
    )
    const summary = screen.getByText('數值溯源（深層回溯原始數值）')
    expect(summary).toBeInTheDocument()
    // 點開 details 後應出現 meta 原始數值
    summary.click()
    expect(screen.getByText(/price_return_pct：-3.3/)).toBeInTheDocument()
    expect(screen.getByText(/volume_trend_pct：25/)).toBeInTheDocument()
  })
})
