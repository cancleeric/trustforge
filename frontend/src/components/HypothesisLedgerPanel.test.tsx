// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import HypothesisLedgerPanel from './HypothesisLedgerPanel'
import type { Evidence, HypothesisLedger } from '../lib/types'

const evidence: Evidence[] = [
  { source: 'coindesk', kind: 'news', fetched_at: '', content_reference: 'BTC 上看 70000', related_claim: '', source_url: '', trust: 0.7, trust_components: {}, flags: [], info_flags: [] },
  { source: 'x-anon', kind: 'social', fetched_at: '', content_reference: 'BTC 恐跌至 50000', related_claim: '', source_url: '', trust: 0.4, trust_components: {}, flags: [], info_flags: [] },
]

const ledger: HypothesisLedger = {
  pro: [0],
  con: [1],
  confidence_limit: '本驗證為「假設對照」而非預測，不宣稱預測力。',
}

describe('HypothesisLedgerPanel', () => {
  it('ledger 為 null/undefined 時不渲染', () => {
    const { container } = render(<HypothesisLedgerPanel ledger={null} evidence={evidence} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('渲染支持方/反方索引與對應證據，並顯示信心限制', () => {
    render(<HypothesisLedgerPanel ledger={ledger} evidence={evidence} />)
    expect(screen.getByText('假設驗證：正反方證據對照')).toBeInTheDocument()
    expect(screen.getByText(/支持方（pro）· 1 筆/)).toBeInTheDocument()
    expect(screen.getByText(/反方（con）· 1 筆/)).toBeInTheDocument()
    // 證據清單回溯：E0 / E1 與其 content_reference 都應出現
    expect(screen.getByText(/E0/)).toBeInTheDocument()
    expect(screen.getByText(/BTC 上看 70000/)).toBeInTheDocument()
    expect(screen.getByText(/BTC 恐跌至 50000/)).toBeInTheDocument()
    expect(screen.getByText(/不宣稱預測力/)).toBeInTheDocument()
  })
})
