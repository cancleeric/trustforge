import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import EvidenceTrailPanel from './EvidenceTrailPanel'
import type { CrossSourceSignal, Evidence } from '../lib/types'

function makeEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    source: 'coindesk',
    fetched_at: '2026-07-04T00:00:00Z',
    content_reference: 'ref',
    related_claim: 'claim',
    source_url: '',
    kind: 'news',
    trust: 0.8,
    trust_components: {},
    flags: [],
    info_flags: [],
    ...overrides,
  }
}

describe('EvidenceTrailPanel — 真實欄位聚合（#171 第 2 項）', () => {
  it('各計數與傳入 evidence 一致（含 flags / info_flags）', () => {
    const evidence: Evidence[] = [
      makeEvidence({ source: 'coindesk', flags: ['喊單'], info_flags: ['高度相似'] }),
      makeEvidence({ source: 'coindesk', flags: ['穩賺'] }),
      makeEvidence({ source: 'coingecko-price', info_flags: ['相似簇'] }),
      makeEvidence({ source: 'reddit-bitcoin' }),
      makeEvidence({ source: 'reddit-bitcoin' }),
    ]
    render(<EvidenceTrailPanel evidence={evidence} signal={null} />)

    // 證據總筆數 = 5
    expect(screen.getByText('證據總筆數').parentElement?.querySelector('.tf-num')?.textContent).toBe('5')
    // 獨立來源數：coindesk / coingecko-price / reddit-bitcoin = 3（raw set，同後端語意）
    expect(screen.getByText('獨立來源數').parentElement?.querySelector('.tf-num')?.textContent).toBe('3')
    // 操縱紅旗筆數 = 2（coindesk 兩筆各帶 flags）
    expect(screen.getByText('操縱紅旗筆數').parentElement?.querySelector('.tf-num')?.textContent).toBe('2')
    // 中性相似提示筆數 = 2（coindesk 1 筆 + coingecko-price 1 筆帶 info_flags）
    expect(screen.getByText('中性相似提示筆數').parentElement?.querySelector('.tf-num')?.textContent).toBe('2')
  })

  it('cross_source_signal=null 時渲染「無法判定」，不補 0（防造假回歸）', () => {
    const evidence: Evidence[] = [makeEvidence(), makeEvidence({ source: 'coingecko-price' })]
    render(<EvidenceTrailPanel evidence={evidence} signal={null} />)
    expect(screen.getByText('無法判定')).toBeInTheDocument()
    // 跨源訊號卡片的值必須是「無法判定」，而不是數字 0
    const label = screen.getByText('跨源訊號')
    const value = label.parentElement?.querySelector('.tf-num')?.textContent
    expect(value).toBe('無法判定')
  })

  it('consensus 顯示「多源共識」', () => {
    const signal: CrossSourceSignal = {
      type: 'consensus',
      summary: '客觀與情緒同向偏多，訊號一致。',
      objective_direction: 'bullish',
      sentiment_direction: 'bullish',
    }
    render(<EvidenceTrailPanel evidence={[makeEvidence()]} signal={signal} />)
    expect(screen.getByText('多源共識')).toBeInTheDocument()
  })

  it('divergence 顯示「訊號背離」並列出去重後的偏多/偏空來源數', () => {
    const signal: CrossSourceSignal = {
      type: 'divergence',
      summary: '客觀偏多、情緒偏空，呈背離。',
      objective_direction: 'bullish',
      sentiment_direction: 'bearish',
      distinct_sources: {
        bullish: [{ source: 'coindesk', stance: 'bullish' }, { source: 'theblock', stance: 'bullish' }],
        bearish: [{ source: 'reddit-bitcoin', stance: 'bearish' }],
      },
    }
    render(<EvidenceTrailPanel evidence={[makeEvidence()]} signal={signal} />)
    expect(screen.getByText('訊號背離')).toBeInTheDocument()
    expect(screen.getByText(/偏多 2 來源 ／ 偏空 1 來源（去重）/)).toBeInTheDocument()
  })
})
