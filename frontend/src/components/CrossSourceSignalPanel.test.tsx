import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import CrossSourceSignalPanel from './CrossSourceSignalPanel'
import type { CrossSourceSignal } from '../lib/types'

// issue #21（CISO-LOW）：情緒類（news/social）訊號僅有 1 個獨立來源時，
// 顯示「單一來源主導」透明徽章；多源時不顯示。核心信任分數計算不在本檔
// 測試範圍（見 tests/test_cross_source_signal.py 後端測試），這裡只驗證
// UI 依 `sentiment_source_count` 正確顯示/隱藏徽章。

function makeSignal(overrides: Partial<CrossSourceSignal> = {}): CrossSourceSignal {
  return {
    type: 'consensus',
    summary: '客觀與情緒同向偏多，訊號一致。',
    objective_direction: 'bullish',
    sentiment_direction: 'bullish',
    ...overrides,
  }
}

const BADGE_TEXT = '單一來源主導'

describe('CrossSourceSignalPanel', () => {
  it('signal 為 null 時顯示 fallback 文字，不顯示單源徽章', () => {
    render(<CrossSourceSignalPanel signal={null} />)
    expect(
      screen.getByText('目前未偵測到同議題、跨源、語意矛盾的顯著訊號。')
    ).toBeInTheDocument()
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('sentiment_source_count === 1（共識）時應顯示「單一來源主導」徽章', () => {
    const signal = makeSignal({ type: 'consensus', sentiment_source_count: 1 })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(BADGE_TEXT)).toBeInTheDocument()
    expect(screen.getByText(signal.summary)).toBeInTheDocument()
  })

  it('sentiment_source_count === 1（背離）時應顯示「單一來源主導」徽章', () => {
    const signal = makeSignal({
      type: 'divergence',
      summary: '客觀數據偏多、情緒類偏空，呈背離，建議交叉驗證、留意轉折。',
      sentiment_direction: 'bearish',
      sentiment_source_count: 1,
    })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(BADGE_TEXT)).toBeInTheDocument()
  })

  it('sentiment_source_count === 2（多源）時不應顯示徽章', () => {
    const signal = makeSignal({ sentiment_source_count: 2 })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('sentiment_source_count 缺欄位（如 _stance_pair_signal 備援分支/舊快照）時不應顯示徽章', () => {
    const signal = makeSignal({ sentiment_source_count: undefined })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.queryByText(BADGE_TEXT)).not.toBeInTheDocument()
  })

  it('徽章不影響既有 summary / claim ids 渲染', () => {
    const signal = makeSignal({
      sentiment_source_count: 1,
      supporting_claim_ids: ['c1', 'c2'],
    })
    render(<CrossSourceSignalPanel signal={signal} />)
    expect(screen.getByText(/佐證 claim_ids：c1、c2/)).toBeInTheDocument()
  })
})
