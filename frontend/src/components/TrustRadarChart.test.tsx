import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TrustRadarChart from './TrustRadarChart'
import type { TrustRadar, TrustRadarDimension } from '../lib/types'

function makeDim(overrides: Partial<TrustRadarDimension> = {}): TrustRadarDimension {
  return {
    label: '價格',
    has_data: true,
    trust: 0.7,
    n_sources: 2,
    n_evidence: 5,
    single_source: false,
    ...overrides,
  }
}

describe('TrustRadarChart', () => {
  it('多維度有資料時應正常渲染且包含 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', trust: 0.8 }),
      onchain: makeDim({ label: '鏈上', trust: 0.5 }),
      news: makeDim({ label: '新聞', trust: 0.3 }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(container.querySelector('.recharts-polar-radius-axis')).not.toBeNull()
  })

  it('所有維度皆無資料時應顯示 fallback 文字且不渲染 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', has_data: false, trust: null }),
      onchain: makeDim({ label: '鏈上', has_data: false, trust: null }),
      news: makeDim({ label: '新聞', has_data: false, trust: null }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(
      screen.getByText('目前尚無任何維度累積足夠資料，暫無法繪製雷達圖。')
    ).toBeDefined()
    expect(container.querySelector('.recharts-polar-radius-axis')).toBeNull()
  })

  it('單一維度有資料時應正常渲染且包含 PolarRadiusAxis', () => {
    const radar: TrustRadar = {
      price: makeDim({ label: '價格', trust: 0.8 }),
    }

    const { container } = render(<TrustRadarChart radar={radar} />)

    expect(container.querySelector('.recharts-polar-radius-axis')).not.toBeNull()
  })
})
