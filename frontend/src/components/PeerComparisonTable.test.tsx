// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import PeerComparisonTable from './PeerComparisonTable'
import type { PeerComparisonEntry, PeerMetricsSnapshot } from '../lib/types'

function metric(value: number | null, unit = 'count/s'): { value: number | null; unit: string; method: string; source: string } {
  return { value, unit, method: 'observed', source: 'fixture://peer-metrics/l2' }
}

function makeSnapshot(overrides: Partial<PeerMetricsSnapshot> = {}): PeerMetricsSnapshot {
  return {
    asset_id: 'asset:arb',
    observed_tps: metric(18.5),
    tvl: metric(2_500_000_000, 'usd'),
    gas_fee: metric(0.03, 'usd/transfer'),
    activity_breakdown: { swap: metric(11.2), bridge: metric(3.4) },
    window_start: '2026-01-01T00:00:00+00:00',
    window_end: '2026-01-08T00:00:00+00:00',
    observed_at: '2026-01-08T00:00:00+00:00',
    ...overrides,
  }
}

describe('PeerComparisonTable', () => {
  it('渲染本體 snapshot 與可比較 peer 的 TPS/TVL/Gas/活躍度並列', () => {
    const peers: PeerComparisonEntry[] = [
      {
        asset_id: 'asset:op',
        snapshot: makeSnapshot({ asset_id: 'asset:op', observed_tps: metric(15.9) }),
        comparable: true,
        reason: null,
      },
    ]
    render(<PeerComparisonTable snapshot={makeSnapshot()} peers={peers} />)
    expect(screen.getAllByText(/asset:arb/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/asset:op/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/18.50 count\/s/).length).toBeGreaterThan(0)
  })

  it('comparable:false 的 peer 顯示「無法比較：{reason}」，不留白補 0', () => {
    const peers: PeerComparisonEntry[] = [
      {
        asset_id: 'asset:matic',
        snapshot: makeSnapshot({ asset_id: 'asset:matic', observed_tps: metric(null) }),
        comparable: false,
        reason: 'observed_tps missing',
      },
    ]
    render(<PeerComparisonTable snapshot={makeSnapshot()} peers={peers} />)
    expect(screen.getAllByText(/無法比較：observed_tps missing/).length).toBeGreaterThan(0)
  })

  it('peer 的 snapshot 為 null（宣告在 peer_group 但查無資料）時仍列出該 peer，顯示「無法比較：snapshot missing」，不靜默丟棄', () => {
    const peers: PeerComparisonEntry[] = [
      { asset_id: 'asset:ghost', snapshot: null, comparable: false, reason: 'snapshot missing' },
    ]
    render(<PeerComparisonTable snapshot={makeSnapshot()} peers={peers} />)
    expect(screen.getAllByText(/asset:ghost/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/無法比較：snapshot missing/).length).toBeGreaterThan(0)
  })

  it('缺值欄位（value: null）誠實顯示「—」，不補 0', () => {
    const snapshot = makeSnapshot({ observed_tps: metric(null) })
    render(<PeerComparisonTable snapshot={snapshot} peers={[]} />)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('peers 為空陣列時顯示空狀態文案', () => {
    render(<PeerComparisonTable snapshot={makeSnapshot()} peers={[]} />)
    expect(screen.getByText('目前無同層 peer 可比較。')).toBeInTheDocument()
  })

  it('顯示「示範資料」illustrative 揭露徽章，不讓評審誤把 fixture 當真實觀測', () => {
    render(<PeerComparisonTable snapshot={makeSnapshot()} peers={[]} />)
    expect(screen.getByText(/示範資料/)).toBeInTheDocument()
  })
})
